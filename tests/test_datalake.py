import hashlib
import json
from threading import Event as ThreadEvent, Thread

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, ReadTimeoutError

from efootball_analytics.database import Database
from efootball_analytics.datalake import DataLake, MAX_ATTEMPTS
from efootball_analytics.models import Event, SubstitutionReason
from efootball_analytics.sessions import Sessions


def rejected(code):
    return ClientError({"Error": {"Code": code, "Message": "sensitive detail"}}, "S3")


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.calls = []
        self.probe_error = None
        self.before_put = lambda key: None

    def head_bucket(self, *, Bucket):
        assert Bucket == "efootball-raw"
        if self.probe_error:
            raise self.probe_error

    def put_object(self, *, Bucket, Key, Body, ContentType, IfNoneMatch=None):
        assert Bucket == "efootball-raw"
        self.calls.append(Key)
        self.before_put(Key)
        if IfNoneMatch == "*" and Key in self.objects:
            raise rejected("PreconditionFailed")
        self.objects[Key] = Body
        assert ContentType == ("image/png" if Key.startswith("screenshots/") else "application/json")


class Writer:
    def save(self, destination):
        destination.write_bytes(b"original screenshot")


@pytest.fixture
def setup(tmp_path):
    database = Database(tmp_path / "matches.sqlite3")
    sessions = Sessions(database, tmp_path, Writer())
    sessions.start("Opponent", "Team")
    storage = FakeS3()
    lake = DataLake(database, tmp_path, storage, "efootball-raw")
    return sessions, storage, lake


def queue_row(sessions):
    with sessions.database.connect() as connection:
        return dict(connection.execute("SELECT * FROM capture_sync").fetchone())


def test_upload_and_repeated_sync(setup):
    sessions, storage, lake = setup
    capture = sessions.capture(Event.GOAL, "73")
    row = queue_row(sessions)
    assert row["sync_status"] == "pending"
    lake.run()
    assert queue_row(sessions)["sync_status"] == "synced"
    assert storage.objects[row["image_object_key"]] == b"original screenshot"
    metadata = json.loads(storage.objects[row["metadata_object_key"]])
    assert metadata == {
        "schema_version": 1, "revision": 1, "capture_id": capture.id,
        "match_id": capture.match_id, "team": "Team", "opponent": "Opponent",
        "event_type": "goal", "match_minute": 73, "substitution_reason": None,
        "substitution_notes": None, "deleted_at": None, "captured_at": capture.captured_at,
        "image_object_key": row["image_object_key"],
        "sha256": hashlib.sha256(b"original screenshot").hexdigest(),
    }
    first_calls = storage.calls.copy()
    lake.run(manual=True)
    assert storage.calls == first_calls
    assert lake.summary()["last_success"]


@pytest.mark.parametrize(("error", "message"), [
    (EndpointConnectionError(endpoint_url="http://localhost:9000"), "connection failed"),
    (rejected("InvalidAccessKeyId"), "Access denied"),
    (rejected("404"), "bucket missing"),
])
def test_unavailable_storage_keeps_capture_and_manual_retry(setup, error, message):
    sessions, storage, lake = setup
    capture = sessions.capture(Event.GOAL)
    storage.probe_error = error
    lake.run()
    row = queue_row(sessions)
    assert row["sync_status"] == "failed"
    assert message in row["last_sync_error"]
    assert "sensitive detail" not in row["last_sync_error"]
    assert (sessions.root / capture.path).is_file()
    storage.probe_error = None
    lake.run()
    assert queue_row(sessions)["sync_attempts"] == 1
    lake.run(manual=True, automatic=False)
    assert queue_row(sessions)["sync_status"] == "synced"


def test_partial_upload_and_restart(setup):
    sessions, storage, lake = setup
    capture = sessions.capture(Event.FORMATION)
    sessions.finish()
    row = queue_row(sessions)

    def timeout(key):
        if key == row["metadata_object_key"]:
            raise ReadTimeoutError(endpoint_url="http://localhost:9000")

    storage.before_put = timeout
    lake.run()
    assert queue_row(sessions)["sync_status"] == "failed"
    assert row["metadata_object_key"] not in storage.objects
    with sessions.database.connect() as connection:
        connection.execute("UPDATE capture_sync SET sync_status = 'uploading'")
    storage.before_put = lambda key: None
    reopened = Database(sessions.database.path)
    DataLake(reopened, sessions.root, storage, "efootball-raw").run()
    assert queue_row(sessions)["sync_status"] == "synced"
    assert storage.calls.count(row["image_object_key"]) == 1
    assert json.loads(storage.objects[row["metadata_object_key"]])["capture_id"] == capture.id


