from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QEnterEvent, QFont, QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QMenu,
    QRadioButton,
    QStyleOptionButton,
)

from sequence_renamer.models import RenamePlan
from sequence_renamer.settings import SettingsStore
from sequence_renamer.transaction import TransactionResult, TransactionStatus
from sequence_renamer.ui import MainWindow, RenamrDialog


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
    return QApplication.instance() or QApplication(["renamr-ui-test"])


@pytest.fixture
def window(tmp_path: Path, application: QApplication) -> MainWindow:
    result = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings"),
        transaction_manager=FakeTransactionManager(),
    )
    yield result
    result.close()
    result.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()


def test_minimal_shell_uses_custom_title_bar_and_compact_default_size(
    window: MainWindow,
) -> None:
    assert window.windowTitle() == "Renamr"
    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert window.size().width() == 840
    assert window.size().height() == 580
    assert window.minimumWidth() == 720
    assert window.minimumHeight() == 500
    assert window.title_bar.objectName() == "titleBar"
    assert window.title_bar.title_label.text() == "Renamr"
    assert window.title_bar.height() == 38
    assert "QFrame#titleBar" in window.styleSheet()
    assert "background: #F9F8F7" in window.styleSheet()
    assert "QLabel#windowTitle { color: #2C2C2B; font-weight: 700; }" in (
        window.styleSheet()
    )


def test_import_actions_are_embedded_and_revealed_in_place(
    window: MainWindow,
    application: QApplication,
) -> None:
    assert window.drop_zone.title_label.text() == "Add files or folders"
    assert window.drop_zone.hint_label.text() == "Drop here"
    assert not window.drop_zone.actions_visible
    assert window.add_files_button is window.drop_zone.add_files_button
    assert window.add_folder_button is window.drop_zone.add_folder_button
    assert window.add_files_button.text() == "Add files"
    assert window.add_folder_button.text() == "Add folder"
    assert window.add_files_button.parent().objectName() == "importActions"
    assert window.add_folder_button.parent().objectName() == "importActions"

    hover_event = QEnterEvent(
        QPointF(1, 1),
        QPointF(1, 1),
        QPointF(1, 1),
    )
    window.drop_zone.enterEvent(hover_event)
    application.processEvents()

    assert window.drop_zone.actions_visible
    assert (
        window.drop_zone.content_stack.currentWidget().objectName() == "importActions"
    )


def test_import_surface_has_no_menu_or_dotted_border(window: MainWindow) -> None:
    stylesheet = window.styleSheet().lower()

    assert window.findChildren(QMenu) == []
    assert "dashed" not in stylesheet
    assert "dotted" not in stylesheet
    assert window.findChildren(QRadioButton) == []
    assert window.acceptDrops()


def test_notion_palette_and_focus_states_are_consistent(window: MainWindow) -> None:
    stylesheet = window.styleSheet()

    assert "background: #F9F8F7" in stylesheet
    assert "background: #EEECEB" in stylesheet
    assert "color: #61605B" in stylesheet
    assert "color: #383837" in stylesheet
    assert "color: #2C2C2B" in stylesheet
    assert (
        "QFrame#titleBar {\n    background: #F9F8F7;\n    border: none;\n}"
        in stylesheet
    )
    assert "QLineEdit:focus { border-color: #D2CFCC; outline: none; }" in stylesheet
    assert "font-weight: 500" in stylesheet


def test_typographic_hierarchy_uses_medium_body_and_true_bold_emphasis(
    window: MainWindow,
) -> None:
    stylesheet = window.styleSheet()
    bold_selectors = (
        "QLabel#fieldLabel",
        "QLabel#windowTitle",
        "QPushButton#windowControl, QPushButton#windowClose",
        "QLabel#dropTitle",
        "QPushButton#importActionButton",
        "QPushButton#primaryButton",
        "QPushButton#segmentButton:checked",
        "QHeaderView::section",
        "QLabel#dialogHeading",
        'QLabel#summary[tone="success"]',
    )

    for selector in bold_selectors:
        start = stylesheet.index(selector)
        block = stylesheet[start : stylesheet.index("}", start) + 1]
        assert "font-weight: 700" in block, selector

    assert "font-weight: 600" not in stylesheet
    assert (
        "QLabel { background: transparent; color: #2C2C2B; font-weight: 500; }"
        in stylesheet
    )

    header_start = stylesheet.index("QHeaderView::section")
    header_block = stylesheet[header_start : stylesheet.index("}", header_start) + 1]
    assert "color: #61605B" in header_block


