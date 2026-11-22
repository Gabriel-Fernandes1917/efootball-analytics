import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from .database import Database
from .models import Capture, Event, Match, SubstitutionReason

logger = logging.getLogger(__name__)


class ScreenshotWriter(Protocol):
    def save(self, destination: Path) -> None: ...


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def normalize_minute(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    if not re.fullmatch(r"[0-9]{1,3}(?:\+[0-9]{1,2})?", value):
        raise ValueError("Enter a minute from 0 to 120, optionally with stoppage time such as 90+3.")
    base, plus, added = value.partition("+")
    if int(base) > 120 or (plus and int(added) == 0):
        raise ValueError("Enter a minute from 0 to 120 with positive stoppage time.")
    return f"{int(base)}+{int(added)}" if plus else str(int(base))


class Sessions:
    def __init__(self, database: Database, root: Path, writer: ScreenshotWriter) -> None:
        self.database = database
        self.root = root
        self.writer = writer

    def active(self) -> Match | None:
        return self.database.active_match()

    def start(self, opponent: str, team: str) -> Match:
        if not opponent.strip() or not team.strip():
            raise ValueError("Enter both the opponent and your team.")
        match = Match(uuid4().hex, opponent.strip(), team.strip(), timestamp(), None)
        try:
            self.database.start(match)
        except sqlite3.IntegrityError as exc:
            raise ValueError("Finish the active match before starting another.") from exc
        return match

    def finish(self) -> None:
        if match := self.active():
            self.database.finish(match.id, timestamp())

    def capture(self, event: Event, minute: str = "") -> Capture:
        match = self.active()
        if match is None:
            raise ValueError("Start a match before capturing.")
        capture_id = uuid4().hex
        path = Path("screenshots", match.id, event.value, f"{capture_id}.png")
        capture = Capture(capture_id, match.id, event, timestamp(), path, normalize_minute(minute))
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.writer.save(destination)
            self.database.add_capture(capture)
        except Exception:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                logger.exception("Could not clean up failed capture %s", capture.id)
            raise
        logger.info("Saved %s capture %s for match %s", event.value, capture.id, match.id)
        return capture

    def correct_minute(self, capture: Capture, minute: str) -> None:
        self.database.update_minute(capture.id, normalize_minute(minute))

    def classify_substitution(
        self, capture: Capture, reason: SubstitutionReason, notes: str = ""
    ) -> None:
        self.database.update_substitution(capture.id, reason, notes.strip() or None)

    def delete(self, capture: Capture) -> None:
        path = self.root / capture.path
        staged = path.with_suffix(f".{uuid4().hex}.deleting")
        try:
            with self.database.deleting_capture(capture.id) as deleted:
                if not deleted:
                    return
                try:
                    path.replace(staged)
                except FileNotFoundError:
                    pass
        except Exception:
            try:
                staged.replace(path)
            except FileNotFoundError:
                pass
            except OSError:
                logger.exception("Could not restore staged screenshot %s", staged)
            raise
        try:
            staged.unlink(missing_ok=True)
        except OSError:
            logger.exception("Could not remove staged screenshot %s", staged)