def test_historical_capture_backfill_and_pending_restart(setup):
    sessions, storage, _ = setup
    capture = sessions.capture(Event.NEAR_MISS, "90+3")
    sessions.finish()
    with sessions.database.connect() as connection:
        connection.execute("DROP TABLE capture_sync_revisions")
        connection.execute("DROP TABLE capture_sync")
    Database(sessions.database.path)
    reopened = Database(sessions.database.path)
    assert queue_row(sessions)["revision"] == 1
    assert queue_row(sessions)["sync_status"] == "pending"
    DataLake(reopened, sessions.root, storage, "efootball-raw").run()
    row = queue_row(sessions)
    assert row["sync_status"] == "synced"
    assert storage.objects[row["image_object_key"]] == (sessions.root / capture.path).read_bytes()
    assert json.loads(storage.objects[row["metadata_object_key"]])["match_minute"] == "90+3"


def test_metadata_edits_preserve_every_revision(setup):
    sessions, storage, lake = setup
    capture = sessions.capture(Event.SUBSTITUTION, "72")
    lake.run()
    sessions.correct_minute(capture, "73")
    sessions.classify_substitution(capture, SubstitutionReason.PHYSICAL, "Fatigue")
    lake.run()
    row = queue_row(sessions)
    current = json.loads(storage.objects[row["metadata_object_key"]])
    assert (current["match_minute"], current["substitution_reason"], current["substitution_notes"]) == (
        73, "physical", "Fatigue"
    )
    history = [json.loads(value) for key, value in storage.objects.items()
               if key.startswith("metadata-revisions/")]
    assert [data["match_minute"] for data in history] == [72, 73, 73]
    assert history[1]["substitution_reason"] is None
    assert storage.calls.count(row["image_object_key"]) == 1


@pytest.mark.parametrize("already_uploaded", [False, True])
def test_deletion_publishes_tombstone_and_keeps_any_uploaded_image(setup, already_uploaded):
    sessions, storage, lake = setup
    capture = sessions.capture(Event.FINAL_RESULT)
    if already_uploaded:
        lake.run()
    sessions.delete(capture)
    lake.run()
    row = queue_row(sessions)
    assert row["sync_status"] == "synced"
    assert not (sessions.root / capture.path).exists()
    assert sessions.database.captures(capture.match_id) == []
    assert (row["image_object_key"] in storage.objects) is already_uploaded
    tombstone = row["metadata_object_key"].replace("metadata/", "tombstones/", 1)
    assert storage.objects[tombstone] == storage.objects[row["metadata_object_key"]]
    assert json.loads(storage.objects[tombstone])["deleted_at"]


@pytest.mark.parametrize("delete", [False, True])
def test_edit_or_delete_during_upload_supersedes_stale_metadata(setup, delete):
    sessions, storage, lake = setup
    capture = sessions.capture(Event.GOAL, "12")
    row = queue_row(sessions)

    def change(key):
        if key == row["image_object_key"]:
            if delete:
                sessions.delete(capture)
            else:
                sessions.correct_minute(capture, "13")

    storage.before_put = change
    lake.run()
    assert row["metadata_object_key"] not in storage.objects
    assert queue_row(sessions)["sync_status"] == "pending"
    lake.run()
    current = json.loads(storage.objects[row["metadata_object_key"]])
    assert current["revision"] == 2
    assert bool(current["deleted_at"]) is delete
    assert current["match_minute"] == (12 if delete else 13)


def test_ambiguous_image_write_is_safe_to_retry(setup):
    sessions, storage, lake = setup
    sessions.capture(Event.GOAL)
    key = queue_row(sessions)["image_object_key"]

    def interrupt(object_key):
        if object_key == key:
            storage.objects[key] = b"original screenshot"
            raise ReadTimeoutError(endpoint_url="http://localhost:9000")

    storage.before_put = interrupt
    lake.run()
    storage.before_put = lambda key: None
    lake.run(manual=True)
    assert queue_row(sessions)["sync_status"] == "synced"
    assert storage.calls.count(key) == 2
    assert len(storage.objects) == 3


def test_missing_local_image_and_bounded_retries(setup):
    sessions, storage, lake = setup
    capture = sessions.capture(Event.GOAL)
    (sessions.root / capture.path).unlink()
    for _ in range(MAX_ATTEMPTS + 1):
        lake.run()
        with sessions.database.connect() as connection:
            connection.execute("UPDATE capture_sync SET retry_at = 0")
    row = queue_row(sessions)
    assert row["sync_attempts"] == MAX_ATTEMPTS
    assert "missing" in row["last_sync_error"]
    assert not storage.objects