def test_import_surface_collapses_without_reordering_sources(
    tmp_path: Path,
    window: MainWindow,
) -> None:
    sources = [tmp_path / "plate.1002.exr", tmp_path / "plate.1001.exr"]
    for source in sources:
        source.write_bytes(b"frame")

    window._add_paths(sources)

    assert window.source_paths == sources
    assert window.drop_zone.height() == 48
    assert window.drop_zone.title_label.text() == "Add more files or folders"
    assert window.drop_zone.selection_label.text() == "2 files"
    assert not window.drop_zone.hint_label.isVisible()
    assert not window.clear_button.isHidden()


def test_clear_resets_sources_and_all_rename_inputs_without_changing_mode(
    tmp_path: Path,
    window: MainWindow,
    application: QApplication,
) -> None:
    source = tmp_path / "plate_preview.1001.exr"
    source.write_bytes(b"frame")
    window.pattern_edit.setText("renamed.####")
    window.find_edit.setText("preview")
    window.replace_edit.setText("final")
    window._add_paths([source])
    window.replace_mode.setChecked(True)
    window._refresh_preview()

    assert window.preview_model.rowCount() == 1
    assert window.preview_model.index(0, 1).data() == "plate_final.1001.exr"

    window.clear_button.click()
    application.processEvents()

    assert window.replace_mode.isChecked()
    assert window.mode_stack.currentIndex() == 1
    assert window.pattern_edit.text() == ""
    assert window.find_edit.text() == ""
    assert window.replace_edit.text() == ""
    assert window.source_paths == []
    assert window.detected_files == ()
    assert window.sequence_groups == ()
    assert window.current_plan.items == ()
    assert window.preview_model.rowCount() == 0
    assert window.summary_label.text() == "No files added"
    assert not window.rename_button.isEnabled()
    assert not window.preview_timer.isActive()


def test_segmented_control_supports_arrow_keys(
    window: MainWindow,
    application: QApplication,
) -> None:
    assert window.sequence_mode.isChecked()
    right = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_Right,
        Qt.KeyboardModifier.NoModifier,
    )
    application.sendEvent(window.sequence_mode, right)
    application.processEvents()

    assert window.replace_mode.isChecked()
    assert window.mode_stack.currentIndex() == 1

    left = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_Left,
        Qt.KeyboardModifier.NoModifier,
    )
    application.sendEvent(window.replace_mode, left)
    application.processEvents()

    assert window.sequence_mode.isChecked()
    assert window.mode_stack.currentIndex() == 0


def test_selected_segment_labels_fit_bold_text_and_padding(
    window: MainWindow,
    application: QApplication,
) -> None:
    window.show()
    application.processEvents()

    control = window.mode_control
    for button in (window.sequence_mode, window.replace_mode):
        button.setChecked(True)
        application.processEvents()

        option = QStyleOptionButton()
        button.initStyleOption(option)
        display_text = control._display_text(button)
        text_width = max(
            option.fontMetrics.horizontalAdvance(display_text),
            option.fontMetrics.boundingRect(display_text).width(),
        )
        required_width = (
            text_width
            + (2 * control.HORIZONTAL_PADDING)
            + (2 * control.BORDER_WIDTH)
            + control.TEXT_SAFETY_MARGIN
        )

        assert button.minimumWidth() >= required_width
        assert button.width() >= required_width


