from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Event(str, Enum):
    FORMATION = "formation"
    SUBSTITUTION = "substitution"
    GOAL = "goal"
    NEAR_MISS = "near_miss"
    MATCH_STATISTICS = "match_statistics"
    FINAL_RESULT = "final_result"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").capitalize()


@dataclass(frozen=True)
class Match:
    id: str
    opponent: str
    team: str
    started_at: str
    finished_at: str | None


class SubstitutionReason(str, Enum):
    PHYSICAL = "physical"
    TACTICAL = "tactical"
    PERFORMANCE = "performance"
    OTHER = "other"

    @property
    def label(self) -> str:
        return {
            SubstitutionReason.PHYSICAL: "Physical / Fatigue",
            SubstitutionReason.TACTICAL: "Tactical",
            SubstitutionReason.PERFORMANCE: "Poor Performance",
            SubstitutionReason.OTHER: "Other",
        }[self]


@dataclass(frozen=True)
class Capture:
    id: str
    match_id: str
    event: Event
    captured_at: str
    path: Path
    minute: str | None
    substitution_reason: SubstitutionReason | None = None
    substitution_notes: str | None = None