def test_concurrent_worker_does_not_duplicate_uploads(setup):
    sessions, storage, lake = setup
    sessions.capture(Event.GOAL)
    entered, release = ThreadEvent(), ThreadEvent()

    def wait(key):
        entered.set()
        assert release.wait(5)

    storage.before_put = wait
    worker = Thread(target=lake.run)
    worker.start()
    try:
        assert entered.wait(5)
        other = DataLake(sessions.database, sessions.root, storage, "efootball-raw")
        other.run(manual=True)
        assert "another window" in other.connection_status
        assert len(storage.calls) == 1
    finally:
        release.set()
        worker.join(5)
    assert queue_row(sessions)["sync_status"] == "synced"


def test_automatic_recovery_after_long_outage(setup):
    sessions, storage, lake = setup
    sessions.capture(Event.GOAL)
    storage.probe_error = EndpointConnectionError(endpoint_url="http://localhost:9000")
    for _ in range(MAX_ATTEMPTS):
        lake.run()
        with sessions.database.connect() as connection:
            connection.execute("UPDATE capture_sync SET retry_at = 0")
    assert queue_row(sessions)["sync_attempts"] == MAX_ATTEMPTS
    storage.probe_error = None
    lake.next_probe = 0
    lake.run()
    assert queue_row(sessions)["sync_status"] == "synced"


def test_gui_remains_responsive_while_sync_is_blocked(setup, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication

    from efootball_analytics.config import Config
    from efootball_analytics.hotkeys import Hotkeys
    from efootball_analytics.ui import Window

    monkeypatch.setattr(Hotkeys, "apply", lambda self, settings: None)
    monkeypatch.setattr(Hotkeys, "stop", lambda self: None)
    app = QApplication.instance() or QApplication([])
    sessions, storage, lake = setup
    sessions.capture(Event.GOAL)
    entered, release = ThreadEvent(), ThreadEvent()

    def wait(key):
        entered.set()
        assert release.wait(5)

    storage.before_put = wait
    window = Window(sessions, Config(sessions.root / "settings.json"), lake)
    window.show()
    try:
        assert entered.wait(3)
        loop = QEventLoop()
        observed = []

        def interact():
            observed.append(not release.is_set())
            window.event_buttons[Event.GOAL].click()
            loop.quit()

        QTimer.singleShot(0, interact)
        QTimer.singleShot(3000, loop.quit)
        loop.exec()
        assert observed == [True]
        assert len(sessions.database.captures(sessions.active().id)) == 2
    finally:
        release.set()
        window.sync_thread.join(5)
        window.close()
        app.processEvents()


def test_deletion_during_canonical_write_remains_queued(setup):
    sessions, storage, lake = setup
    capture = sessions.capture(Event.GOAL)
    key = queue_row(sessions)["metadata_object_key"]

    def delete(key_being_written):
        if key_being_written == key:
            sessions.delete(capture)

    storage.before_put = delete
    lake.run()
    assert queue_row(sessions)["sync_status"] == "pending"
    storage.before_put = lambda key: None
    lake.run()
    tombstone = key.replace("metadata/", "tombstones/", 1)
    assert json.loads(storage.objects[tombstone])["deleted_at"]
    assert storage.objects[tombstone] == storage.objects[key]
    assert queue_row(sessions)["sync_status"] == "synced"


def test_boto3_configuration_and_conditional_write(setup, monkeypatch):
    from botocore.stub import Stubber

    from efootball_analytics.datalake import storage_client

    monkeypatch.setenv("S3_ENDPOINT", "http://localhost:9000")
    monkeypatch.setenv("S3_ACCESS_KEY", "mock-access")
    monkeypatch.setenv("S3_SECRET_KEY", "mock-secret")
    monkeypatch.setenv("S3_REGION", "us-east-1")
    client = storage_client()
    assert client.meta.endpoint_url == "http://localhost:9000"
    assert client.meta.config.s3["addressing_style"] == "path"
    sessions, _, _ = setup
    lake = DataLake(sessions.database, sessions.root, client, "efootball-raw")
    with Stubber(client) as stub:
        stub.add_response("head_bucket", {}, {"Bucket": "efootball-raw"})
        lake.run(automatic=False)
        assert lake.connection_status == "SILO connected"
        stub.add_response("put_object", {}, {
            "Bucket": "efootball-raw", "Key": "screenshots/mock.png", "Body": b"mock",
            "ContentType": "image/png", "IfNoneMatch": "*",
        })
        lake._put("screenshots/mock.png", b"mock", "image/png", immutable=True)
        stub.assert_no_pending_responses()
