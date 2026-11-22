import hashlib
import json
import logging
import mimetypes
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from sqlite3 import Row
from threading import Event
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .database import Database

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 5


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def metadata_bytes(snapshot: str, digest: str | None) -> bytes:
    document = json.loads(snapshot)
    document["sha256"] = digest
    return json.dumps(document, ensure_ascii=False).encode("utf-8")


def storage_client():
    access = os.environ.get("S3_ACCESS_KEY")
    secret = os.environ.get("S3_SECRET_KEY")
    if not access or not secret:
        return None
    endpoint = os.environ.get("S3_ENDPOINT", "http://localhost:9000")
    parsed = urlsplit(endpoint)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.query or parsed.fragment):
        raise ValueError("Set S3_ENDPOINT to an HTTP or HTTPS URL without credentials.")
    return boto3.client(
        "s3", endpoint_url=endpoint,
        aws_access_key_id=access, aws_secret_access_key=secret,
        region_name=os.environ.get("S3_REGION", "us-east-1"),
        config=Config(
            connect_timeout=2, read_timeout=5,
            retries={"total_max_attempts": 1},
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def storage_error(error: Exception) -> str:
    if isinstance(error, ClientError):
        code = error.response.get("Error", {}).get("Code")
        if code in {"403", "401", "AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch"}:
            return "Access denied. Check the S3 credentials and Raw bucket permissions."
        if code in {"404", "NoSuchBucket"}:
            return "Raw bucket missing. Check S3_BUCKET_RAW and the SILO endpoint."
        return "SILO rejected the request. Check the bucket and server logs."
    return "SILO connection failed. Check Docker, the S3 endpoint, and network access."


@contextmanager
def worker_lock(path: Path):
    # An OS lock is released on process exit, including a crash. Keep the lock file in place.
    with path.open("a+b") as handle:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            def lock():
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            def lock():
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            lock()
        except (BlockingIOError, PermissionError):
            yield False
        except OSError as error:
            # Windows reports an occupied byte-range lock as EACCES or EDEADLK.
            if os.name != "nt" or error.errno not in {13, 36}:
                raise
            yield False
        else:
            yield True


class DataLake:
    def __init__(self, database: Database, root: Path, client=None, bucket: str | None = None):
        self.database = database
        self.root = root
        self.client = client
        self.bucket = bucket if bucket is not None else os.environ.get("S3_BUCKET_RAW", "efootball-raw")
        self.connection_status = "Checking SILO"
        self.stopping = Event()
        self.next_probe = 0.0

    def summary(self) -> Row:
        with self.database.connect() as connection:
            return connection.execute("""
                SELECT count(CASE WHEN sync_status IN ('pending', 'uploading') THEN 1 END) AS pending,
                       count(CASE WHEN sync_status = 'synced' AND deleted_at IS NULL THEN 1 END) AS synced,
                       count(CASE WHEN sync_status = 'failed' THEN 1 END) AS failed,
                       max(synced_at) AS last_success,
                       (SELECT last_sync_error FROM capture_sync WHERE sync_status = 'failed'
                        ORDER BY last_sync_attempt DESC LIMIT 1) AS last_error
                FROM capture_sync
            """).fetchone()

    def run(self, *, manual: bool = False, automatic: bool = True) -> None:
        try:
            with worker_lock(self.database.path.with_suffix(".sync.lock")) as acquired:
                if acquired:
                    self._run(manual, automatic)
                else:
                    self.connection_status = "Sync running in another window"
        except Exception as error:
            # Only the exception type is safe to log for configuration and SDK errors.
            logger.error("Data Lake worker failed (%s)", type(error).__name__)
            self.connection_status = "Data Lake sync error. See app.log."

    def _run(self, manual: bool, automatic: bool) -> None:
        with self.database.connect() as connection:
            connection.execute("""
                UPDATE capture_sync SET sync_status = 'pending', retry_at = 0
                WHERE sync_status = 'uploading'
            """)
            if manual:
                connection.execute("""
                    UPDATE capture_sync SET sync_attempts = 0, retry_at = 0
                    WHERE sync_status = 'failed'
                """)
        batch = self._eligible() if manual or automatic else []
        if not batch and not manual and time.monotonic() < self.next_probe:
            return
        self.next_probe = time.monotonic() + 30
        if self.client is None:
            try:
                self.client = storage_client()
            except (ValueError, BotoCoreError):
                self.connection_status = "Invalid S3 configuration. Check S3_ENDPOINT and S3_REGION."
                return
        if self.client is None:
            self.connection_status = "Set S3_ACCESS_KEY and S3_SECRET_KEY to connect"
            return
        error = None
        try:
            self.client.head_bucket(Bucket=self.bucket)
            self.connection_status = "SILO connected"
        except (BotoCoreError, ClientError) as exc:
            error = storage_error(exc)
            self.connection_status = error

        if error is None and (manual or automatic):
            with self.database.connect() as connection:
                reset = connection.execute("""
                    UPDATE capture_sync SET sync_attempts = 0, retry_at = 0, blocked_on_probe = 0
                    WHERE sync_status = 'failed' AND blocked_on_probe = 1
                """).rowcount
            if reset:
                batch = self._eligible()

        for candidate in batch:
            if self.stopping.is_set():
                break
            with self.database.connect() as connection:
                row = connection.execute("""
                    UPDATE capture_sync SET sync_status = 'uploading',
                        sync_attempts = sync_attempts + 1, last_sync_attempt = ?
                    WHERE capture_id = ? AND revision = ? RETURNING *
                """, (now(), candidate["capture_id"], candidate["revision"])).fetchone()
            if row is not None:
                self._attempt(row, error)

    def _eligible(self) -> list[Row]:
        with self.database.connect() as connection:
            return connection.execute("""
                SELECT capture_id, revision FROM capture_sync
                WHERE sync_status = 'pending' OR (
                    sync_status = 'failed' AND sync_attempts < ? AND retry_at <= ?
                )
                ORDER BY deleted_at IS NULL, retry_at, capture_id
            """, (MAX_ATTEMPTS, time.time())).fetchall()

    def _attempt(self, row: Row, error: str | None) -> None:
        blocked_on_probe = error is not None
        try:
            complete = error is None and self._upload(row)
        except (BotoCoreError, ClientError) as exc:
            complete = False
            error = storage_error(exc)
            self.connection_status = error
        except OSError:
            complete = False
            error = "Local screenshot is missing or unreadable. Restore it and retry."
        except ValueError:
            complete = False
            error = "Local screenshot changed during synchronization. Restore the original and retry."
        with self.database.connect() as connection:
            if complete:
                connection.execute("""
                    UPDATE capture_sync SET sync_status = 'synced', synced_at = ?,
                        last_sync_error = NULL WHERE capture_id = ? AND revision = ?
                """, (now(), row["capture_id"], row["revision"]))
            elif error is not None:
                delay = 5 * 2 ** min(row["sync_attempts"] - 1, MAX_ATTEMPTS - 1)
                connection.execute("""
                    UPDATE capture_sync SET sync_status = 'failed', last_sync_error = ?, retry_at = ?,
                        blocked_on_probe = ? WHERE capture_id = ? AND revision = ?
                """, (error, time.time() + delay, blocked_on_probe, row["capture_id"], row["revision"]))

    def _upload(self, row: Row) -> bool:
        capture_id = row["capture_id"]
        digest = row["sha256"]
        if not row["deleted_at"] and not row["image_uploaded"]:
            image_path = self.root / row["local_path"]
            image = image_path.read_bytes()
            checksum = hashlib.sha256(image).hexdigest()
            if digest is not None and digest != checksum:
                raise ValueError("Screenshot checksum changed.")
            digest = checksum
            with self.database.connect() as connection:
                connection.execute("UPDATE capture_sync SET sha256 = ? WHERE capture_id = ?",
                                   (digest, capture_id))
            if not self._current(row):
                return False
            content_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
            self._put(row["image_object_key"], image, content_type, immutable=True)
            with self.database.connect() as connection:
                connection.execute("UPDATE capture_sync SET image_uploaded = 1 WHERE capture_id = ?",
                                   (capture_id,))

        key = row["metadata_object_key"]
        with self.database.connect() as connection:
            revisions = connection.execute("""
                SELECT revision, metadata FROM capture_sync_revisions
                WHERE capture_id = ? AND revision BETWEEN ? AND ? ORDER BY revision
            """, (capture_id, min(row["archived_revision"] + 1, row["revision"]),
                  row["revision"])).fetchall()

        if row["deleted_at"]:
            tombstone = f"tombstones/{key.removeprefix('metadata/')}"
            self._put(tombstone, metadata_bytes(revisions[-1]["metadata"], digest),
                      "application/json", immutable=True)
        for revision, metadata in revisions:
            if not self._current(row):
                return False
            body = metadata_bytes(metadata, digest)
            if revision > row["archived_revision"]:
                archive = f"metadata-revisions/{key.removeprefix('metadata/')[:-5]}/revision={revision}.json"
                self._put(archive, body, "application/json", immutable=True)
                with self.database.connect() as connection:
                    connection.execute("""
                        UPDATE capture_sync SET archived_revision = ? WHERE capture_id = ?
                    """, (revision, capture_id))
        if not self._current(row):
            return False
        self._put(key, metadata_bytes(revisions[-1]["metadata"], digest), "application/json")
        return True

    def _current(self, row: Row) -> bool:
        if self.stopping.is_set():
            return False
        with self.database.connect() as connection:
            revision = connection.execute(
                "SELECT revision FROM capture_sync WHERE capture_id = ?", (row["capture_id"],)
            ).fetchone()[0]
            return revision == row["revision"]

    def _put(self, key: str, body: bytes, content_type: str, *, immutable: bool = False) -> None:
        options = {"IfNoneMatch": "*"} if immutable else {}
        try:
            self.client.put_object(
                Bucket=self.bucket, Key=key, Body=body, ContentType=content_type, **options
            )
        except ClientError as exc:
            if not immutable or exc.response.get("Error", {}).get("Code") not in {
                "412", "PreconditionFailed"
            }:
                raise
