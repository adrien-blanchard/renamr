from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QAbstractItemView, QHeaderView

from sequence_renamer.models import RenamePlan
from sequence_renamer.settings import SettingsStore
from sequence_renamer.transaction import TransactionResult, TransactionStatus
from sequence_renamer.ui import MainWindow


class FakeTransactionManager:
    can_undo = False

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
    return QApplication.instance() or QApplication(["renamr-table-test"])


@pytest.fixture
def window(tmp_path: Path, application: QApplication) -> MainWindow:
    result = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings"),
        transaction_manager=FakeTransactionManager(),
    )
    result.show()
    application.processEvents()
    yield result
    result.close()
    result.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()


def _settle_layout(application: QApplication) -> None:
    application.processEvents()
    QTest.qWait(10)
    application.processEvents()


def _name_widths(window: MainWindow) -> tuple[int, int]:
    return window.table.columnWidth(0), window.table.columnWidth(1)


def _available_name_width(window: MainWindow) -> int:
    header = window.table.horizontalHeader()
    return header.viewport().width() - sum(
        header.sectionSize(column) for column in (2, 3)
    )


def _assert_name_columns_fill_viewport(window: MainWindow) -> None:
    assert sum(_name_widths(window)) == _available_name_width(window)
    assert window.table.horizontalScrollBar().maximum() == 0


def test_name_columns_fill_default_and_wide_windows_with_an_even_initial_split(
    window: MainWindow,
    application: QApplication,
) -> None:
    header = window.table.horizontalHeader()

    assert header.sectionResizeMode(0) is QHeaderView.ResizeMode.Fixed
    assert header.sectionResizeMode(1) is QHeaderView.ResizeMode.Fixed
    assert header.sectionResizeMode(2) is QHeaderView.ResizeMode.Fixed
    assert header.sectionResizeMode(3) is QHeaderView.ResizeMode.Fixed
    assert not header.sectionsMovable()
    assert not header.stretchLastSection()
    assert not header.toolTip()
    assert "Double-click" not in header.accessibleDescription()

    window.resize(840, 580)
    _settle_layout(application)
    default_widths = _name_widths(window)
    _assert_name_columns_fill_viewport(window)
    assert abs(default_widths[0] - default_widths[1]) <= 1
    assert min(default_widths) >= header.PREFERRED_NAME_MINIMUM

    window.resize(1500, 800)
    _settle_layout(application)
    wide_widths = _name_widths(window)
    _assert_name_columns_fill_viewport(window)
    assert abs(wide_widths[0] - wide_widths[1]) <= 1
    assert wide_widths[0] > default_widths[0]
    assert wide_widths[1] > default_widths[1]


