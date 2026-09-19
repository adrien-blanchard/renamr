"""Shared immutable data models used by the renaming engine and UI."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class PlanStatus(str, Enum):
    """Validation state for an individual planned rename."""

    READY = "Ready"
    WAITING = "Waiting"
    NO_MATCH = "No match"
    UNCHANGED = "Unchanged"
    ERROR = "Error"


@dataclass(frozen=True, slots=True)
class DetectedFile:
    """A source file with an optional frame number identified in its stem."""

    path: Path
    selection_index: int
    frame_number: int | None = None
    frame_text: str | None = None
    frame_start: int | None = None
    frame_end: int | None = None
    sequence_key: str | None = None
    sequence_pattern: str | None = None
    detection_warning: str | None = None

    @property
    def is_sequence(self) -> bool:
        return self.frame_number is not None and self.sequence_key is not None


@dataclass(frozen=True, slots=True)
class SequenceGroup:
    """A group of files sharing one detected frame-number location."""

    key: str
    directory: Path
    prefix: str
    suffix: str
    extension: str
    files: tuple[DetectedFile, ...]
    ambiguous: bool = False
    warning: str | None = None

    @property
    def frames(self) -> tuple[int, ...]:
        return tuple(
            sorted(
                item.frame_number
                for item in self.files
                if item.frame_number is not None
            )
        )

    @property
    def first_frame(self) -> int | None:
        return self.frames[0] if self.frames else None

    @property
    def last_frame(self) -> int | None:
        return self.frames[-1] if self.frames else None

    @property
    def missing_frames(self) -> tuple[int, ...]:
        frames = self.frames
        if len(frames) < 2:
            return ()
        first, last = frames[0], frames[-1]
        if last - first > 100_000:
            return ()
        existing = set(frames)
        return tuple(frame for frame in range(first, last + 1) if frame not in existing)

    @property
    def missing_frame_count(self) -> int:
        """Return the number of gaps without enumerating a potentially huge range."""

        existing = set(self.frames)
        if len(existing) < 2:
            return 0
        first, last = min(existing), max(existing)
        return (last - first + 1) - len(existing)


@dataclass(frozen=True, slots=True)
class RenamePlanItem:
    """One source-to-target mapping and its validation result."""

    source: Path
    target: Path
    selection_index: int
    frame_number: int | None
    status: PlanStatus
    message: str = ""


@dataclass(frozen=True, slots=True)
class RenamePlan:
    """A complete immutable plan that must validate before execution."""

    items: tuple[RenamePlanItem, ...]
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ready_items(self) -> tuple[RenamePlanItem, ...]:
        return tuple(item for item in self.items if item.status is PlanStatus.READY)

    @property
    def error_items(self) -> tuple[RenamePlanItem, ...]:
        return tuple(item for item in self.items if item.status is PlanStatus.ERROR)

    @property
    def waiting_items(self) -> tuple[RenamePlanItem, ...]:
        return tuple(item for item in self.items if item.status is PlanStatus.WAITING)

    @property
    def no_match_items(self) -> tuple[RenamePlanItem, ...]:
        return tuple(item for item in self.items if item.status is PlanStatus.NO_MATCH)

    @property
    def unchanged_items(self) -> tuple[RenamePlanItem, ...]:
        return tuple(item for item in self.items if item.status is PlanStatus.UNCHANGED)

    @property
    def can_execute(self) -> bool:
        return (
            bool(self.ready_items)
            and not self.errors
            and not self.error_items
            and not self.waiting_items
        )
