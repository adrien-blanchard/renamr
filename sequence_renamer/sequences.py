"""Detect PNG and EXR image sequences without changing selection order."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
import re
from typing import Iterable

from .models import DetectedFile, SequenceGroup


SUPPORTED_SEQUENCE_EXTENSIONS = frozenset({".exr", ".png"})

_DIGIT_GROUP_RE = re.compile(r"\d+")
_SEMANTIC_NUMBER_LABELS = frozenset(
    {
        "cam",
        "camera",
        "layer",
        "part",
        "pass",
        "scene",
        "seq",
        "sequence",
        "shot",
        "take",
        "tile",
        "ver",
        "version",
    }
)
_MULTIPLE_VARIABLE_FIELDS_WARNING = (
    "Frame number is ambiguous because multiple numeric fields vary between "
    "the selected files."
)
_MULTIPLE_PLAUSIBLE_FIELDS_WARNING = (
    "Frame number is ambiguous because the filename contains multiple "
    "plausible numeric fields."
)


@dataclass(frozen=True, slots=True)
class _NumericCandidate:
    start: int
    end: int
    digit_start: int
    text: str
    value: int


@dataclass(frozen=True, slots=True)
class _PendingFile:
    path: Path
    selection_index: int
    candidates: tuple[_NumericCandidate, ...]


@dataclass(frozen=True, slots=True)
class _FamilyDecision:
    candidate_index: int
    warning: str | None = None
    use_family_key: bool = False


@dataclass(slots=True)
class _SequenceBucket:
    key: str
    directory: Path
    prefix: str
    suffix: str
    extension: str
    files: list[DetectedFile] = field(default_factory=list)
    ambiguous: bool = False
    warning: str | None = None


def _is_version_group(stem: str, digit_start: int) -> bool:
    """Return whether a digit group is the numeric part of ``v###``."""

    return digit_start > 0 and stem[digit_start - 1] in {"v", "V"}


def _numeric_candidates(stem: str) -> tuple[_NumericCandidate, ...]:
    """Return every non-version numeric field that could be a frame token."""

    candidates: list[_NumericCandidate] = []
    for match in _DIGIT_GROUP_RE.finditer(stem):
        if _is_version_group(stem, match.start()):
            continue

        start = match.start()

        # A hyphen is a negative sign when it starts the stem or follows
        # another separator.  In ``render-0001`` it remains part of the prefix,
        # while in ``render.-001`` it belongs to the frame number.
        if start > 0 and stem[start - 1] == "-":
            hyphen_index = start - 1
            if hyphen_index == 0 or not stem[hyphen_index - 1].isalnum():
                start = hyphen_index

        text = stem[start : match.end()]
        candidates.append(
            _NumericCandidate(
                start=start,
                end=match.end(),
                digit_start=match.start(),
                text=text,
                value=int(text),
            )
        )
    return tuple(candidates)


def _numeric_skeleton(stem: str, candidates: tuple[_NumericCandidate, ...]) -> str:
    """Replace all candidates so matching files can be analysed as a family."""

    parts: list[str] = []
    cursor = 0
    for candidate in candidates:
        parts.append(stem[cursor : candidate.start])
        parts.append("<NUMBER>")
        cursor = candidate.end
    parts.append(stem[cursor:])
    return "".join(parts)


def _family_key(path: Path, candidates: tuple[_NumericCandidate, ...]) -> str:
    skeleton = _numeric_skeleton(path.stem, candidates)
    pattern_path = path.parent.absolute() / f"{skeleton}{path.suffix}"
    return str(pattern_path).casefold()


def _sequence_key(directory: Path, prefix: str, suffix: str, extension: str) -> str:
    """Build an opaque, case-insensitive key suitable for Windows paths."""

    pattern_path = directory.absolute() / f"{prefix}<FRAME>{suffix}{extension}"
    return str(pattern_path).casefold()


