import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from .models import Capture, Event, Match, SubstitutionReason


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS matches (
                    id TEXT PRIMARY KEY,
                    opponent TEXT NOT NULL,
                    team TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_match
                    ON matches ((1)) WHERE finished_at IS NULL;
                CREATE TABLE IF NOT EXISTS captures (
                    id TEXT PRIMARY KEY,
                    match_id TEXT NOT NULL REFERENCES matches(id),
                    event TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    path TEXT NOT NULL UNIQUE,
                    minute TEXT
                );
                CREATE INDEX IF NOT EXISTS captures_by_match
                    ON captures (match_id, captured_at);
            """)
            connection.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(captures)")}
            if "substitution_reason" not in columns:
                connection.execute("""
                    ALTER TABLE captures ADD COLUMN substitution_reason TEXT
                    CHECK (substitution_reason IS NULL OR (
                        event = 'substitution'
                        AND substitution_reason IN ('physical', 'tactical', 'performance', 'other')
                    ))
                """)
            if "substitution_notes" not in columns:
                connection.execute("""
                    ALTER TABLE captures ADD COLUMN substitution_notes TEXT
                    CHECK (substitution_notes IS NULL OR substitution_reason IS NOT NULL)
                """)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection

    def matches(self) -> list[Match]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM matches ORDER BY started_at DESC")
            return [Match(**dict(row)) for row in rows]

    def active_match(self) -> Match | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM matches WHERE finished_at IS NULL").fetchone()
            return Match(**dict(row)) if row is not None else None

    def start(self, match: Match) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO matches VALUES (?, ?, ?, ?, NULL)",
                (match.id, match.opponent, match.team, match.started_at),
            )

    def finish(self, match_id: str, finished_at: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE matches SET finished_at = ? WHERE id = ? AND finished_at IS NULL",
                (finished_at, match_id),
            )

    def add_capture(self, capture: Capture) -> None:
        with self.connect() as connection:
            result = connection.execute("""
                INSERT INTO captures (
                    id, match_id, event, captured_at, path, minute,
                    substitution_reason, substitution_notes
                )
                SELECT ?, id, ?, ?, ?, ?, ?, ? FROM matches
                WHERE id = ? AND finished_at IS NULL
            """, (capture.id, capture.event.value, capture.captured_at,
                  capture.path.as_posix(), capture.minute,
                  capture.substitution_reason.value if capture.substitution_reason else None,
                  capture.substitution_notes, capture.match_id))
            if result.rowcount != 1:
                raise ValueError("Start a match before capturing.")

    def captures(self, match_id: str) -> list[Capture]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM captures WHERE match_id = ? ORDER BY captured_at DESC, id",
                (match_id,),
            )
            return [
                Capture(
                    row["id"], row["match_id"], Event(row["event"]),
                    row["captured_at"], Path(row["path"]), row["minute"],
                    SubstitutionReason(reason) if (reason := row["substitution_reason"]) else None,
                    row["substitution_notes"],
                )
                for row in rows
            ]

    def update_substitution(
        self, capture_id: str, reason: SubstitutionReason, notes: str | None
    ) -> None:
        with self.connect() as connection:
            result = connection.execute("""
                UPDATE captures SET substitution_reason = ?, substitution_notes = ?
                WHERE id = ? AND event = 'substitution'
            """, (reason.value, notes, capture_id))
            if result.rowcount != 1:
                raise ValueError("Select an existing substitution capture.")

    def update_minute(self, capture_id: str, minute: str | None) -> None:
        with self.connect() as connection:
            connection.execute("UPDATE captures SET minute = ? WHERE id = ?", (minute, capture_id))

    @contextmanager
    def deleting_capture(self, capture_id: str) -> Iterator[bool]:
        with self.connect() as connection:
            result = connection.execute("DELETE FROM captures WHERE id = ?", (capture_id,))
            yield result.rowcount == 1