def test_segment_widths_recalculate_after_runtime_font_change(
    window: MainWindow,
    application: QApplication,
) -> None:
    window.show()
    application.processEvents()

    wider_font = QFont(window.sequence_mode.font())
    wider_font.setStretch(150)
    for button in (window.sequence_mode, window.replace_mode):
        button.setFont(wider_font)
    application.processEvents()

    control = window.mode_control
    for button in (window.sequence_mode, window.replace_mode):
        button.setChecked(True)
        application.processEvents()

        option = QStyleOptionButton()
        button.initStyleOption(option)
        display_text = control._display_text(button)
        text_width = max(
            option.fontMetrics.horizontalAdvance(display_text),
            option.fontMetrics.boundingRect(display_text).width(),
        )
        required_width = (
            text_width
            + (2 * control.HORIZONTAL_PADDING)
            + (2 * control.BORDER_WIDTH)
            + control.TEXT_SAFETY_MARGIN
        )

        assert button.minimumWidth() >= required_width
        assert button.width() >= required_width


def test_find_replace_keeps_preview_visible_before_input(
    tmp_path: Path,
    window: MainWindow,
) -> None:
    sources = [tmp_path / "preview.1001.png", tmp_path / "preview.1002.png"]
    for source in sources:
        source.write_bytes(b"frame")
    window._add_paths(sources)

    window.replace_mode.setChecked(True)
    window._refresh_preview()

    assert window.preview_model.rowCount() == 2
    assert [window.preview_model.index(row, 0).data() for row in range(2)] == [
        source.name for source in sources
    ]
    assert (
        window.preview_model.index(0, 0).data(Qt.ItemDataRole.ForegroundRole).name()
        == "#2c2c2b"
    )
    assert (
        window.preview_model.index(0, 1).data(Qt.ItemDataRole.ForegroundRole).name()
        == "#61605b"
    )
    assert [window.preview_model.index(row, 3).data() for row in range(2)] == [
        "Waiting",
        "Waiting",
    ]
    assert "waiting for input" in window.summary_label.text()
    assert not window.rename_button.isEnabled()


def test_switching_modes_keeps_the_input_area_and_table_in_place(
    window: MainWindow,
    application: QApplication,
) -> None:
    window.show()
    application.processEvents()
    sequence_stack_geometry = window.mode_stack.geometry()
    sequence_table_geometry = window.table.geometry()
    sequence_table_y = window.table.mapTo(window, QPoint(0, 0)).y()

    window.replace_mode.setChecked(True)
    application.processEvents()

    assert window.mode_stack.currentIndex() == 1
    assert window.mode_stack.height() == 78
    assert window.mode_stack.geometry() == sequence_stack_geometry
    assert window.table.geometry() == sequence_table_geometry
    assert window.table.mapTo(window, QPoint(0, 0)).y() == sequence_table_y


def test_table_and_dialog_use_minimal_non_native_presentation(
    window: MainWindow,
) -> None:
    assert not window.table.alternatingRowColors()
    assert not window.table.showGrid()
    assert window.table.parentWidget() is window.table_frame
    assert window.table_frame.objectName() == "previewTableFrame"
    assert window.table.objectName() == "previewTable"
    assert window.table.frameShape() == QFrame.Shape.NoFrame
    table_margins = window.table_frame.layout().contentsMargins()
    assert (
        table_margins.left(),
        table_margins.top(),
        table_margins.right(),
        table_margins.bottom(),
    ) == (1, 1, 1, 1)

    dialog = RenamrDialog(
        window,
        window_title="Renamr",
        heading="Rename 2 files?",
        body="Frame numbers and file extensions will be preserved.",
        confirm_text="Rename 2 files",
        cancel_text="Cancel",
    )
    assert dialog.objectName() == "renamrDialog"
    assert dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert dialog.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert dialog.panel.objectName() == "dialogPanel"
    assert dialog.panel.graphicsEffect() is not None
    assert dialog.confirm_button.text() == "Rename 2 files"


def test_documented_shortcuts_are_installed(window: MainWindow) -> None:
    assert window.open_files_shortcut.key().toString() == "Ctrl+O"
    assert window.open_folder_shortcut.key().toString() == "Ctrl+Shift+O"
    assert window.rename_shortcut.key().toString() == "Ctrl+Return"
    assert window.undo_shortcut.key().toString() == "Ctrl+Z"
