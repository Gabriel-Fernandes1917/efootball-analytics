import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from efootball_analytics.database import Database
from efootball_analytics.models import Event, SubstitutionReason
from efootball_analytics.sessions import Sessions


class FakeWriter:
    def save(self, destination: Path) -> None:
        destination.write_bytes(b"screenshot")


def test_legacy_database_migrates_in_place(tmp_path: Path) -> None:
    database_path = tmp_path / "matches.sqlite3"
    photo = tmp_path / "screenshots/match/substitution/old.png"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"original screenshot")
    with closing(sqlite3.connect(database_path)) as connection:
        connection.executescript("""
            CREATE TABLE matches (
                id TEXT PRIMARY KEY, opponent TEXT NOT NULL, team TEXT NOT NULL,
                started_at TEXT NOT NULL, finished_at TEXT
            );
            CREATE TABLE captures (
                id TEXT PRIMARY KEY, match_id TEXT NOT NULL REFERENCES matches(id),
                event TEXT NOT NULL, captured_at TEXT NOT NULL, path TEXT NOT NULL UNIQUE, minute TEXT
            );
            INSERT INTO matches VALUES ('match', 'Opponent', 'Team', 'start', 'finish');
            INSERT INTO captures VALUES (
                'old', 'match', 'substitution', 'timestamp',
                'screenshots/match/substitution/old.png', '60'
            );
        """)
    database = Database(database_path)
    existing = database.captures("match")[0]
    assert database.matches()[0].finished_at == "finish"
    assert (existing.id, existing.event, existing.captured_at, existing.minute) == (
        "old", Event.SUBSTITUTION, "timestamp", "60"
    )
    assert (tmp_path / existing.path).read_bytes() == b"original screenshot"
    assert existing.substitution_reason is None and existing.substitution_notes is None
    database.update_substitution(existing.id, SubstitutionReason.PHYSICAL, "Low stamina")
    reloaded = Database(database_path).captures("match")[0]
    assert reloaded.substitution_reason == SubstitutionReason.PHYSICAL
    assert reloaded.substitution_notes == "Low stamina"
    assert (tmp_path / reloaded.path).read_bytes() == b"original screenshot"


def test_classification_edits_and_other_events(tmp_path: Path) -> None:
    database = Database(tmp_path / "matches.sqlite3")
    sessions = Sessions(database, tmp_path, FakeWriter())
    match = sessions.start("Opponent", "Team")
    substitution = sessions.capture(Event.SUBSTITUTION, "75")
    goal = sessions.capture(Event.GOAL, "76")
    sessions.classify_substitution(substitution, SubstitutionReason.TACTICAL, "  Change formation ")
    assert database.captures(match.id)[1].substitution_notes == "Change formation"
    sessions.finish()
    sessions.correct_minute(substitution, "74")
    sessions.classify_substitution(substitution, SubstitutionReason.PERFORMANCE, " ")
    with pytest.raises(ValueError, match="existing substitution"):
        sessions.classify_substitution(goal, SubstitutionReason.OTHER)
    with pytest.raises(sqlite3.IntegrityError):
        with database.connect() as connection:
            connection.execute(
                "UPDATE captures SET substitution_reason = 'invalid' WHERE id = ?",
                (substitution.id,),
            )
    saved_goal, saved_substitution = database.captures(match.id)
    assert saved_goal == goal
    assert saved_substitution.substitution_reason == SubstitutionReason.PERFORMANCE
    assert saved_substitution.substitution_notes is None
    assert saved_substitution.minute == "74" and saved_substitution.path == substitution.path
