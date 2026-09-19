"""Filesystem-level tests for the complete frame-preserving rename pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from sequence_renamer.planner import build_replace_plan, build_sequence_plan
from sequence_renamer.sequences import detect_sequences
from sequence_renamer.transaction import TransactionManager, TransactionStatus


def _write_binary(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    return path


def test_exr_hash_pipeline_preserves_frames_gaps_bytes_and_selection_order(
    tmp_path: Path,
) -> None:
    source_directory = tmp_path / "plates"
    source_directory.mkdir()
    sources = [
        _write_binary(source_directory / "plate.1004.EXR", b"exr-frame-1004"),
        _write_binary(source_directory / "plate.1001.EXR", b"exr-frame-1001"),
        _write_binary(source_directory / "plate.1002.EXR", b"exr-frame-1002"),
    ]
    detected, groups = detect_sequences(sources)

    assert [item.frame_number for item in detected] == [1004, 1001, 1002]
    assert groups[0].frames == (1001, 1002, 1004)
    assert groups[0].missing_frames == (1003,)

    plan = build_sequence_plan(detected, "comp.####")

    assert plan.can_execute
    assert [item.target.name for item in plan.items] == [
        "comp.1004.EXR",
        "comp.1001.EXR",
        "comp.1002.EXR",
    ]

    manager = TransactionManager(tmp_path / "journals")
    renamed = manager.execute(plan)

    assert renamed.status is TransactionStatus.COMPLETED
    assert (source_directory / "comp.1004.EXR").read_bytes() == b"exr-frame-1004"
    assert (source_directory / "comp.1001.EXR").read_bytes() == b"exr-frame-1001"
    assert (source_directory / "comp.1002.EXR").read_bytes() == b"exr-frame-1002"
    assert not (source_directory / "comp.1003.EXR").exists()

    undone = manager.undo_last()

    assert undone.status is TransactionStatus.UNDONE
    assert [path.read_bytes() for path in sources] == [
        b"exr-frame-1004",
        b"exr-frame-1001",
        b"exr-frame-1002",
    ]


@pytest.mark.parametrize("frame_token", ["%04d", "%04"])
def test_png_printf_pipeline_supports_unicode_paths_and_undo(
    tmp_path: Path,
    frame_token: str,
) -> None:
    source_directory = tmp_path / "Données_équipe_東京"
    source_directory.mkdir()
    sources = [
        _write_binary(source_directory / "planète_東京.25.PNG", b"png-frame-25"),
        _write_binary(source_directory / "planète_東京.7.PNG", b"png-frame-7"),
    ]
    detected, groups = detect_sequences(sources)

    assert len(groups) == 1
    assert [item.frame_number for item in detected] == [25, 7]

    plan = build_sequence_plan(detected, f"précomp_é.{frame_token}")

    assert plan.can_execute
    assert [item.target.name for item in plan.items] == [
        "précomp_é.0025.PNG",
        "précomp_é.0007.PNG",
    ]

    manager = TransactionManager(tmp_path / "journaux")
    renamed = manager.execute(plan)

    assert renamed.status is TransactionStatus.COMPLETED
    assert (source_directory / "précomp_é.0025.PNG").read_bytes() == b"png-frame-25"
    assert (source_directory / "précomp_é.0007.PNG").read_bytes() == b"png-frame-7"
    assert renamed.journal_path is not None
    journal_text = renamed.journal_path.read_text(encoding="utf-8")
    assert "Données_équipe_東京" in journal_text
    assert "précomp_é.0025.PNG" in journal_text

    undone = manager.undo_last()

    assert undone.status is TransactionStatus.UNDONE
    assert [path.read_bytes() for path in sources] == [
        b"png-frame-25",
        b"png-frame-7",
    ]


def test_real_detection_offsets_protect_only_the_frame_during_replace(
    tmp_path: Path,
) -> None:
    sources = [
        _write_binary(
            tmp_path / "shot1001_take.1002_v003.exr",
            b"frame-1002",
        ),
        _write_binary(
            tmp_path / "shot1001_take.1001_v003.exr",
            b"frame-1001",
        ),
    ]
    detected, groups = detect_sequences(sources)

    assert len(groups) == 1
    assert [item.frame_text for item in detected] == ["1002", "1001"]
    assert all(
        item.path.stem[item.frame_start : item.frame_end] == item.frame_text
        for item in detected
        if item.frame_start is not None and item.frame_end is not None
    )

    plan = build_replace_plan(detected, "1001", "2002")

    assert plan.can_execute
    assert [item.target.name for item in plan.items] == [
        "shot2002_take.1002_v003.exr",
        "shot2002_take.1001_v003.exr",
    ]

    manager = TransactionManager(tmp_path / "journals")
    renamed = manager.execute(plan)

    assert renamed.status is TransactionStatus.COMPLETED
    assert (tmp_path / "shot2002_take.1002_v003.exr").read_bytes() == b"frame-1002"
    assert (tmp_path / "shot2002_take.1001_v003.exr").read_bytes() == b"frame-1001"
    assert manager.undo_last().status is TransactionStatus.UNDONE
    assert [path.read_bytes() for path in sources] == [b"frame-1002", b"frame-1001"]


def test_multiple_detected_sequences_in_one_folder_are_blocked_end_to_end(
    tmp_path: Path,
) -> None:
    sources = [
        _write_binary(tmp_path / "beauty.1001.exr", b"beauty-1001"),
        _write_binary(tmp_path / "beauty.1002.exr", b"beauty-1002"),
        _write_binary(tmp_path / "matte.1003.exr", b"matte-1003"),
        _write_binary(tmp_path / "matte.1004.exr", b"matte-1004"),
    ]
    detected, groups = detect_sequences(sources)

    assert len(groups) == 2
    plan = build_sequence_plan(detected, "comp.####")

    assert not plan.can_execute
    result = TransactionManager(tmp_path / "journals").execute(plan)
    assert result.status is TransactionStatus.INVALID_PLAN
    assert [path.read_bytes() for path in sources] == [
        b"beauty-1001",
        b"beauty-1002",
        b"matte-1003",
        b"matte-1004",
    ]
    assert not (tmp_path / "journals").exists()