def test_only_central_separator_is_draggable_and_its_ratio_survives_resize(
    window: MainWindow,
    application: QApplication,
) -> None:
    window.resize(840, 580)
    _settle_layout(application)
    header = window.table.horizontalHeader()
    viewport = header.viewport()
    original_widths = _name_widths(window)
    original_total = sum(original_widths)
    central_boundary = header.sectionViewportPosition(0) + original_widths[0]
    start = QPoint(central_boundary + 5, header.height() // 2)

    # Five pixels away from the painted one-pixel grip is intentionally still
    # easy to acquire; users should not need pixel-perfect aim.
    QTest.mouseMove(viewport, start)
    application.processEvents()
    assert header._hovered_boundary == 0
    assert viewport.cursor().shape() is Qt.CursorShape.SplitHCursor

    image = viewport.grab().toImage()
    grip_color = image.pixelColor(
        central_boundary - 1,
        header.height() // 2,
    ).name()
    assert grip_color in {"#b8b8b5", "#e1e1de"}

    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
    finish = QPoint(start.x() + 80, start.y())
    QTest.mouseMove(viewport, finish, delay=10)
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=finish)
    _settle_layout(application)

    dragged_widths = _name_widths(window)
    assert dragged_widths[0] == original_widths[0] + 80
    assert sum(dragged_widths) == original_total
    assert header._active_boundary is None

    # The right edge of New Name is intentionally not a resize control.
    new_name_edge = header.sectionViewportPosition(1) + header.sectionSize(1)
    QTest.mouseMove(viewport, QPoint(new_name_edge, header.height() // 2))
    application.processEvents()
    assert header._hovered_boundary is None
    assert viewport.cursor().shape() is not Qt.CursorShape.SplitHCursor

    dragged_ratio = dragged_widths[0] / original_total
    window.resize(1500, 800)
    _settle_layout(application)
    wide_widths = _name_widths(window)
    _assert_name_columns_fill_viewport(window)
    assert abs((wide_widths[0] / sum(wide_widths)) - dragged_ratio) < 0.002


def test_double_clicking_central_separator_does_not_change_column_widths(
    tmp_path: Path,
    window: MainWindow,
    application: QApplication,
) -> None:
    source = tmp_path / f"original_{'very_long_' * 7}plate.1001.exr"
    source.write_bytes(b"frame")
    window._add_paths([source])
    window.pattern_edit.setText(f"delivery_{'descriptive_' * 7}beauty.####")
    window._refresh_preview()
    _settle_layout(application)

    header = window.table.horizontalHeader()
    widths_before = _name_widths(window)
    boundary = header.sectionViewportPosition(0) + header.sectionSize(0)
    QTest.mouseDClick(
        header.viewport(),
        Qt.MouseButton.LeftButton,
        pos=QPoint(boundary, header.height() // 2),
    )
    _settle_layout(application)

    assert _name_widths(window) == widths_before
    _assert_name_columns_fill_viewport(window)


def test_large_sequence_fills_maximized_like_width_after_scrollbar_settles(
    tmp_path: Path,
    window: MainWindow,
    application: QApplication,
) -> None:
    paths: list[Path] = []
    for frame in range(1001, 1116):
        source = tmp_path / f"frere_image_rv030_0010_default_output_v003.{frame}.exr"
        source.write_bytes(b"frame")
        paths.append(source)
    window._add_paths(paths)

    window.resize(1734, 757)
    _settle_layout(application)
    # Let the 115-row model's vertical scrollbar complete its geometry pass.
    _settle_layout(application)

    header = window.table.horizontalHeader()
    widths = _name_widths(window)
    assert window.table.verticalScrollBar().maximum() > 0
    assert widths[0] > 300
    assert widths[1] > 300
    assert sum(widths) == _available_name_width(window)
    assert sum(header.sectionSize(column) for column in range(4)) == (
        header.viewport().width()
    )
    assert window.table.horizontalScrollBar().maximum() == 0


def test_unused_header_area_and_scrollbar_corner_stay_white(
    window: MainWindow,
    application: QApplication,
) -> None:
    stylesheet = window.styleSheet()
    assert "QHeaderView {\n    background: #FFFFFF;\n    border: none;\n}" in stylesheet
    assert "QTableCornerButton::section {\n    background: #FFFFFF;" in stylesheet

    application.processEvents()
    header_viewport = window.table.horizontalHeader().viewport()
    image = header_viewport.grab().toImage()
    assert (
        image.pixelColor(
            header_viewport.width() - 2,
            header_viewport.height() // 2,
        ).name()
        == "#ffffff"
    )


def test_name_split_ratio_survives_preview_and_mode_changes(
    tmp_path: Path,
    window: MainWindow,
    application: QApplication,
) -> None:
    source = tmp_path / "a_very_long_original_plate_name_v012.1001.exr"
    source.write_bytes(b"frame")
    window._add_paths([source])

    window.resize(840, 580)
    _settle_layout(application)
    header = window.table.horizontalHeader()
    viewport = header.viewport()
    initial_widths = _name_widths(window)
    boundary = header.sectionViewportPosition(0) + initial_widths[0]
    start = QPoint(boundary, header.height() // 2)
    finish = QPoint(start.x() + 70, start.y())
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(viewport, finish, delay=10)
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=finish)
    _settle_layout(application)
    chosen_ratio = window.table.columnWidth(0) / sum(_name_widths(window))

    window.pattern_edit.setText("a_very_long_delivery_name_v013.####")
    window._refresh_preview()
    _settle_layout(application)
    _assert_name_columns_fill_viewport(window)
    assert (
        abs((window.table.columnWidth(0) / sum(_name_widths(window))) - chosen_ratio)
        < 0.002
    )

    window.replace_mode.setChecked(True)
    _settle_layout(application)
    window.find_edit.setText("plate")
    window.replace_edit.setText("beauty")
    window._refresh_preview()
    _settle_layout(application)

    _assert_name_columns_fill_viewport(window)
    assert (
        abs((window.table.columnWidth(0) / sum(_name_widths(window))) - chosen_ratio)
        < 0.002
    )
    assert (
        window.table.horizontalScrollMode()
        is QAbstractItemView.ScrollMode.ScrollPerPixel
    )
    assert (
        window.table.horizontalScrollBarPolicy()
        is Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )


def test_long_original_and_new_names_are_available_in_cell_tooltips(
    tmp_path: Path,
    window: MainWindow,
) -> None:
    source = tmp_path / "original_plate_name_with_a_long_description_v012.1001.exr"
    source.write_bytes(b"frame")
    window._add_paths([source])
    window.pattern_edit.setText("renamed_delivery_with_a_long_description_v013.####")
    window._refresh_preview()

    original_tooltip = window.preview_model.index(0, 0).data(
        Qt.ItemDataRole.ToolTipRole
    )
    new_name_tooltip = window.preview_model.index(0, 1).data(
        Qt.ItemDataRole.ToolTipRole
    )

    assert source.name in original_tooltip
    assert window.preview_model.index(0, 1).data() in new_name_tooltip


def test_clear_preserves_the_responsive_name_split(
    tmp_path: Path,
    window: MainWindow,
    application: QApplication,
) -> None:
    source = tmp_path / "long_source_name_for_horizontal_scroll.1001.exr"
    source.write_bytes(b"frame")
    window._add_paths([source])

    window.resize(840, 580)
    _settle_layout(application)
    header = window.table.horizontalHeader()
    viewport = header.viewport()
    boundary = header.sectionViewportPosition(0) + header.sectionSize(0)
    start = QPoint(boundary, header.height() // 2)
    finish = QPoint(start.x() - 55, start.y())
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(viewport, finish, delay=10)
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=finish)
    _settle_layout(application)
    chosen_ratio = window.table.columnWidth(0) / sum(_name_widths(window))

    window.clear_button.click()
    _settle_layout(application)

    _assert_name_columns_fill_viewport(window)
    assert window.table.horizontalScrollBar().value() == 0
    assert (
        abs((window.table.columnWidth(0) / sum(_name_widths(window))) - chosen_ratio)
        < 0.002
    )
