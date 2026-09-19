from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import sequence_renamer.transaction as transaction_module
from sequence_renamer.models import PlanStatus, RenamePlan, RenamePlanItem
from sequence_renamer.transaction import (
    TransactionManager,
    TransactionResult,
    TransactionStatus,
)


def _plan(*mappings: tuple[Path, Path]) -> RenamePlan:
    return RenamePlan(
        items=tuple(
            RenamePlanItem(
                source=source,
                target=target,
                selection_index=index,
                frame_number=1001 + index,
                status=PlanStatus.READY,
            )
            for index, (source, target) in enumerate(mappings)
        )
    )


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _transaction_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("transaction-*.json"))


def _assert_no_engine_temporary_files(directory: Path) -> None:
    assert not list(directory.glob(".sequence-renamer-*.tmp"))


def test_success_writes_atomic_journal_and_returns_final_paths(tmp_path: Path) -> None:
    files = tmp_path / "files"
    journals = tmp_path / "journals"
    files.mkdir()
    source_a = _write(files / "plate.1001.exr", "frame-1001")
    source_b = _write(files / "plate.1002.exr", "frame-1002")
    target_a = files / "comp.1001.exr"
    target_b = files / "comp.1002.exr"

    manager = TransactionManager(journals)
    result = manager.execute(_plan((source_a, target_a), (source_b, target_b)))

    assert result.status is TransactionStatus.COMPLETED
    assert result.success
    assert result.completed_count == 2
    assert result.final_paths == (target_a, target_b)
    assert target_a.read_text(encoding="utf-8") == "frame-1001"
    assert target_b.read_text(encoding="utf-8") == "frame-1002"
    assert not source_a.exists()
    assert not source_b.exists()
    assert manager.can_undo
    _assert_no_engine_temporary_files(files)

    journal_paths = _transaction_files(journals)
    assert len(journal_paths) == 1
    journal = json.loads(journal_paths[0].read_text(encoding="utf-8"))
    assert journal["status"] == "completed"
    assert [entry["state"] for entry in journal["entries"]] == ["target", "target"]
    assert not list(journals.glob("*.tmp"))


def test_two_phase_rename_handles_a_three_file_cycle(tmp_path: Path) -> None:
    journals = tmp_path / "journals"
    source_a = _write(tmp_path / "a.exr", "A")
    source_b = _write(tmp_path / "b.exr", "B")
    source_c = _write(tmp_path / "c.exr", "C")

    result = TransactionManager(journals).execute(
        _plan(
            (source_a, source_b),
            (source_b, source_c),
            (source_c, source_a),
        )
    )

    assert result.status is TransactionStatus.COMPLETED
    assert source_a.read_text(encoding="utf-8") == "C"
    assert source_b.read_text(encoding="utf-8") == "A"
    assert source_c.read_text(encoding="utf-8") == "B"
    _assert_no_engine_temporary_files(tmp_path)


