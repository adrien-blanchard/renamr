"""Pure rename-plan construction and Windows-safe validation."""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import replace
from enum import Enum
from pathlib import Path
from typing import Iterable

from .models import DetectedFile, PlanStatus, RenamePlan, RenamePlanItem
from .patterns import PatternError, normalize_pattern, render_frame_pattern


SUPPORTED_SEQUENCE_EXTENSIONS = frozenset({".exr", ".png"})
_INVALID_WINDOWS_CHARS = frozenset('<>:"/\\|?*')
_RESERVED_WINDOWS_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
        "COM¹",
        "COM²",
        "COM³",
        "LPT¹",
        "LPT²",
        "LPT³",
    }
)


class RenameMode(str, Enum):
    """User-facing rename modes."""

    SEQUENCE = "Rename Sequence"
    REPLACE = "Find & Replace"


def canonical_path_key(path: Path) -> str:
    """Return a Windows-like comparison key, even when tests run elsewhere."""

    absolute = os.path.abspath(os.fspath(path))
    return absolute.replace("/", "\\").casefold()


def validate_windows_basename(name: str) -> str | None:
    """Return an English validation error, or None when a basename is safe."""

    if not name:
        return "The output name is empty."
    if name in {".", ".."}:
        return "The output name cannot be '.' or '..'."
    if any(character in _INVALID_WINDOWS_CHARS for character in name):
        return 'The output name contains a Windows-reserved character: <>:"/\\|?*.'
    if any(ord(character) < 32 for character in name):
        return "The output name contains a control character."
    if name.endswith((" ", ".")):
        return "Windows file names cannot end with a space or period."
    try:
        utf16_code_units = len(name.encode("utf-16-le")) // 2
    except UnicodeEncodeError:
        return "The output name contains an invalid Unicode character."
    if utf16_code_units > 255:
        return "The output name is longer than 255 UTF-16 code units."

    device_name = name.split(".", 1)[0].rstrip(" .").upper()
    if device_name in _RESERVED_WINDOWS_NAMES:
        return f"'{device_name}' is a reserved Windows device name."
    return None


def _with_original_extension(pattern: str, extension: str) -> str:
    clean_pattern = pattern.strip()
    lower_pattern = clean_pattern.casefold()
    lower_extension = extension.casefold()
    if lower_pattern.endswith(lower_extension):
        return clean_pattern[: -len(extension)] + extension

    for known_extension in SUPPORTED_SEQUENCE_EXTENSIONS:
        if lower_pattern.endswith(known_extension):
            raise PatternError(
                f"The source extension is {extension}; changing it to "
                f"{known_extension} would not convert the image format."
            )
    return clean_pattern + extension


def _sequence_target_name(item: DetectedFile, pattern: str) -> tuple[str, str | None]:
    extension = item.path.suffix
    complete_pattern = _with_original_extension(pattern, extension)
    if not item.is_sequence:
        raise PatternError(
            "No frame number was detected; Sequence mode never numbers files "
            "from their selection order."
        )

    assert item.frame_number is not None
    return render_frame_pattern(complete_pattern, item.frame_number), None


def _multi_sequence_directories(
    files: tuple[DetectedFile, ...],
) -> frozenset[str]:
    """Return directories containing more than one detected sequence key."""

    keys_by_directory: dict[str, set[str]] = {}
    for item in files:
        if item.sequence_key is None:
            continue
        directory_key = canonical_path_key(item.path.parent)
        keys_by_directory.setdefault(directory_key, set()).add(
            item.sequence_key.casefold()
        )
    return frozenset(
        directory
        for directory, sequence_keys in keys_by_directory.items()
        if len(sequence_keys) > 1
    )


