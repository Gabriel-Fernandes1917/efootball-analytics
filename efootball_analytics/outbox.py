import json
from datetime import datetime, timezone
from pathlib import Path
from sqlite3 import Connection


SCHEMA = (
    """CREATE TABLE IF NOT EXISTS capture_sync (
        capture_id TEXT PRIMARY KEY,
        revision INTEGER NOT NULL DEFAULT 0,
        archived_revision INTEGER NOT NULL DEFAULT 0,
        sync_status TEXT NOT NULL DEFAULT 'pending'
            CHECK (sync_status IN ('pending', 'uploading', 'synced', 'failed')),
        sync_attempts INTEGER NOT NULL DEFAULT 0,
        last_sync_attempt TEXT,
        synced_at TEXT,
        last_sync_error TEXT,
        retry_at REAL NOT NULL DEFAULT 0,
        blocked_on_probe INTEGER NOT NULL DEFAULT 0,
        local_path TEXT NOT NULL,
        image_object_key TEXT NOT NULL,
        metadata_object_key TEXT NOT NULL,
        sha256 TEXT,
        image_uploaded INTEGER NOT NULL DEFAULT 0,
        deleted_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS capture_sync_revisions (
        capture_id TEXT NOT NULL REFERENCES capture_sync(capture_id),
        revision INTEGER NOT NULL,
        metadata TEXT NOT NULL,
        PRIMARY KEY (capture_id, revision)
    )""",
)


def enqueue(connection: Connection, capture_id: str, *, deleted: bool = False) -> None:
    capture = connection.execute("""
        SELECT c.*, m.team, m.opponent FROM captures c
        JOIN matches m ON m.id = c.match_id WHERE c.id = ?
    """, (capture_id,)).fetchone()
    if capture is None:
        return
    stem = f"match_id={capture['match_id']}/capture_id={capture_id}"
    image_key = f"screenshots/{stem}{Path(capture['path']).suffix}"
    deleted_at = datetime.now(timezone.utc).isoformat() if deleted else None
    revision = connection.execute("""
        INSERT INTO capture_sync (
            capture_id, revision, local_path, image_object_key, metadata_object_key, deleted_at
        ) VALUES (?, 1, ?, ?, ?, ?)
        ON CONFLICT(capture_id) DO UPDATE SET
            revision = revision + 1, sync_status = 'pending', sync_attempts = 0,
            last_sync_error = NULL, retry_at = 0, blocked_on_probe = 0,
            deleted_at = excluded.deleted_at
        RETURNING revision
    """, (capture_id, capture["path"], image_key, f"metadata/{stem}.json", deleted_at)).fetchone()[0]
    minute = capture["minute"]
    if minute is not None and minute.isascii() and minute.isdecimal():
        minute = int(minute)
    metadata = {
        "schema_version": 1, "revision": revision,
        "capture_id": capture_id, "match_id": capture["match_id"],
        "team": capture["team"], "opponent": capture["opponent"],
        "event_type": capture["event"], "match_minute": minute,
        "substitution_reason": capture["substitution_reason"],
        "substitution_notes": capture["substitution_notes"],
        "captured_at": capture["captured_at"], "image_object_key": image_key,
        "deleted_at": deleted_at,
    }
    connection.execute("INSERT INTO capture_sync_revisions VALUES (?, ?, ?)",
                       (capture_id, revision, json.dumps(metadata, ensure_ascii=False)))

