from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from sequence_renamer.app import (
    InstanceAlreadyRunningError,
    InstanceLockError,
    _acquire_instance_lock,
)
from sequence_renamer.models import (
    DetectedFile,
    PlanStatus,
    RenamePlan,
    RenamePlanItem,
    SequenceGroup,
)
from sequence_renamer.settings import AppSettings, SettingsStore
from sequence_renamer.transaction import TransactionResult, TransactionStatus
from sequence_renamer.ui import MainWindow


class FakeTransactionManager:
    def __init__(self, *, can_undo: bool = False) -> None:
        self.can_undo = can_undo

    def recover_incomplete(self) -> tuple[TransactionResult, ...]:
        return ()

    def execute(self, plan: RenamePlan) -> TransactionResult:
        return TransactionResult(
            status=TransactionStatus.COMPLETED,
            message="Rename completed.",
            completed=plan.ready_items,
        )

    def undo_last(self) -> TransactionResult:
        return TransactionResult(
            status=TransactionStatus.NOTHING_TO_UNDO,
            message="There is no completed rename transaction to undo.",
        )


@pytest.fixture(scope="session")
def application() -> QApplication:
    return QApplication.instance() or QApplication(["sequence-renamer-test"])


@pytest.fixture
def window(tmp_path: Path, application: QApplication) -> MainWindow:
    result = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings"),
        transaction_manager=FakeTransactionManager(),
    )
    yield result
    if result._busy:
        result._set_busy(False)
    result.close()
    result.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()


def _ready_plan(source: Path, target: Path) -> RenamePlan:
    item = RenamePlanItem(
        source=source,
        target=target,
        selection_index=0,
        frame_number=1001,
        status=PlanStatus.READY,
    )
    return RenamePlan(items=(item,))


def test_start_rename_rebuilds_and_captures_latest_preview(
    tmp_path: Path,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = [tmp_path / "shot.1001.exr", tmp_path / "shot.1002.exr"]
    for source in sources:
        source.write_bytes(b"frame")
    window._add_paths(sources)
    stale_plan = window.current_plan

    window.pattern_edit.setText("fresh.####")
    assert window.preview_timer.isActive()
    window.current_plan = stale_plan

    captured: dict[str, object] = {}
    monkeypatch.setattr(window, "_confirm_action", lambda **kwargs: True)
    monkeypatch.setattr(
        window,
        "_begin_operation",
        lambda operation, plan=None: captured.update(operation=operation, plan=plan),
    )

    window._start_rename()

    plan = captured["plan"]
    assert isinstance(plan, RenamePlan)
    assert captured["operation"] == "rename"
    assert not window.preview_timer.isActive()
    assert [item.target.name for item in plan.ready_items] == [
        "fresh.1001.exr",
        "fresh.1002.exr",
    ]


def test_completion_uses_active_plan_not_mutable_preview(
    tmp_path: Path,
    window: MainWindow,
) -> None:
    source = tmp_path / "source.1001.exr"
    executed_target = tmp_path / "executed.1001.exr"
    preview_target = tmp_path / "later-preview.1001.exr"
    source.write_bytes(b"frame")

    executed_plan = _ready_plan(source, executed_target)
    window.source_paths = [source]
    window.current_plan = _ready_plan(source, preview_target)
    window._active_plan = executed_plan
    window._operation_kind = "rename"
    window._operation_result = TransactionResult(
        status=TransactionStatus.COMPLETED,
        message="Rename completed.",
        completed=executed_plan.ready_items,
    )
    window._set_busy(True)

    window._operation_thread_finished()

    assert window.source_paths == [executed_target]
    assert window.source_paths != [preview_target]
    assert not window._busy


def test_worker_result_does_not_clear_busy_before_thread_finishes(
    window: MainWindow,
) -> None:
    result = TransactionResult(
        status=TransactionStatus.COMPLETED,
        message="Rename completed.",
    )
    window._set_busy(True)

    window._capture_operation_result(result)

    assert window._busy
    assert not window.drop_zone.isEnabled()
    assert not window.drop_zone.acceptDrops()
    window._set_busy(False)


def test_busy_window_rejects_every_source_mutation(
    tmp_path: Path,
    window: MainWindow,
) -> None:
    original = tmp_path / "original.1001.png"
    incoming = tmp_path / "incoming.1002.png"
    original.write_bytes(b"original")
    incoming.write_bytes(b"incoming")
    window._add_paths([original])
    before = list(window.source_paths)
    window._set_busy(True)

    window._add_paths([incoming])
    window._add_dropped_paths([str(incoming)])
    window._clear_sources()

    assert window.source_paths == before
    assert not window.drop_zone.isEnabled()
    assert not window.drop_zone.acceptDrops()
    window._set_busy(False)
    assert window.drop_zone.isEnabled()
    assert window.drop_zone.acceptDrops()


def test_undo_is_dispatched_as_an_async_operation(
    tmp_path: Path,
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = FakeTransactionManager(can_undo=True)
    undo_window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings"),
        transaction_manager=manager,
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(undo_window, "_confirm_action", lambda **kwargs: True)
    monkeypatch.setattr(
        undo_window,
        "_begin_operation",
        lambda operation, plan=None: captured.update(operation=operation, plan=plan),
    )

    undo_window._undo_last()

    assert captured == {"operation": "undo", "plan": None}
    undo_window.close()
    undo_window.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()


def test_preference_write_failure_does_not_block_import(
    tmp_path: Path,
    application: QApplication,
) -> None:
    class FailingSettingsStore:
        def load(self) -> AppSettings:
            return AppSettings()

        def remember_directory(self, settings: AppSettings, directory: Path) -> None:
            raise OSError("read-only settings directory")

    settings = FailingSettingsStore()
    import_window = MainWindow(
        settings_store=settings,  # type: ignore[arg-type]
        transaction_manager=FakeTransactionManager(),
    )
    source = tmp_path / "plate.1001.exr"
    source.write_bytes(b"frame")

    import_window._remember_directory(tmp_path)
    import_window._add_paths([source])

    assert import_window.source_paths == [source]
    import_window.close()
    import_window.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()


def test_summary_reports_large_sequence_gap_without_materializing_it(
    window: MainWindow,
) -> None:
    first = DetectedFile(
        path=Path("plate.000000.exr"),
        selection_index=0,
        frame_number=0,
        sequence_key="plate|.exr",
    )
    last = DetectedFile(
        path=Path("plate.200000.exr"),
        selection_index=1,
        frame_number=200_000,
        sequence_key="plate|.exr",
    )
    window.source_paths = [first.path, last.path]
    window.sequence_groups = (
        SequenceGroup(
            key="plate|.exr",
            directory=Path("."),
            prefix="plate.",
            suffix="",
            extension=".exr",
            files=(first, last),
        ),
    )

    window._update_summary()

    assert "199999 missing frames" in window.summary_label.text()


def test_single_instance_lock_rejects_a_second_live_owner(tmp_path: Path) -> None:
    first = _acquire_instance_lock(tmp_path)
    try:
        assert first.staleLockTime() == 0
        with pytest.raises(InstanceAlreadyRunningError):
            _acquire_instance_lock(tmp_path)
    finally:
        first.unlock()

    replacement = _acquire_instance_lock(tmp_path)
    replacement.unlock()


def test_single_instance_lock_fails_safely_for_unusable_directory(
    tmp_path: Path,
) -> None:
    not_a_directory = tmp_path / "blocked"
    not_a_directory.write_text("file", encoding="utf-8")

    with pytest.raises(InstanceLockError):
        _acquire_instance_lock(not_a_directory)
