from contextlib import contextmanager
from pathlib import Path

import pytest

from efootball_analytics.database import Database
from efootball_analytics.models import Event
from efootball_analytics.sessions import Sessions, normalize_minute


class FakeWriter:
    def save(self, destination: Path) -> None:
        destination.write_bytes(b"fake screenshot")


@pytest.fixture
def sessions(tmp_path):
    return Sessions(Database(tmp_path / "matches.sqlite3"), tmp_path, FakeWriter())


def test_match_lifecycle_and_capture_history(sessions):
    with pytest.raises(ValueError, match="both"):
        sessions.start("", "Team")
    with pytest.raises(ValueError, match="Start a match"):
        sessions.capture(Event.GOAL)

    match = sessions.start(" Opponent ", " Team ")
    with pytest.raises(ValueError, match="active match"):
        sessions.start("Other", "Team")
    first = sessions.capture(Event.FORMATION)
    second = sessions.capture(Event.GOAL, "090+03")
    assert second.path.parts[:3] == ("screenshots", match.id, "goal")
    assert (sessions.root / second.path).read_bytes() == b"fake screenshot"
    assert second.minute == "90+3"
    reopened = Database(sessions.database.path)
    assert reopened.matches() == [match]
    assert reopened.captures(match.id) == [second, first]

    sessions.finish()
    assert sessions.active() is None
    assert reopened.matches()[0].finished_at is not None
    assert reopened.matches()[0].opponent == "Opponent"
    sessions.correct_minute(second, "91")
    assert reopened.captures(match.id)[0].minute == "91"
    sessions.correct_minute(second, "")
    assert reopened.captures(match.id)[0].minute is None
    sessions.delete(second)
    assert reopened.captures(match.id) == [first]
    assert not (sessions.root / second.path).exists()
    assert sessions.start("Next opponent", "Team").id != match.id


def test_capture_write_failure_removes_partial_file(sessions, monkeypatch):
    match = sessions.start("Opponent", "Team")

    def fail(destination: Path) -> None:
        destination.write_bytes(b"partial")
        raise OSError("capture failed")

    monkeypatch.setattr(sessions.writer, "save", fail)
    with pytest.raises(OSError, match="capture failed"):
        sessions.capture(Event.GOAL)
    assert sessions.database.captures(match.id) == []
    assert list(sessions.root.rglob("*.png")) == []


def test_failed_metadata_insert_removes_screenshot(sessions, monkeypatch):
    match = sessions.start("Opponent", "Team")
    original = sessions.database.add_capture

    def fail_after_finish(capture):
        sessions.finish()
        original(capture)

    monkeypatch.setattr(sessions.database, "add_capture", fail_after_finish)
    with pytest.raises(ValueError, match="Start a match"):
        sessions.capture(Event.GOAL)
    assert sessions.database.captures(match.id) == []
    assert list(sessions.root.rglob("*.png")) == []


def test_failed_metadata_delete_restores_screenshot(sessions, monkeypatch):
    match = sessions.start("Opponent", "Team")
    capture = sessions.capture(Event.GOAL)

    original = sessions.database.deleting_capture

    @contextmanager
    def fail(capture_id: str):
        with original(capture_id) as deleted:
            yield deleted
            raise OSError("database unavailable")

    monkeypatch.setattr(sessions.database, "deleting_capture", fail)
    with pytest.raises(OSError, match="database unavailable"):
        sessions.delete(capture)
    assert sessions.database.captures(match.id) == [capture]
    assert (sessions.root / capture.path).is_file()


@pytest.mark.parametrize("value", ["-1", "121", "90+0", "90.5", "90+three"])
def test_invalid_minutes(value):
    with pytest.raises(ValueError):
        normalize_minute(value)