def _attached_word(stem: str, candidate: _NumericCandidate) -> str | None:
    """Return a directly attached alphabetic label, such as ``take`` in take01."""

    if candidate.digit_start == 0 or not stem[candidate.digit_start - 1].isalpha():
        return None
    start = candidate.digit_start - 1
    while start > 0 and stem[start - 1].isalpha():
        start -= 1
    return stem[start : candidate.digit_start].casefold()


def _is_semantic_identifier(stem: str, candidate: _NumericCandidate) -> bool:
    word = _attached_word(stem, candidate)
    return word in _SEMANTIC_NUMBER_LABELS if word is not None else False


def _is_short_semantic_identifier(stem: str, candidate: _NumericCandidate) -> bool:
    digit_count = len(candidate.text.removeprefix("-"))
    return digit_count <= 3 and _is_semantic_identifier(stem, candidate)


def _candidate_score(stem: str, candidate: _NumericCandidate) -> int:
    """Rank candidates for a preview only; ambiguous choices remain blocked."""

    score = 0
    digit_count = len(candidate.text.removeprefix("-"))
    if candidate.start > 0 and not stem[candidate.start - 1].isalnum():
        score += 4
    if digit_count >= 3:
        score += 2
    if digit_count >= 4:
        score += 1
    if candidate.end == len(stem):
        score += 3
    if _is_semantic_identifier(stem, candidate):
        score -= 6
    return score


def _best_candidate_index(files: list[_PendingFile]) -> int:
    candidate_count = len(files[0].candidates)
    scores = [
        sum(_candidate_score(item.path.stem, item.candidates[index]) for item in files)
        for index in range(candidate_count)
    ]
    return max(range(candidate_count), key=lambda index: scores[index])


def _analyse_family(files: list[_PendingFile]) -> _FamilyDecision:
    """Choose a coherent numeric column or return a blocking ambiguity."""

    candidate_count = len(files[0].candidates)
    varying_columns = [
        index
        for index in range(candidate_count)
        if len({item.candidates[index].value for item in files}) > 1
    ]

    if len(varying_columns) > 1:
        return _FamilyDecision(
            candidate_index=_best_candidate_index(files),
            warning=_MULTIPLE_VARIABLE_FIELDS_WARNING,
            use_family_key=True,
        )

    if len(varying_columns) == 1:
        candidate_index = varying_columns[0]
    elif candidate_count == 1:
        candidate_index = 0
    else:
        candidate_index = _best_candidate_index(files)
        nonselected_are_identifiers = all(
            _is_semantic_identifier(item.path.stem, item.candidates[index])
            for item in files
            for index in range(candidate_count)
            if index != candidate_index
        )
        if not nonselected_are_identifiers:
            return _FamilyDecision(
                candidate_index=candidate_index,
                warning=_MULTIPLE_PLAUSIBLE_FIELDS_WARNING,
                use_family_key=True,
            )

    selected_candidates = (
        (item.path.stem, item.candidates[candidate_index]) for item in files
    )
    if any(
        _is_short_semantic_identifier(stem, candidate)
        for stem, candidate in selected_candidates
    ):
        label = _attached_word(files[0].path.stem, files[0].candidates[candidate_index])
        warning = (
            f"Frame number is ambiguous because '{label}' is followed by a "
            "short semantic identifier."
        )
        return _FamilyDecision(
            candidate_index=candidate_index,
            warning=warning,
            use_family_key=True,
        )

    return _FamilyDecision(candidate_index=candidate_index)


def _detected_file(
    item: _PendingFile,
    candidate: _NumericCandidate,
    *,
    warning: str | None,
    key_override: str | None,
) -> DetectedFile:
    stem = item.path.stem
    extension = item.path.suffix
    prefix = stem[: candidate.start]
    suffix = stem[candidate.end :]
    key = key_override or _sequence_key(item.path.parent, prefix, suffix, extension)

    # Width follows printf semantics: the sign occupies one character for a
    # negative frame.  This maps ``-001`` and ``0001`` to the same #### token.
    token_width = len(candidate.text)
    sequence_pattern = f"{prefix}{'#' * token_width}{suffix}{extension}"

    return DetectedFile(
        path=item.path,
        selection_index=item.selection_index,
        frame_number=candidate.value,
        frame_text=candidate.text,
        frame_start=candidate.start,
        frame_end=candidate.end,
        sequence_key=key,
        sequence_pattern=sequence_pattern,
        detection_warning=warning,
    )