def _replace_target_name(
    item: DetectedFile,
    find_text: str,
    replacement: str,
) -> tuple[str, str | None]:
    stem = item.path.stem
    if (
        item.frame_number is not None
        and item.frame_start is not None
        and item.frame_end is not None
    ):
        prefix = stem[: item.frame_start]
        frame_text = stem[item.frame_start : item.frame_end]
        suffix = stem[item.frame_end :]
        replaced_prefix = prefix.replace(find_text, replacement)
        replaced_suffix = suffix.replace(find_text, replacement)
        if replaced_prefix == prefix and replaced_suffix == suffix:
            return (
                item.path.name,
                "The search text was not found outside the frame token.",
            )
        return replaced_prefix + frame_text + replaced_suffix + item.path.suffix, None

    replaced_stem = stem.replace(find_text, replacement)
    if replaced_stem == stem:
        return item.path.name, "The search text was not found."
    return replaced_stem + item.path.suffix, None


def _waiting_plan(
    files: tuple[DetectedFile, ...],
    message: str,
) -> RenamePlan:
    """Keep imported files visible while a required field is still empty."""

    return _validate_complete_plan(
        [
            RenamePlanItem(
                source=item.path,
                target=item.path,
                selection_index=item.selection_index,
                frame_number=item.frame_number,
                status=PlanStatus.WAITING,
                message=message,
            )
            for item in files
        ]
    )


def _validate_complete_plan(items: list[RenamePlanItem]) -> RenamePlan:
    source_keys = [canonical_path_key(item.source) for item in items]
    source_key_set = set(source_keys)
    duplicate_sources = {
        key for key, count in Counter(source_keys).items() if count > 1
    }
    target_keys = [canonical_path_key(item.target) for item in items]
    duplicate_targets = {
        key for key, count in Counter(target_keys).items() if count > 1
    }
    validated: list[RenamePlanItem] = []
    global_errors: list[str] = []

    for item, source_key, target_key in zip(
        items, source_keys, target_keys, strict=True
    ):
        messages: list[str] = [item.message] if item.message else []
        status = item.status

        if source_key in duplicate_sources:
            status = PlanStatus.ERROR
            messages.append("This source file was added more than once.")
        if not item.source.exists() or not item.source.is_file():
            status = PlanStatus.ERROR
            messages.append(
                "The source file no longer exists or is not a regular file."
            )
        if canonical_path_key(item.source.parent) != canonical_path_key(
            item.target.parent
        ):
            status = PlanStatus.ERROR
            messages.append("Renaming cannot move a file outside its source folder.")

        basename_error = validate_windows_basename(item.target.name)
        if basename_error:
            status = PlanStatus.ERROR
            messages.append(basename_error)
        if target_key in duplicate_targets:
            status = PlanStatus.ERROR
            messages.append("More than one source produces this destination name.")

        exact_same_path = os.fspath(item.source) == os.fspath(item.target)
        if exact_same_path and status is PlanStatus.READY:
            status = PlanStatus.UNCHANGED
            if not messages:
                messages.append("The output name is unchanged.")
        elif item.target.exists() and target_key not in source_key_set:
            status = PlanStatus.ERROR
            messages.append(
                "The destination already exists and will not be overwritten."
            )

        message = " ".join(dict.fromkeys(message for message in messages if message))
        validated_item = replace(item, status=status, message=message)
        validated.append(validated_item)
        if status is PlanStatus.ERROR and message:
            global_errors.append(f"{item.source.name}: {message}")

    return RenamePlan(
        items=tuple(validated),
        errors=tuple(dict.fromkeys(global_errors)),
    )


