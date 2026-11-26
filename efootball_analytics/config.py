import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile

from .models import Event

logger = logging.getLogger(__name__)
MODIFIERS = ("ctrl", "alt", "shift", "cmd")


def default_shortcuts() -> dict[Event, str]:
    return {event: f"F{number}" for number, event in enumerate(Event, 6)}


def parse_shortcut(value: str) -> str:
    parts = [part.strip().lower() for part in value.split("+")]
    modifiers, key = parts[:-1], parts[-1]
    if (not re.fullmatch(r"f(?:[1-9]|1[0-2])|[a-z0-9]", key)
            or any(part not in MODIFIERS for part in modifiers)
            or len(set(modifiers)) != len(modifiers)
            or (len(key) == 1 and not modifiers)):
        raise ValueError("Use F1–F12 or a shortcut such as Ctrl+Shift+G.")
    tokens = [f"<{part}>" for part in MODIFIERS if part in modifiers]
    return "+".join([*tokens, f"<{key}>" if len(key) > 1 else key])


@dataclass(frozen=True)
class Settings:
    enabled: bool = True
    shortcuts: dict[Event, str] = field(default_factory=default_shortcuts)

    def bindings(self) -> dict[str, Event]:
        bindings = {parse_shortcut(self.shortcuts[event]): event for event in Event}
        if len(bindings) != len(Event):
            raise ValueError("Assign a different shortcut to each event.")
        return bindings


class Config:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Settings:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            enabled = data["enabled"]
            if not isinstance(enabled, bool):
                raise ValueError("The enabled setting must be a boolean.")
            settings = Settings(enabled, {event: data["shortcuts"][event.value] for event in Event})
            settings.bindings()
            return settings
        except FileNotFoundError:
            return Settings()
        except (ValueError, TypeError, KeyError, AttributeError, OSError):
            logger.exception("Could not load settings. Global shortcuts are disabled.")
            return Settings(enabled=False)

    def save(self, settings: Settings) -> None:
        settings.bindings()
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                delete=False) as temporary:
            temp_path = Path(temporary.name)
            try:
                json.dump({"enabled": settings.enabled, "shortcuts": settings.shortcuts},
                          temporary, indent=2)
                temporary.close()
                os.replace(temp_path, self.path)
            finally:
                temporary.close()
                temp_path.unlink(missing_ok=True)