def detect_sequences(
    paths: Iterable[str | PathLike[str] | Path],
) -> tuple[tuple[DetectedFile, ...], tuple[SequenceGroup, ...]]:
    """Detect and group image sequences from *paths*.

    The first returned tuple always mirrors input selection order, including
    unsupported and non-sequence files.  Numeric candidates are compared across
    structurally matching names so multiple changing fields are never silently
    interpreted as frames.  Groups are ordered by first appearance and retain
    selection order.
    """

    raw_paths = [Path(raw_path) for raw_path in paths]
    detected_by_index: list[DetectedFile | None] = [None] * len(raw_paths)
    families: OrderedDict[str, list[_PendingFile]] = OrderedDict()

    for selection_index, path in enumerate(raw_paths):
        if path.suffix.casefold() not in SUPPORTED_SEQUENCE_EXTENSIONS:
            detected_by_index[selection_index] = DetectedFile(
                path=path, selection_index=selection_index
            )
            continue

        candidates = _numeric_candidates(path.stem)
        if not candidates:
            detected_by_index[selection_index] = DetectedFile(
                path=path, selection_index=selection_index
            )
            continue

        pending = _PendingFile(
            path=path,
            selection_index=selection_index,
            candidates=candidates,
        )
        families.setdefault(_family_key(path, candidates), []).append(pending)

    for family_key, files in families.items():
        decision = _analyse_family(files)
        for item in files:
            candidate = item.candidates[decision.candidate_index]
            detected_by_index[item.selection_index] = _detected_file(
                item,
                candidate,
                warning=decision.warning,
                key_override=family_key if decision.use_family_key else None,
            )

    detected_files = tuple(item for item in detected_by_index if item is not None)
    buckets: OrderedDict[str, _SequenceBucket] = OrderedDict()

    for detected in detected_files:
        if not detected.is_sequence:
            continue

        assert detected.sequence_key is not None
        assert detected.frame_start is not None
        assert detected.frame_end is not None

        stem = detected.path.stem
        prefix = stem[: detected.frame_start]
        suffix = stem[detected.frame_end :]
        bucket = buckets.get(detected.sequence_key)
        if bucket is None:
            bucket = _SequenceBucket(
                key=detected.sequence_key,
                directory=detected.path.parent,
                prefix=prefix,
                suffix=suffix,
                extension=detected.path.suffix,
                ambiguous=detected.detection_warning is not None,
                warning=detected.detection_warning,
            )
            buckets[detected.sequence_key] = bucket
        elif detected.detection_warning is not None:
            bucket.ambiguous = True
            bucket.warning = bucket.warning or detected.detection_warning
        bucket.files.append(detected)

    groups: list[SequenceGroup] = []
    for bucket in buckets.values():
        frame_numbers = [
            item.frame_number for item in bucket.files if item.frame_number is not None
        ]
        has_duplicate_frames = len(frame_numbers) != len(set(frame_numbers))
        ambiguous = bucket.ambiguous or has_duplicate_frames
        warning = bucket.warning
        if has_duplicate_frames:
            duplicate_warning = "The sequence contains duplicate frame numbers."
            warning = f"{warning} {duplicate_warning}" if warning else duplicate_warning
        groups.append(
            SequenceGroup(
                key=bucket.key,
                directory=bucket.directory,
                prefix=bucket.prefix,
                suffix=bucket.suffix,
                extension=bucket.extension,
                files=tuple(bucket.files),
                ambiguous=ambiguous,
                warning=warning,
            )
        )

    return detected_files, tuple(groups)


__all__ = ["SUPPORTED_SEQUENCE_EXTENSIONS", "detect_sequences"]