def build_sequence_plan(
    detected_files: Iterable[DetectedFile],
    output_pattern: str,
) -> RenamePlan:
    """Build a frame-preserving rename plan from a Nuke-style pattern."""

    files = tuple(detected_files)
    if not files:
        return RenamePlan(
            items=(), errors=("Add files before building a rename plan.",)
        )

    if not output_pattern.strip():
        return _waiting_plan(files, "Enter an output pattern to preview the rename.")

    try:
        normalized_pattern = normalize_pattern(output_pattern.strip())
    except PatternError as error:
        items = tuple(
            RenamePlanItem(
                source=item.path,
                target=item.path,
                selection_index=item.selection_index,
                frame_number=item.frame_number,
                status=PlanStatus.ERROR,
                message=str(error),
            )
            for item in files
        )
        return RenamePlan(items=items, errors=(str(error),))

    multi_sequence_directories = _multi_sequence_directories(files)
    planned: list[RenamePlanItem] = []
    for item in files:
        blocking_messages: list[str] = []
        if not item.is_sequence:
            blocking_messages.append(
                "No frame number was detected; Sequence mode never numbers files "
                "from their selection order."
            )
        if item.detection_warning:
            blocking_messages.append(item.detection_warning)
        if canonical_path_key(item.path.parent) in multi_sequence_directories:
            blocking_messages.append(
                "This folder contains multiple selected sequences; a single global "
                "output pattern could merge them. Rename one sequence at a time."
            )
        if blocking_messages:
            planned.append(
                RenamePlanItem(
                    source=item.path,
                    target=item.path,
                    selection_index=item.selection_index,
                    frame_number=item.frame_number,
                    status=PlanStatus.ERROR,
                    message=" ".join(blocking_messages),
                )
            )
            continue

        try:
            target_name, warning = _sequence_target_name(item, normalized_pattern)
            target = item.path.with_name(target_name)
            planned.append(
                RenamePlanItem(
                    source=item.path,
                    target=target,
                    selection_index=item.selection_index,
                    frame_number=item.frame_number,
                    status=PlanStatus.READY,
                    message=warning or "",
                )
            )
        except (PatternError, ValueError) as error:
            planned.append(
                RenamePlanItem(
                    source=item.path,
                    target=item.path,
                    selection_index=item.selection_index,
                    frame_number=item.frame_number,
                    status=PlanStatus.ERROR,
                    message=str(error),
                )
            )
    return _validate_complete_plan(planned)


def build_replace_plan(
    detected_files: Iterable[DetectedFile],
    find_text: str,
    replacement: str,
) -> RenamePlan:
    """Build a find/replace plan that never modifies a detected frame token."""

    files = tuple(detected_files)
    if not files:
        return RenamePlan(
            items=(), errors=("Add files before building a rename plan.",)
        )
    if not find_text:
        return _waiting_plan(files, "Enter text to find to preview the replacement.")

    planned: list[RenamePlanItem] = []
    for item in files:
        if item.detection_warning:
            planned.append(
                RenamePlanItem(
                    source=item.path,
                    target=item.path,
                    selection_index=item.selection_index,
                    frame_number=item.frame_number,
                    status=PlanStatus.ERROR,
                    message=item.detection_warning,
                )
            )
            continue
        target_name, warning = _replace_target_name(item, find_text, replacement)
        basename_error = validate_windows_basename(target_name)
        if basename_error:
            planned.append(
                RenamePlanItem(
                    source=item.path,
                    target=item.path,
                    selection_index=item.selection_index,
                    frame_number=item.frame_number,
                    status=PlanStatus.ERROR,
                    message=basename_error,
                )
            )
            continue
        try:
            target = item.path.with_name(target_name)
        except ValueError as error:
            planned.append(
                RenamePlanItem(
                    source=item.path,
                    target=item.path,
                    selection_index=item.selection_index,
                    frame_number=item.frame_number,
                    status=PlanStatus.ERROR,
                    message=f"The replacement creates an invalid file name: {error}",
                )
            )
            continue
        planned.append(
            RenamePlanItem(
                source=item.path,
                target=target,
                selection_index=item.selection_index,
                frame_number=item.frame_number,
                status=PlanStatus.NO_MATCH if warning else PlanStatus.READY,
                message=warning or "",
            )
        )
    return _validate_complete_plan(planned)


def build_plan(
    detected_files: Iterable[DetectedFile],
    mode: RenameMode,
    *,
    output_pattern: str = "",
    find_text: str = "",
    replacement: str = "",
) -> RenamePlan:
    """Dispatch to the selected pure planning strategy."""

    if mode is RenameMode.SEQUENCE:
        return build_sequence_plan(detected_files, output_pattern)
    return build_replace_plan(detected_files, find_text, replacement)
