from dataclasses import replace
from pathlib import Path

import pytest

from sequence_renamer.models import DetectedFile, PlanStatus
from sequence_renamer.planner import (
    build_replace_plan,
    build_sequence_plan,
    validate_windows_basename,
)


def detected(
    path: Path, index: int, frame: int | None, text: str | None = None
) -> DetectedFile:
    stem = path.stem
    start = stem.rfind(text) if text else None
    end = start + len(text) if start is not None and text is not None else None
    return DetectedFile(
        path=path,
        selection_index=index,
        frame_number=frame,
        frame_text=text,
        frame_start=start,
        frame_end=end,
        sequence_key="sequence" if frame is not None else None,
    )


def test_sequence_plan_preserves_frames_gaps_and_selection_independence(
    tmp_path: Path,
) -> None:
    paths = [tmp_path / "plate.1004.exr", tmp_path / "plate.1001.exr"]
    for path in paths:
        path.write_bytes(b"frame")
    files = [detected(paths[0], 0, 1004, "1004"), detected(paths[1], 1, 1001, "1001")]

    plan = build_sequence_plan(files, "comp.####.exr")

    assert plan.can_execute
    assert [item.target.name for item in plan.items] == [
        "comp.1004.exr",
        "comp.1001.exr",
    ]


def test_nuke_printf_alias_is_accepted(tmp_path: Path) -> None:
    source = tmp_path / "plate.7.png"
    source.write_bytes(b"frame")

    plan = build_sequence_plan([detected(source, 0, 7, "7")], "preview.%04")

    assert plan.can_execute
    assert plan.items[0].target.name == "preview.0007.png"


def test_extension_change_is_blocked(tmp_path: Path) -> None:
    source = tmp_path / "plate.1001.exr"
    source.write_bytes(b"frame")

    plan = build_sequence_plan([detected(source, 0, 1001, "1001")], "preview.####.png")

    assert not plan.can_execute
    assert "would not convert" in plan.items[0].message


def test_find_replace_protects_frame_digits(tmp_path: Path) -> None:
    source = tmp_path / "shot1001_take.1001.exr"
    source.write_bytes(b"frame")
    item = detected(source, 0, 1001, "1001")
    # Select the final occurrence as the detected frame.
    item = DetectedFile(
        path=item.path,
        selection_index=0,
        frame_number=1001,
        frame_text="1001",
        frame_start=source.stem.rfind("1001"),
        frame_end=source.stem.rfind("1001") + 4,
        sequence_key="sequence",
    )

    plan = build_replace_plan([item], "1001", "2002")

    assert plan.can_execute
    assert plan.items[0].target.name == "shot2002_take.1001.exr"


def test_empty_sequence_pattern_keeps_ordered_waiting_rows(tmp_path: Path) -> None:
    paths = [tmp_path / "plate.1004.exr", tmp_path / "plate.1001.exr"]
    for path in paths:
        path.write_bytes(b"frame")
    files = [
        detected(paths[0], 0, 1004, "1004"),
        detected(paths[1], 1, 1001, "1001"),
    ]

    plan = build_sequence_plan(files, "")

    assert not plan.can_execute
    assert [item.source for item in plan.items] == paths
    assert [item.target for item in plan.items] == paths
    assert [item.frame_number for item in plan.items] == [1004, 1001]
    assert all(item.status is PlanStatus.WAITING for item in plan.items)


def test_empty_find_keeps_ordered_waiting_rows(tmp_path: Path) -> None:
    paths = [tmp_path / "plate.1004.exr", tmp_path / "plate.1001.exr"]
    for path in paths:
        path.write_bytes(b"frame")
    files = [
        detected(paths[0], 0, 1004, "1004"),
        detected(paths[1], 1, 1001, "1001"),
    ]

    plan = build_replace_plan(files, "", "comp")

    assert not plan.can_execute
    assert [item.source for item in plan.items] == paths
    assert [item.target for item in plan.items] == paths
    assert all(item.status is PlanStatus.WAITING for item in plan.items)


def test_find_replace_marks_nonmatching_rows_without_blocking_matches(
    tmp_path: Path,
) -> None:
    matching = tmp_path / "preview.1001.exr"
    untouched = tmp_path / "matte.1002.exr"
    matching.write_bytes(b"frame")
    untouched.write_bytes(b"frame")
    files = [
        detected(matching, 0, 1001, "1001"),
        detected(untouched, 1, 1002, "1002"),
    ]

    plan = build_replace_plan(files, "preview", "final")

    assert plan.can_execute
    assert plan.items[0].status is PlanStatus.READY
    assert plan.items[0].target.name == "final.1001.exr"
    assert plan.items[1].status is PlanStatus.NO_MATCH
    assert plan.items[1].target == untouched


@pytest.mark.parametrize("name", ["CON.exr", "bad?.png", "trailing .exr.", "NUL.txt"])
def test_windows_invalid_names_are_rejected(name: str) -> None:
    assert validate_windows_basename(name) is not None