def test_target_that_appears_during_staging_is_never_overwritten(
    tmp_path: Path,
) -> None:
    journals = tmp_path / "journals"
    source_a = _write(tmp_path / "a.png", "A")
    source_b = _write(tmp_path / "b.png", "B")
    target_a = tmp_path / "renamed-a.png"
    target_b = tmp_path / "renamed-b.png"
    calls = 0

    def create_collision_after_first_move(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        os.rename(source, target)
        if calls == 1:
            target_a.write_text("unrelated", encoding="utf-8")

    result = TransactionManager(
        journals,
        rename_func=create_collision_after_first_move,
    ).execute(_plan((source_a, target_a), (source_b, target_b)))

    assert result.status is TransactionStatus.ROLLED_BACK
    assert not result.success
    assert source_a.read_text(encoding="utf-8") == "A"
    assert source_b.read_text(encoding="utf-8") == "B"
    assert target_a.read_text(encoding="utf-8") == "unrelated"
    assert not target_b.exists()
    assert any("Target appeared" in error for error in result.errors)
    _assert_no_engine_temporary_files(tmp_path)


def test_staging_failure_restores_every_source(tmp_path: Path) -> None:
    journals = tmp_path / "journals"
    source_a = _write(tmp_path / "a.exr", "A")
    source_b = _write(tmp_path / "b.exr", "B")
    target_a = tmp_path / "x.exr"
    target_b = tmp_path / "y.exr"
    calls = 0

    def fail_second_move(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated staging failure")
        os.rename(source, target)

    result = TransactionManager(journals, rename_func=fail_second_move).execute(
        _plan((source_a, target_a), (source_b, target_b))
    )

    assert result.status is TransactionStatus.ROLLED_BACK
    assert source_a.read_text(encoding="utf-8") == "A"
    assert source_b.read_text(encoding="utf-8") == "B"
    assert not target_a.exists()
    assert not target_b.exists()
    assert "simulated staging failure" in "\n".join(result.errors)
    _assert_no_engine_temporary_files(tmp_path)


def test_commit_failure_after_partial_commit_restores_exact_contents(
    tmp_path: Path,
) -> None:
    journals = tmp_path / "journals"
    source_a = _write(tmp_path / "a.exr", "A")
    source_b = _write(tmp_path / "b.exr", "B")
    target_a = tmp_path / "x.exr"
    target_b = tmp_path / "y.exr"
    calls = 0

    def fail_second_commit(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("simulated commit failure")
        os.rename(source, target)

    result = TransactionManager(journals, rename_func=fail_second_commit).execute(
        _plan((source_a, target_a), (source_b, target_b))
    )

    assert result.status is TransactionStatus.ROLLED_BACK
    assert source_a.read_text(encoding="utf-8") == "A"
    assert source_b.read_text(encoding="utf-8") == "B"
    assert not target_a.exists()
    assert not target_b.exists()
    assert "simulated commit failure" in "\n".join(result.errors)
    _assert_no_engine_temporary_files(tmp_path)


def test_successful_transaction_can_be_undone_once(tmp_path: Path) -> None:
    journals = tmp_path / "journals"
    source_a = _write(tmp_path / "plate.1001.exr", "A")
    source_b = _write(tmp_path / "plate.1002.exr", "B")
    target_a = tmp_path / "comp.1001.exr"
    target_b = tmp_path / "comp.1002.exr"
    manager = TransactionManager(journals)

    execute_result = manager.execute(_plan((source_a, target_a), (source_b, target_b)))
    undo_result = manager.undo_last()

    assert execute_result.status is TransactionStatus.COMPLETED
    assert undo_result.status is TransactionStatus.UNDONE
    assert undo_result.success
    assert undo_result.final_paths == (source_a, source_b)
    assert source_a.read_text(encoding="utf-8") == "A"
    assert source_b.read_text(encoding="utf-8") == "B"
    assert not target_a.exists()
    assert not target_b.exists()
    assert not manager.can_undo
    assert manager.undo_last().status is TransactionStatus.NOTHING_TO_UNDO
    _assert_no_engine_temporary_files(tmp_path)


def test_incomplete_rollback_can_be_recovered_on_next_start(tmp_path: Path) -> None:
    journals = tmp_path / "journals"
    source_a = _write(tmp_path / "a.exr", "A")
    source_b = _write(tmp_path / "b.exr", "B")
    target_a = tmp_path / "x.exr"
    target_b = tmp_path / "y.exr"
    calls = 0

    def fail_commit_and_first_rollback_move(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls in {4, 5}:
            raise OSError(f"simulated failure {calls}")
        os.rename(source, target)

    failed = TransactionManager(
        journals,
        rename_func=fail_commit_and_first_rollback_move,
    ).execute(_plan((source_a, target_a), (source_b, target_b)))

    assert failed.status is TransactionStatus.ROLLBACK_FAILED
    recovered = TransactionManager(journals).recover_incomplete()

    assert len(recovered) == 1
    assert recovered[0].status is TransactionStatus.RECOVERED
    assert recovered[0].success
    assert source_a.read_text(encoding="utf-8") == "A"
    assert source_b.read_text(encoding="utf-8") == "B"
    assert not target_a.exists()
    assert not target_b.exists()
    _assert_no_engine_temporary_files(tmp_path)


class _SimulatedProcessCrash(BaseException):
    """Bypass the in-process exception rollback like a terminated process."""


def test_staging_crash_with_recreated_source_never_reports_false_success(
    tmp_path: Path,
) -> None:
    journals = tmp_path / "journals"
    source = _write(tmp_path / "plate.1001.exr", "managed-original")
    target = tmp_path / "comp.1001.exr"

    def crash_after_move(move_source: Path, move_target: Path) -> None:
        os.rename(move_source, move_target)
        raise _SimulatedProcessCrash

    with pytest.raises(_SimulatedProcessCrash):
        TransactionManager(journals, rename_func=crash_after_move).execute(
            _plan((source, target))
        )

    staged = list(tmp_path.glob(".sequence-renamer-stage-*.tmp"))
    assert len(staged) == 1
    assert staged[0].read_text(encoding="utf-8") == "managed-original"
    source.write_text("foreign-replacement", encoding="utf-8")

    recovered = TransactionManager(journals).recover_incomplete()

    assert len(recovered) == 1
    assert recovered[0].status is TransactionStatus.RECOVERY_FAILED
    assert not recovered[0].success
    assert source.read_text(encoding="utf-8") == "foreign-replacement"
    assert not target.exists()
    managed_locations = [
        path
        for path in tmp_path.glob(".sequence-renamer-*.tmp")
        if path.read_text(encoding="utf-8") == "managed-original"
    ]
    assert len(managed_locations) == 1
    assert any("Refusing to overwrite" in error for error in recovered[0].errors)


def test_crash_after_staging_move_is_recovered_from_persisted_intent(
    tmp_path: Path,
) -> None:
    journals = tmp_path / "journals"
    source = _write(tmp_path / "plate.1001.exr", "frame")
    target = tmp_path / "comp.1001.exr"

    def crash_after_move(move_source: Path, move_target: Path) -> None:
        os.rename(move_source, move_target)
        raise _SimulatedProcessCrash

    with pytest.raises(_SimulatedProcessCrash):
        TransactionManager(journals, rename_func=crash_after_move).execute(
            _plan((source, target))
        )

    recovered = TransactionManager(journals).recover_incomplete()

    assert [result.status for result in recovered] == [TransactionStatus.RECOVERED]
    assert source.read_text(encoding="utf-8") == "frame"
    assert not target.exists()
    _assert_no_engine_temporary_files(tmp_path)


def test_crash_after_commit_move_is_recovered_from_persisted_intent(
    tmp_path: Path,
) -> None:
    journals = tmp_path / "journals"
    source = _write(tmp_path / "plate.1001.exr", "frame")
    target = tmp_path / "comp.1001.exr"
    calls = 0

    def crash_after_commit(move_source: Path, move_target: Path) -> None:
        nonlocal calls
        calls += 1
        os.rename(move_source, move_target)
        if calls == 2:
            raise _SimulatedProcessCrash

    with pytest.raises(_SimulatedProcessCrash):
        TransactionManager(journals, rename_func=crash_after_commit).execute(
            _plan((source, target))
        )

    assert target.read_text(encoding="utf-8") == "frame"
    recovered = TransactionManager(journals).recover_incomplete()

    assert [result.status for result in recovered] == [TransactionStatus.RECOVERED]
    assert source.read_text(encoding="utf-8") == "frame"
    assert not target.exists()
    _assert_no_engine_temporary_files(tmp_path)


class _CountingTransactionManager(TransactionManager):
    def __init__(self, journal_dir: Path) -> None:
        super().__init__(journal_dir)
        self.snapshot_writes = 0
        self.wal_writes = 0
        self.bytes_written = 0

    def _atomic_write_snapshot(
        self,
        path: Path,
        journal: dict[str, object],
    ) -> None:
        self.snapshot_writes += 1
        self.bytes_written += len(
            (json.dumps(journal, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        )
        super()._atomic_write_snapshot(path, journal)

    def _append_wal_event(self, path: Path, event: dict[str, object]) -> None:
        self.wal_writes += 1
        self.bytes_written += len(
            (
                json.dumps(
                    event,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
        )
        super()._append_wal_event(path, event)


def _measure_journal_writes(root: Path, file_count: int) -> _CountingTransactionManager:
    files = root / "files"
    files.mkdir(parents=True)
    mappings: list[tuple[Path, Path]] = []
    for frame in range(file_count):
        source = _write(files / f"plate.{frame:04d}.exr", f"frame-{frame}")
        mappings.append((source, files / f"comp.{frame:04d}.exr"))
    manager = _CountingTransactionManager(root / "journals")

    result = manager.execute(_plan(*mappings))

    assert result.status is TransactionStatus.COMPLETED
    return manager


def test_journal_write_count_and_volume_scale_linearly(tmp_path: Path) -> None:
    small_count = 12
    large_count = 48
    small = _measure_journal_writes(tmp_path / "small", small_count)
    large = _measure_journal_writes(tmp_path / "large", large_count)

    assert small.snapshot_writes == 2
    assert large.snapshot_writes == 2
    assert small.wal_writes <= 2 * small_count + 4
    assert large.wal_writes <= 2 * large_count + 4
    assert small.journal_entries_compared == 2 * small_count
    assert large.journal_entries_compared == 2 * large_count
    assert large.journal_entries_compared == 4 * small.journal_entries_compared
    assert large.bytes_written < small.bytes_written * 6
    assert large.bytes_written < 20_000 + 5_000 * large_count


def test_undo_refuses_entire_transaction_when_one_target_was_replaced(
    tmp_path: Path,
) -> None:
    journals = tmp_path / "journals"
    source_a = _write(tmp_path / "plate.1001.exr", "managed-a")
    source_b = _write(tmp_path / "plate.1002.exr", "managed-b")
    target_a = tmp_path / "comp.1001.exr"
    target_b = tmp_path / "comp.1002.exr"
    manager = TransactionManager(journals)
    renamed = manager.execute(_plan((source_a, target_a), (source_b, target_b)))
    assert renamed.status is TransactionStatus.COMPLETED
    journal_count = len(_transaction_files(journals))

    target_b.unlink()
    target_b.write_text("external-replacement-with-different-data", encoding="utf-8")
    undone = manager.undo_last()

    assert undone.status is TransactionStatus.INVALID_PLAN
    assert not undone.success
    assert "target files changed" in undone.message
    assert any("replaced or modified" in error for error in undone.errors)
    assert not source_a.exists()
    assert not source_b.exists()
    assert target_a.read_text(encoding="utf-8") == "managed-a"
    assert target_b.read_text(encoding="utf-8") == (
        "external-replacement-with-different-data"
    )
    assert len(_transaction_files(journals)) == journal_count
    _assert_no_engine_temporary_files(tmp_path)


def test_source_fingerprint_failure_returns_failed_before_any_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journals = tmp_path / "journals"
    source_a = _write(tmp_path / "plate.1001.exr", "frame-a")
    source_b = _write(tmp_path / "plate.1002.exr", "frame-b")
    target_a = tmp_path / "comp.1001.exr"
    target_b = tmp_path / "comp.1002.exr"
    real_fingerprint = transaction_module._file_fingerprint

    def fail_second_fingerprint(path: Path) -> dict[str, int]:
        if Path(path) == source_b:
            raise PermissionError("simulated metadata access failure")
        return real_fingerprint(path)

    monkeypatch.setattr(
        transaction_module,
        "_file_fingerprint",
        fail_second_fingerprint,
    )
    result = TransactionManager(journals).execute(
        _plan((source_a, target_a), (source_b, target_b))
    )

    assert result.status is TransactionStatus.FAILED
    assert not result.success
    assert "no files were changed" in result.message
    assert "simulated metadata access failure" in "\n".join(result.errors)
    assert source_a.read_text(encoding="utf-8") == "frame-a"
    assert source_b.read_text(encoding="utf-8") == "frame-b"
    assert not target_a.exists()
    assert not target_b.exists()
    assert not _transaction_files(journals)
    _assert_no_engine_temporary_files(tmp_path)


def test_failure_factory_is_ui_compatible() -> None:
    result = TransactionResult.failure("Unexpected controller error")

    assert result.status is TransactionStatus.FAILED
    assert not result.success
    assert result.errors == ("Unexpected controller error",)


@pytest.mark.parametrize("existing_content", ["external", "do-not-touch"])
def test_preexisting_target_blocks_transaction_without_mutation(
    tmp_path: Path,
    existing_content: str,
) -> None:
    source = _write(tmp_path / "source.png", "source")
    target = _write(tmp_path / "target.png", existing_content)

    result = TransactionManager(tmp_path / "journals").execute(_plan((source, target)))

    assert result.status is TransactionStatus.INVALID_PLAN
    assert source.read_text(encoding="utf-8") == "source"
    assert target.read_text(encoding="utf-8") == existing_content
    assert not _transaction_files(tmp_path / "journals")


def test_case_only_rename_uses_staging_and_can_be_undone(tmp_path: Path) -> None:
    journals = tmp_path / "journals"
    source = _write(tmp_path / "plate.1001.exr", "frame-1001")
    target = tmp_path / "PLATE.1001.exr"
    manager = TransactionManager(journals)

    renamed = manager.execute(_plan((source, target)))

    assert renamed.status is TransactionStatus.COMPLETED
    assert target.read_text(encoding="utf-8") == "frame-1001"
    assert target.name in {path.name for path in tmp_path.iterdir()}
    assert source.name not in {path.name for path in tmp_path.iterdir()}
    _assert_no_engine_temporary_files(tmp_path)

    undone = manager.undo_last()

    assert undone.status is TransactionStatus.UNDONE
    assert source.read_text(encoding="utf-8") == "frame-1001"
    assert source.name in {path.name for path in tmp_path.iterdir()}
    assert target.name not in {path.name for path in tmp_path.iterdir()}
    _assert_no_engine_temporary_files(tmp_path)