def test_windows_basename_length_counts_utf16_code_units() -> None:
    assert validate_windows_basename("a" * 253 + "🎬") is None

    error = validate_windows_basename("a" * 254 + "🎬")

    assert error == "The output name is longer than 255 UTF-16 code units."


def test_windows_basename_rejects_lone_surrogate_safely() -> None:
    error = validate_windows_basename("invalid\ud800name.exr")

    assert error == "The output name contains an invalid Unicode character."


def test_duplicate_destinations_are_blocked(tmp_path: Path) -> None:
    first = tmp_path / "one.0001.exr"
    second = tmp_path / "two.0001.exr"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    files = [detected(first, 0, 1, "0001"), detected(second, 1, 1, "0001")]

    plan = build_sequence_plan(files, "same.####")

    assert not plan.can_execute
    assert all(item.status is PlanStatus.ERROR for item in plan.items)


def test_external_existing_destination_is_never_overwritten(tmp_path: Path) -> None:
    source = tmp_path / "plate.0001.exr"
    destination = tmp_path / "comp.0001.exr"
    source.write_bytes(b"source")
    destination.write_bytes(b"existing")

    plan = build_sequence_plan([detected(source, 0, 1, "0001")], "comp.####")

    assert not plan.can_execute
    assert "will not be overwritten" in plan.items[0].message


def test_sequence_mode_blocks_files_without_a_detected_frame(tmp_path: Path) -> None:
    source = tmp_path / "still.png"
    source.write_bytes(b"still-image")

    plan = build_sequence_plan([detected(source, 0, None)], "comp.####")

    assert not plan.can_execute
    assert plan.items[0].status is PlanStatus.ERROR
    assert "never numbers files from their selection order" in plan.items[0].message


def test_sequence_mode_blocks_detection_warnings(tmp_path: Path) -> None:
    source = tmp_path / "ambiguous.1001.exr"
    source.write_bytes(b"frame")
    item = replace(
        detected(source, 0, 1001, "1001"),
        detection_warning="The frame token is ambiguous.",
    )

    plan = build_sequence_plan([item], "comp.####")

    assert not plan.can_execute
    assert "frame token is ambiguous" in plan.items[0].message


def test_find_replace_blocks_detection_warnings(tmp_path: Path) -> None:
    source = tmp_path / "ambiguous.1001.part1.exr"
    source.write_bytes(b"frame")
    item = replace(
        detected(source, 0, 1, "1"),
        detection_warning="The frame token is ambiguous.",
    )

    plan = build_replace_plan([item], "1001", "2002")

    assert not plan.can_execute
    assert plan.items[0].status is PlanStatus.ERROR
    assert plan.items[0].target == source
    assert "frame token is ambiguous" in plan.items[0].message


def test_global_pattern_blocks_multiple_sequences_in_one_folder(
    tmp_path: Path,
) -> None:
    beauty = tmp_path / "beauty.1001.exr"
    matte = tmp_path / "matte.1003.exr"
    beauty.write_bytes(b"beauty")
    matte.write_bytes(b"matte")
    files = [
        replace(detected(beauty, 0, 1001, "1001"), sequence_key="beauty"),
        replace(detected(matte, 1, 1003, "1003"), sequence_key="matte"),
    ]

    plan = build_sequence_plan(files, "comp.####")

    assert not plan.can_execute
    assert all(item.status is PlanStatus.ERROR for item in plan.items)
    assert all("could merge them" in item.message for item in plan.items)


def test_distinct_sequences_in_distinct_folders_remain_safe(tmp_path: Path) -> None:
    beauty_directory = tmp_path / "beauty"
    matte_directory = tmp_path / "matte"
    beauty_directory.mkdir()
    matte_directory.mkdir()
    beauty = beauty_directory / "beauty.1001.exr"
    matte = matte_directory / "matte.1003.exr"
    beauty.write_bytes(b"beauty")
    matte.write_bytes(b"matte")
    files = [
        replace(detected(beauty, 0, 1001, "1001"), sequence_key="beauty"),
        replace(detected(matte, 1, 1003, "1003"), sequence_key="matte"),
    ]

    plan = build_sequence_plan(files, "comp.####")

    assert plan.can_execute
    assert [item.target for item in plan.items] == [
        beauty_directory / "comp.1001.exr",
        matte_directory / "comp.1003.exr",
    ]


@pytest.mark.parametrize("replacement", ["bad/name", "bad\\name"])
def test_find_replace_path_separators_are_preview_errors(
    tmp_path: Path,
    replacement: str,
) -> None:
    source = tmp_path / "plate.1001.exr"
    source.write_bytes(b"frame")

    plan = build_replace_plan(
        [detected(source, 0, 1001, "1001")],
        "plate",
        replacement,
    )

    assert not plan.can_execute
    assert plan.items[0].status is PlanStatus.ERROR
    assert "Windows-reserved character" in plan.items[0].message
