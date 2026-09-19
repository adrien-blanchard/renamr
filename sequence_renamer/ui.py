"""Minimal, sequence-aware PySide6 user interface for Renamr."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Literal

from PySide6.QtCore import QEvent, QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QCloseEvent,
    QCursor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QFontMetrics,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPen,
    QResizeEvent,
    QShowEvent,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .models import DetectedFile, RenamePlan, SequenceGroup
from .planner import (
    RenameMode,
    SUPPORTED_SEQUENCE_EXTENSIONS,
    build_plan,
    canonical_path_key,
)
from .sequences import detect_sequences
from .settings import AppSettings, SettingsStore
from .table_model import RenamePreviewModel
from .transaction import TransactionManager, TransactionResult, TransactionStatus


LOGGER = logging.getLogger("sequence_renamer.ui")


APP_STYLESHEET = """
QMainWindow, QWidget#root, QDialog#renamrDialog {
    background: #FFFFFF;
    color: #2C2C2B;
    font-size: 14px;
    font-weight: 500;
}
QWidget#content { background: #FFFFFF; }
QLabel { background: transparent; color: #2C2C2B; font-weight: 500; }
QLabel#hint, QLabel#summary, QLabel#dropHint, QLabel#selectionCount,
QLabel#dialogBody { color: #61605B; }
QLabel#fieldLabel { color: #2C2C2B; font-weight: 700; }
QFrame#titleBar {
    background: #F9F8F7;
    border: none;
}
QLabel#windowTitle { color: #2C2C2B; font-weight: 700; }
QPushButton#windowControl, QPushButton#windowClose {
    min-width: 42px;
    max-width: 42px;
    min-height: 37px;
    max-height: 37px;
    padding: 0;
    color: #61605B;
    background: transparent;
    border: none;
    border-radius: 0;
    font-size: 14px;
    font-weight: 700;
    outline: none;
}
QPushButton#windowControl:hover, QPushButton#windowClose:hover {
    background: #EEECEB;
    color: #383837;
}
QPushButton#windowControl:pressed, QPushButton#windowClose:pressed {
    background: #E4E1DF;
    color: #383837;
}
QFrame#dropZone {
    background: #F9F8F7;
    border: none;
    border-radius: 6px;
    outline: none;
}
QFrame#dropZone:hover, QFrame#dropZone:focus {
    background: #EEECEB;
    border: none;
    outline: none;
}
QFrame#dropZone[dragActive="true"] {
    background: #EEECEB;
    border: none;
    outline: none;
}
QWidget#importIdle, QWidget#importActions { background: transparent; }
QLabel#dropTitle { color: #61605B; font-weight: 700; }
QFrame#dropZone:hover QLabel#dropTitle,
QFrame#dropZone:focus QLabel#dropTitle,
QFrame#dropZone[dragActive="true"] QLabel#dropTitle,
QFrame#dropZone:hover QLabel#dropHint,
QFrame#dropZone:focus QLabel#dropHint,
QFrame#dropZone[dragActive="true"] QLabel#dropHint { color: #383837; }
QPushButton {
    min-height: 32px;
    color: #2C2C2B;
    background: #FFFFFF;
    border: 1px solid #E3E3E0;
    border-radius: 5px;
    padding: 0 13px;
    font-weight: 500;
    outline: none;
}
QPushButton:hover { background: #EEECEB; color: #383837; }
QPushButton:pressed { background: #E4E1DF; color: #383837; }
QPushButton:disabled { color: #A5A39F; background: #F9F8F7; }
QPushButton#importActionButton {
    min-height: 30px;
    padding: 0 14px;
    background: transparent;
    border: none;
    color: #61605B;
    font-weight: 700;
    outline: none;
}
QPushButton#importActionButton:hover {
    background: #E4E1DF;
    color: #383837;
}
QPushButton#importActionButton:focus,
QPushButton#importActionButton:pressed { border: none; outline: none; color: #383837; }
QPushButton#textButton {
    min-height: 26px;
    padding: 0 7px;
    border: none;
    background: transparent;
    color: #61605B;
}
QPushButton#textButton:hover { background: #EEECEB; color: #383837; }
QPushButton#primaryButton {
    background: #2C2C2B;
    color: #FFFFFF;
    border-color: #2C2C2B;
    font-weight: 700;
}
QPushButton#primaryButton:hover { background: #383837; border-color: #383837; }
QPushButton#primaryButton:disabled {
    background: #D8D5D2;
    color: #F9F8F7;
    border-color: #D8D5D2;
}
QFrame#segmentedControl {
    background: #F9F8F7;
    border: none;
    border-radius: 6px;
}
QPushButton#segmentButton {
    min-height: 30px;
    padding: 0 14px;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    color: #61605B;
    font-weight: 500;
    outline: none;
}
QPushButton#segmentButton:hover { color: #383837; background: #EEECEB; }
QPushButton#segmentButton:checked {
    color: #2C2C2B;
    background: #FFFFFF;
    border-color: #DDDDDA;
    font-weight: 700;
}
QPushButton#segmentButton:focus { outline: none; }
QPushButton#segmentButton:focus:!checked { border-color: transparent; }
QLineEdit {
    min-height: 34px;
    background: #FFFFFF;
    border: 1px solid #DEDEDB;
    border-radius: 5px;
    padding: 0 10px;
    color: #2C2C2B;
    selection-background-color: #D9E7FC;
    font-weight: 500;
    outline: none;
}
QLineEdit:focus { border-color: #D2CFCC; outline: none; }
QFrame#previewTableFrame {
    background: #FFFFFF;
    border: 1px solid #E7E7E4;
    border-radius: 6px;
}
QTableView#previewTable {
    background: #FFFFFF;
    alternate-background-color: #FFFFFF;
    border: none;
    border-radius: 5px;
    gridline-color: transparent;
    selection-background-color: #F9F8F7;
    selection-color: #2C2C2B;
    color: #2C2C2B;
    font-weight: 500;
    outline: 0;
}
QHeaderView {
    background: #FFFFFF;
    border: none;
}
QHeaderView::section {
    background: #FFFFFF;
    color: #61605B;
    border: none;
    border-bottom: 1px solid #E7E7E4;
    padding: 9px 8px;
    font-weight: 700;
}
QTableCornerButton::section {
    background: #FFFFFF;
    border: none;
}
QTableView::item { border-bottom: 1px solid #F0F0EE; padding: 5px 8px; }
QScrollBar:vertical {
    width: 9px;
    margin: 2px 1px 2px 0;
    background: transparent;
}
QScrollBar:horizontal {
    height: 10px;
    margin: 1px 3px 1px 3px;
    background: #F9F8F7;
}
QScrollBar::handle:vertical { background: #D1D1CE; border-radius: 4px; min-height: 28px; }
QScrollBar::handle:horizontal { background: #C9C8C4; border-radius: 4px; min-width: 36px; }
QScrollBar::handle:hover { background: #B8B8B5; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; border: none; background: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QDialog#renamrDialog { background: transparent; }
QFrame#dialogPanel {
    background: #FFFFFF;
    border: 1px solid #E9E9E7;
    border-radius: 9px;
}
QLabel#dialogHeading { color: #2C2C2B; font-size: 18px; font-weight: 700; }
QPlainTextEdit#dialogDetails {
    background: #F9F8F7;
    color: #383837;
    border: 1px solid #E2E2DF;
    border-radius: 5px;
    padding: 8px;
    font-family: "Consolas";
    font-size: 9pt;
}
QFrame#footerSeparator { background: #ECECEA; border: none; max-height: 1px; }
QLabel#summary[tone="success"] { color: #287A4D; font-weight: 700; }
"""


class DropZone(QFrame):
    """Quiet import surface that reveals its actions in place on hover."""

    paths_dropped = Signal(list)
    files_requested = Signal()
    folder_requested = Signal()
    clear_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._selection_count = 0
        self._drag_active = False
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(0)
        self._hide_timer.timeout.connect(self._hide_actions_if_idle)
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Import files or a folder")
        self.setAccessibleDescription(
            "Drop PNG or EXR files and folders here. Press Enter to choose an import action."
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 10, 8)
        layout.setSpacing(10)
        layout.addStretch(1)

        self.content_stack = QStackedWidget()
        self.content_stack.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Preferred,
        )

        idle = QWidget()
        idle.setObjectName("importIdle")
        idle_layout = QVBoxLayout(idle)
        idle_layout.setContentsMargins(0, 0, 0, 0)
        idle_layout.setSpacing(1)
        self.title_label = QLabel("Add files or folders")
        self.title_label.setObjectName("dropTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label = QLabel("Drop here")
        self.hint_label.setObjectName("dropHint")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        idle_layout.addWidget(self.title_label)
        idle_layout.addWidget(self.hint_label)

        actions = QWidget()
        actions.setObjectName("importActions")
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(8)
        self.add_files_button = QPushButton("Add files")
        self.add_folder_button = QPushButton("Add folder")
        for button in (self.add_files_button, self.add_folder_button):
            button.setObjectName("importActionButton")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_files_button.setAccessibleName("Add files")
        self.add_folder_button.setAccessibleName("Add folder")
        self.add_files_button.clicked.connect(self.files_requested)
        self.add_folder_button.clicked.connect(self.folder_requested)
        actions_layout.addWidget(self.add_files_button)
        actions_layout.addWidget(self.add_folder_button)

        self.content_stack.addWidget(idle)
        self.content_stack.addWidget(actions)
        layout.addWidget(self.content_stack)

        self.selection_label = QLabel()
        self.selection_label.setObjectName("selectionCount")
        self.selection_label.hide()
        layout.addWidget(self.selection_label)

        self.clear_button = QPushButton("Clear")
        self.clear_button.setObjectName("textButton")
        self.clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_button.setAccessibleName("Clear imported files")
        self.clear_button.hide()
        self.clear_button.clicked.connect(self.clear_requested)
        layout.addWidget(self.clear_button)
        layout.addStretch(1)
        self.set_selection_count(0)

    @property
    def actions_visible(self) -> bool:
        return self.content_stack.currentIndex() == 1

    def set_actions_visible(self, visible: bool, *, focus_first: bool = False) -> None:
        if self._drag_active or not self.isEnabled():
            visible = False
        self.content_stack.setCurrentIndex(1 if visible else 0)
        if visible and focus_first:
            self.add_files_button.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def set_selection_count(self, count: int) -> None:
        """Switch between welcoming and compact states without moving content."""

        self._selection_count = count
        compact = count > 0
        self.setProperty("compact", compact)
        self.title_label.setText(
            "Add more files or folders" if compact else "Add files or folders"
        )
        self.hint_label.setVisible(not compact)
        self.selection_label.setText(f"{count} file{'s' if count != 1 else ''}")
        self.selection_label.setVisible(compact)
        self.clear_button.setVisible(compact)
        self.setFixedHeight(48 if compact else 68)
        self.style().unpolish(self)
        self.style().polish(self)

    def enterEvent(self, event: QEvent) -> None:  # noqa: N802
        self.set_actions_visible(True)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        self._hide_timer.start()
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:  # noqa: N802, ANN001
        self.set_actions_visible(True)
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802, ANN001
        self._hide_timer.start()
        super().focusOutEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            if self.isEnabled():
                self.set_actions_visible(True, focus_first=True)
                event.accept()
                return
        super().keyPressEvent(event)

    def _hide_actions_if_idle(self) -> None:
        local_cursor = self.mapFromGlobal(QCursor.pos())
        focus_widget = QApplication.focusWidget()
        focus_inside = focus_widget is not None and (
            focus_widget is self or self.isAncestorOf(focus_widget)
        )
        if not self.rect().contains(local_cursor) and not focus_inside:
            self.set_actions_visible(False)

    def set_drag_active(self, active: bool) -> None:
        self._drag_active = active
        self.setProperty("dragActive", active)
        if active:
            self.content_stack.setCurrentIndex(0)
            self.title_label.setText("Drop to add files")
            self.hint_label.setVisible(False)
        else:
            self.title_label.setText(
                "Add more files or folders"
                if self._selection_count
                else "Add files or folders"
            )
            self.hint_label.setVisible(not self._selection_count)
            self.set_actions_visible(self.underMouse())
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if not self.isEnabled() or not self.acceptDrops():
            event.ignore()
            return
        if event.mimeData().hasUrls():
            self.set_drag_active(True)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802, ANN001
        self._clear_drag_state()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        self._clear_drag_state()
        if not self.isEnabled() or not self.acceptDrops():
            event.ignore()
            return
        paths = [
            url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()
        ]
        if paths:
            self.paths_dropped.emit(paths)
            event.acceptProposedAction()

    def _clear_drag_state(self) -> None:
        self.set_drag_active(False)


class WindowControlButton(QPushButton):
    """Font-independent, restrained window control."""

    def __init__(
        self,
        kind: Literal["minimize", "maximize", "close"],
        accessible_name: str,
    ) -> None:
        super().__init__()
        self.kind = kind
        self.maximized = False
        self.setObjectName("windowClose" if kind == "close" else "windowControl")
        self.setAccessibleName(accessible_name)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = QColor("#383837" if self.underMouse() else "#61605B")
        painter.setPen(QPen(color, 1.25))
        center = self.rect().center()
        x, y = center.x(), center.y()

        if self.kind == "minimize":
            painter.drawLine(x - 4, y + 2, x + 4, y + 2)
        elif self.kind == "close":
            painter.drawLine(x - 4, y - 4, x + 4, y + 4)
            painter.drawLine(x + 4, y - 4, x - 4, y + 4)
        elif self.maximized:
            painter.drawLine(x - 2, y - 4, x + 4, y - 4)
            painter.drawLine(x + 4, y - 4, x + 4, y + 2)
            painter.drawRect(x - 4, y - 2, 6, 6)
        else:
            painter.drawRect(x - 4, y - 4, 8, 8)


class WindowTitleBar(QFrame):
    """Minimal movable title bar for the frameless main window."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__()
        self._window = window
        self._fallback_drag_offset = None
        self.setObjectName("titleBar")
        self.setFixedHeight(38)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 0, 0)
        layout.setSpacing(0)
        self.title_label = QLabel("Renamr")
        self.title_label.setObjectName("windowTitle")
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.title_label)
        layout.addStretch(1)

        self.minimize_button = WindowControlButton("minimize", "Minimize Renamr")
        self.maximize_button = WindowControlButton("maximize", "Maximize Renamr")
        self.close_button = WindowControlButton("close", "Close Renamr")
        layout.addWidget(self.minimize_button)
        layout.addWidget(self.maximize_button)
        layout.addWidget(self.close_button)
        self.minimize_button.clicked.connect(window.showMinimized)
        self.maximize_button.clicked.connect(self.toggle_maximized)
        self.close_button.clicked.connect(window.close)

    def toggle_maximized(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()
        QTimer.singleShot(0, self.sync_window_state)

    def sync_window_state(self) -> None:
        maximized = self._window.isMaximized()
        self.maximize_button.maximized = maximized
        self.maximize_button.update()
        self.maximize_button.setAccessibleName(
            "Restore Renamr" if maximized else "Maximize Renamr"
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self._window.windowHandle()
            if handle is not None and handle.startSystemMove():
                self._fallback_drag_offset = None
            else:
                self._fallback_drag_offset = (
                    event.globalPosition().toPoint()
                    - self._window.frameGeometry().topLeft()
                )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            self._fallback_drag_offset is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and not self._window.isMaximized()
        ):
            self._window.move(
                event.globalPosition().toPoint() - self._fallback_drag_offset
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._fallback_drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class PreviewHeader(QHeaderView):
    """Responsive preview header with one draggable name-column split."""

    FIXED_SECTIONS = (2, 3)
    HANDLE_RADIUS = 8
    PREFERRED_NAME_MINIMUM = 140
    FALLBACK_NAME_MINIMUM = 64
    FRAME_COLUMN_WIDTH = 80
    STATUS_COLUMN_WIDTH = 104

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._hovered_boundary: int | None = None
        self._active_boundary: int | None = None
        self._drag_origin_x = 0
        self._drag_origin_left_width = 0
        self._drag_name_area_width = 0
        self._split_ratio = 0.5
        self._applying_name_layout = False
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.setInterval(0)
        self._layout_timer.timeout.connect(self.apply_name_layout)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setAccessibleName("Rename preview columns")
        self.setAccessibleDescription(
            "Drag the divider between Original Name and New Name to adjust "
            "their relative widths."
        )
        self.sectionResized.connect(self._section_resized)

    def _boundary_at(self, x_position: float) -> int | None:
        """Return the central name-column divider near a viewport position."""

        boundary = self.sectionViewportPosition(0) + self.sectionSize(0)
        if abs(x_position - boundary) <= self.HANDLE_RADIUS:
            return 0
        return None

    def schedule_name_layout(self) -> None:
        """Coalesce geometry/model changes into one responsive column layout."""

        self._layout_timer.start()

    def _minimum_for_name_area(self, total_width: int) -> int:
        if total_width >= self.PREFERRED_NAME_MINIMUM * 2:
            return self.PREFERRED_NAME_MINIMUM
        return self.FALLBACK_NAME_MINIMUM

    def _resize_name_sections(
        self,
        total_width: int,
        left_width: int,
        *,
        update_ratio: bool,
    ) -> None:
        """Apply two widths whose sum fills the available name-column area."""

        if update_ratio and total_width > 0:
            # Keep the requested ratio even while a narrow viewport forces a
            # temporary minimum-width clamp. It is restored when space returns.
            self._split_ratio = max(0.0, min(1.0, left_width / total_width))

        minimum = self._minimum_for_name_area(total_width)
        if total_width < minimum * 2:
            # This only occurs below the application's supported minimum width.
            # Keep both columns operable and allow safe viewport clipping
            # instead of collapsing either filename column to zero.
            left_width = minimum
            right_width = minimum
        else:
            left_width = max(minimum, min(left_width, total_width - minimum))
            right_width = total_width - left_width

        self._applying_name_layout = True
        try:
            self.resizeSection(0, left_width)
            self.resizeSection(1, right_width)
        finally:
            self._applying_name_layout = False

    @Slot()
    def apply_name_layout(self) -> None:
        """Fill the header viewport while preserving the user's split ratio."""

        if self.count() < 4 or self.viewport().width() <= 0:
            return
        fixed_width = sum(self.sectionSize(index) for index in self.FIXED_SECTIONS)
        name_area_width = max(0, self.viewport().width() - fixed_width)
        desired_left_width = round(name_area_width * self._split_ratio)
        self._resize_name_sections(
            name_area_width,
            desired_left_width,
            update_ratio=False,
        )

    @Slot(int, int, int)
    def _section_resized(
        self,
        logical_index: int,
        _old_size: int,
        _new_size: int,
    ) -> None:
        if not self._applying_name_layout and logical_index in self.FIXED_SECTIONS:
            self.schedule_name_layout()

    def _show_resize_cursor(self, visible: bool) -> None:
        if visible:
            self.viewport().setCursor(QCursor(Qt.CursorShape.SplitHCursor))
        else:
            self.viewport().unsetCursor()

    def _set_hovered_boundary(self, logical_index: int | None) -> None:
        if self._hovered_boundary == logical_index:
            return
        self._hovered_boundary = logical_index
        self.viewport().update()

    def paintSection(  # noqa: N802
        self,
        painter: QPainter,
        rect,
        logical_index: int,
    ) -> None:
        super().paintSection(painter, rect, logical_index)
        if logical_index != 0:
            return

        highlighted = logical_index in {
            self._hovered_boundary,
            self._active_boundary,
        }
        painter.save()
        pen = QPen(QColor("#B8B8B5" if highlighted else "#E1E1DE"))
        pen.setWidth(2 if highlighted else 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        x_position = rect.right()
        inset = 8 if highlighted else 11
        painter.drawLine(
            x_position,
            rect.top() + inset,
            x_position,
            rect.bottom() - inset,
        )
        painter.restore()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._active_boundary is not None:
            delta = round(event.position().x()) - self._drag_origin_x
            self._resize_name_sections(
                self._drag_name_area_width,
                self._drag_origin_left_width + delta,
                update_ratio=True,
            )
            self._show_resize_cursor(True)
            event.accept()
            return

        boundary = self._boundary_at(event.position().x())
        self._set_hovered_boundary(boundary)
        if boundary is not None:
            self._show_resize_cursor(True)
            event.accept()
            return

        super().mouseMoveEvent(event)
        self._show_resize_cursor(False)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        boundary = self._boundary_at(event.position().x())
        if event.button() == Qt.MouseButton.LeftButton and boundary is not None:
            self._active_boundary = boundary
            self._drag_origin_x = round(event.position().x())
            self._drag_origin_left_width = self.sectionSize(0)
            self._drag_name_area_width = self.sectionSize(0) + self.sectionSize(1)
            self._set_hovered_boundary(boundary)
            self._show_resize_cursor(True)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._active_boundary is not None:
            self._active_boundary = None
            self.schedule_name_layout()
            boundary = self._boundary_at(event.position().x())
            self._set_hovered_boundary(boundary)
            self._show_resize_cursor(boundary is not None)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        if self._active_boundary is None:
            self._set_hovered_boundary(None)
            self._show_resize_cursor(False)
        super().leaveEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.schedule_name_layout()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self.schedule_name_layout()


class WindowResizeHandle(QWidget):
    """Invisible edge that delegates frameless resizing to Windows."""

    def __init__(
        self,
        window: QMainWindow,
        edges: Qt.Edge,
        cursor: Qt.CursorShape,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._edges = edges
        self.setCursor(cursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            event.button() == Qt.MouseButton.LeftButton
            and not self._window.isMaximized()
        ):
            handle = self._window.windowHandle()
            if handle is not None and handle.startSystemResize(self._edges):
                event.accept()
                return
        super().mousePressEvent(event)


class SegmentedControl(QFrame):
    """Two-way mode control with radio semantics and arrow-key navigation."""

    HORIZONTAL_PADDING = 14
    BORDER_WIDTH = 1
    TEXT_SAFETY_MARGIN = 4

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("segmentedControl")
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(0)
        self._fit_timer.timeout.connect(self.ensure_selected_text_fit)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(0)

        self.sequence_button = QPushButton("Rename sequence")
        # Qt treats a single ampersand as a mnemonic marker. Doubling it keeps
        # the literal character visible in the segmented control.
        self.replace_button = QPushButton("Find && replace")
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for button, accessible_name in (
            (self.sequence_button, "Rename sequence mode"),
            (self.replace_button, "Find and replace mode"),
        ):
            button.setObjectName("segmentButton")
            button.setCheckable(True)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.setAccessibleName(accessible_name)
            button.installEventFilter(self)
            self.group.addButton(button)
            layout.addWidget(button)
        self.sequence_button.setChecked(True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    @staticmethod
    def _display_text(button: QPushButton) -> str:
        """Return button text as Qt paints it after mnemonic processing."""

        placeholder = "\0"
        return (
            button.text()
            .replace("&&", placeholder)
            .replace("&", "")
            .replace(placeholder, "&")
        )

    def ensure_selected_text_fit(self) -> None:
        """Reserve enough width for either label at the selected bold weight."""

        for button in self.group.buttons():
            # Checked-state QSS applies Bold only after polish/show. Build
            # those metrics explicitly so startup and application-font changes
            # cannot size the control from the unselected Medium text.
            selected_font = QFont(button.font())
            selected_font.setWeight(QFont.Weight.Bold)
            metrics = QFontMetrics(selected_font)
            display_text = self._display_text(button)
            text_width = max(
                metrics.horizontalAdvance(display_text),
                metrics.boundingRect(display_text).width(),
            )
            required_width = (
                text_width
                + (2 * self.HORIZONTAL_PADDING)
                + (2 * self.BORDER_WIDTH)
                + self.TEXT_SAFETY_MARGIN
            )
            if button.minimumWidth() != required_width:
                button.setMinimumWidth(required_width)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        # A style-sheet font can resolve to its final metrics only once the
        # native window and screen DPI are known.
        self.ensure_selected_text_fit()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched in self.group.buttons() and event.type() in (
            QEvent.Type.ApplicationFontChange,
            QEvent.Type.FontChange,
        ):
            # Font/style propagation reaches related buttons in separate
            # events. Recalculate once they have all adopted the new metrics.
            self._fit_timer.start()
        if event.type() == QEvent.Type.KeyPress and watched in self.group.buttons():
            assert isinstance(event, QKeyEvent)
            if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                button = (
                    self.sequence_button
                    if event.key() == Qt.Key.Key_Left
                    else self.replace_button
                )
                button.setChecked(True)
                button.setFocus()
                return True
        return super().eventFilter(watched, event)


class RenamrDialog(QDialog):
    """A restrained, icon-free dialog shared by confirmations and failures."""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        window_title: str,
        heading: str,
        body: str,
        confirm_text: str = "OK",
        cancel_text: str | None = None,
        details: str = "",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("renamrDialog")
        self.setWindowTitle(window_title)
        self.setModal(True)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumWidth(454)
        self.setAccessibleName(heading)
        self.setStyleSheet(APP_STYLESHEET)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        self.panel = QFrame()
        self.panel.setObjectName("dialogPanel")
        shadow = QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 52))
        self.panel.setGraphicsEffect(shadow)
        outer.addWidget(self.panel)

        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        heading_label = QLabel(heading)
        heading_label.setObjectName("dialogHeading")
        heading_label.setWordWrap(True)
        layout.addWidget(heading_label)

        body_label = QLabel(body)
        body_label.setObjectName("dialogBody")
        body_label.setWordWrap(True)
        body_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(body_label)

        self.details_edit: QPlainTextEdit | None = None
        if details:
            self.details_edit = QPlainTextEdit(details)
            self.details_edit.setObjectName("dialogDetails")
            self.details_edit.setReadOnly(True)
            self.details_edit.setMaximumHeight(150)
            self.details_edit.hide()
            details_button = QPushButton("Show details")
            details_button.setObjectName("textButton")
            details_button.setCheckable(True)
            details_button.setAccessibleName("Show technical details")

            def toggle_details(visible: bool) -> None:
                assert self.details_edit is not None
                self.details_edit.setVisible(visible)
                details_button.setText("Hide details" if visible else "Show details")
                self.adjustSize()

            details_button.toggled.connect(toggle_details)
            layout.addWidget(details_button, 0, Qt.AlignmentFlag.AlignLeft)
            layout.addWidget(self.details_edit)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        button_row.addStretch(1)
        if cancel_text:
            cancel_button = QPushButton(cancel_text)
            cancel_button.clicked.connect(self.reject)
            button_row.addWidget(cancel_button)
        self.confirm_button = QPushButton(confirm_text)
        self.confirm_button.setObjectName("primaryButton")
        self.confirm_button.setDefault(True)
        self.confirm_button.clicked.connect(self.accept)
        button_row.addWidget(self.confirm_button)
        layout.addLayout(button_row)

    @classmethod
    def confirm(
        cls,
        parent: QWidget,
        *,
        window_title: str,
        heading: str,
        body: str,
        confirm_text: str,
    ) -> bool:
        dialog = cls(
            parent,
            window_title=window_title,
            heading=heading,
            body=body,
            confirm_text=confirm_text,
            cancel_text="Cancel",
        )
        return dialog.exec() == QDialog.DialogCode.Accepted

    @classmethod
    def alert(
        cls,
        parent: QWidget,
        *,
        window_title: str,
        heading: str,
        body: str,
        details: str = "",
    ) -> None:
        cls(
            parent,
            window_title=window_title,
            heading=heading,
            body=body,
            details=details,
        ).exec()


OperationKind = Literal["rename", "undo"]


class TransactionWorker(QObject):
    """Run a filesystem transaction away from the Qt GUI thread."""

    finished = Signal(object)

    def __init__(
        self,
        manager: TransactionManager,
        operation: OperationKind,
        plan: RenamePlan | None = None,
    ) -> None:
        super().__init__()
        self.manager = manager
        self.operation = operation
        self.plan = plan

    @Slot()
    def run(self) -> None:
        try:
            if self.operation == "rename":
                if self.plan is None:
                    raise ValueError("A rename operation requires a captured plan.")
                result = self.manager.execute(self.plan)
            else:
                result = self.manager.undo_last()
        except Exception as error:  # defensive boundary for a windowed executable
            LOGGER.exception("Unhandled transaction worker failure")
            result = TransactionResult.failure(f"Unexpected error: {error}")
        self.finished.emit(result)


class MainWindow(QMainWindow):
    """Single-window Renamr interface."""

    def __init__(
        self,
        *,
        settings_store: SettingsStore | None = None,
        transaction_manager: TransactionManager | None = None,
    ) -> None:
        super().__init__()
        self.settings_store = settings_store or SettingsStore()
        self.settings: AppSettings = self.settings_store.load()
        self.transaction_manager = transaction_manager or TransactionManager()
        self.source_paths: list[Path] = []
        self.detected_files: tuple[DetectedFile, ...] = ()
        self.sequence_groups: tuple[SequenceGroup, ...] = ()
        self.current_plan = RenamePlan(items=())
        self._busy = False
        self._thread: QThread | None = None
        self._worker: TransactionWorker | None = None
        self._operation_kind: OperationKind | None = None
        self._active_plan: RenamePlan | None = None
        self._operation_result: TransactionResult | None = None

        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowTitle("Renamr")
        self.setMinimumSize(720, 500)
        self.resize(840, 580)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._build_ui()
        self.setStyleSheet(APP_STYLESHEET)
        self.mode_control.ensure_selected_text_fit()
        self._connect_signals()
        self._recover_if_needed()
        self._refresh_preview()
        # Keep the neutral import surface on launch. Keyboard users can still
        # reach it first with Tab, where its embedded actions are revealed.
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("root")
        shell = QVBoxLayout(central)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        self.title_bar = WindowTitleBar(self)
        shell.addWidget(self.title_bar)

        content = QWidget()
        content.setObjectName("content")
        outer = QVBoxLayout(content)
        outer.setContentsMargins(22, 16, 22, 16)
        outer.setSpacing(12)
        shell.addWidget(content, 1)

        self.drop_zone = DropZone()
        outer.addWidget(self.drop_zone)
        self.add_files_button = self.drop_zone.add_files_button
        self.add_folder_button = self.drop_zone.add_folder_button
        self.clear_button = self.drop_zone.clear_button

        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        self.mode_control = SegmentedControl()
        self.sequence_mode = self.mode_control.sequence_button
        self.replace_mode = self.mode_control.replace_button
        self.mode_group = self.mode_control.group
        mode_row.addWidget(self.mode_control)
        mode_row.addStretch(1)
        outer.addLayout(mode_row)

        self.mode_stack = QStackedWidget()
        self.mode_stack.setFixedHeight(78)
        self.mode_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        sequence_page = QWidget()
        sequence_layout = QVBoxLayout(sequence_page)
        sequence_layout.setContentsMargins(0, 0, 0, 0)
        sequence_layout.setSpacing(4)
        sequence_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        output_label = QLabel("Output pattern")
        output_label.setObjectName("fieldLabel")
        sequence_layout.addWidget(output_label)
        self.pattern_edit = QLineEdit()
        self.pattern_edit.setPlaceholderText("shot_comp_v001.####")
        self.pattern_edit.setAccessibleName("Output pattern")
        sequence_layout.addWidget(self.pattern_edit)
        token_help = QLabel(
            "Frame tokens: ####, %04d or %04. File extensions stay unchanged."
        )
        token_help.setObjectName("hint")
        token_help.setWordWrap(True)
        sequence_layout.addWidget(token_help)

        replace_page = QWidget()
        replace_layout = QHBoxLayout(replace_page)
        replace_layout.setContentsMargins(0, 0, 0, 0)
        replace_layout.setSpacing(12)
        replace_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        find_column = QVBoxLayout()
        find_column.setSpacing(4)
        find_label = QLabel("Find")
        find_label.setObjectName("fieldLabel")
        find_column.addWidget(find_label)
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("Text to find")
        self.find_edit.setAccessibleName("Text to find")
        find_column.addWidget(self.find_edit)
        replace_column = QVBoxLayout()
        replace_column.setSpacing(4)
        replace_label = QLabel("Replace with")
        replace_label.setObjectName("fieldLabel")
        replace_column.addWidget(replace_label)
        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText("Replacement (optional)")
        self.replace_edit.setAccessibleName("Replacement text")
        replace_column.addWidget(self.replace_edit)
        replace_layout.addLayout(find_column)
        replace_layout.addLayout(replace_column)
        self.mode_stack.addWidget(sequence_page)
        self.mode_stack.addWidget(replace_page)
        outer.addWidget(self.mode_stack)

        self.preview_model = RenamePreviewModel()
        self.table_frame = QFrame()
        self.table_frame.setObjectName("previewTableFrame")
        table_layout = QVBoxLayout(self.table_frame)
        table_layout.setContentsMargins(1, 1, 1, 1)
        table_layout.setSpacing(0)

        self.table = QTableView()
        self.table.setObjectName("previewTable")
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        self.table.setAccessibleName("Rename preview")
        self.preview_header = PreviewHeader(self.table)
        self.table.setHorizontalHeader(self.preview_header)
        self.table.setModel(self.preview_model)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self.table.setSortingEnabled(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionsClickable(False)
        header.setSectionsMovable(False)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setMinimumSectionSize(PreviewHeader.FALLBACK_NAME_MINIMUM)
        header.resizeSection(2, PreviewHeader.FRAME_COLUMN_WIDTH)
        header.resizeSection(3, PreviewHeader.STATUS_COLUMN_WIDTH)
        self.preview_header.schedule_name_layout()
        self.table.verticalHeader().setDefaultSectionSize(33)
        table_layout.addWidget(self.table)
        outer.addWidget(self.table_frame, 1)

        separator = QFrame()
        separator.setObjectName("footerSeparator")
        separator.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(separator)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(10)
        self.summary_label = QLabel("No files added")
        self.summary_label.setObjectName("summary")
        self.undo_button = QPushButton("Undo")
        self.undo_button.setToolTip("Undo the last completed rename (Ctrl+Z)")
        self.rename_button = QPushButton("Rename files")
        self.rename_button.setObjectName("primaryButton")
        footer.addWidget(self.summary_label, 1)
        footer.addWidget(self.undo_button)
        footer.addWidget(self.rename_button)
        outer.addLayout(footer)
        self.setCentralWidget(central)
        self._create_resize_handles()

        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(120)

        self.open_files_shortcut = QShortcut(QKeySequence("Ctrl+O"), self)
        self.open_folder_shortcut = QShortcut(QKeySequence("Ctrl+Shift+O"), self)
        self.rename_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        self.rename_keypad_shortcut = QShortcut(QKeySequence("Ctrl+Enter"), self)
        self.undo_shortcut = QShortcut(QKeySequence("Ctrl+Z"), self)
        self.undo_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)

    def _create_resize_handles(self) -> None:
        self._resize_handles = {
            "left": WindowResizeHandle(
                self, Qt.Edge.LeftEdge, Qt.CursorShape.SizeHorCursor
            ),
            "right": WindowResizeHandle(
                self, Qt.Edge.RightEdge, Qt.CursorShape.SizeHorCursor
            ),
            "top": WindowResizeHandle(
                self, Qt.Edge.TopEdge, Qt.CursorShape.SizeVerCursor
            ),
            "bottom": WindowResizeHandle(
                self, Qt.Edge.BottomEdge, Qt.CursorShape.SizeVerCursor
            ),
            "top_left": WindowResizeHandle(
                self,
                Qt.Edge.TopEdge | Qt.Edge.LeftEdge,
                Qt.CursorShape.SizeFDiagCursor,
            ),
            "top_right": WindowResizeHandle(
                self,
                Qt.Edge.TopEdge | Qt.Edge.RightEdge,
                Qt.CursorShape.SizeBDiagCursor,
            ),
            "bottom_left": WindowResizeHandle(
                self,
                Qt.Edge.BottomEdge | Qt.Edge.LeftEdge,
                Qt.CursorShape.SizeBDiagCursor,
            ),
            "bottom_right": WindowResizeHandle(
                self,
                Qt.Edge.BottomEdge | Qt.Edge.RightEdge,
                Qt.CursorShape.SizeFDiagCursor,
            ),
        }
        self._position_resize_handles()

    def _position_resize_handles(self) -> None:
        if not hasattr(self, "_resize_handles"):
            return
        if self.isMaximized():
            for handle in self._resize_handles.values():
                handle.hide()
            return

        width, height = self.width(), self.height()
        edge, corner = 6, 10
        geometries = {
            "left": (0, corner, edge, max(0, height - 2 * corner)),
            "right": (width - edge, corner, edge, max(0, height - 2 * corner)),
            "top": (corner, 0, max(0, width - 2 * corner), edge),
            "bottom": (corner, height - edge, max(0, width - 2 * corner), edge),
            "top_left": (0, 0, corner, corner),
            "top_right": (width - corner, 0, corner, corner),
            "bottom_left": (0, height - corner, corner, corner),
            "bottom_right": (width - corner, height - corner, corner, corner),
        }
        for name, handle in self._resize_handles.items():
            handle.setGeometry(*geometries[name])
            handle.show()
            handle.raise_()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._position_resize_handles()
        if hasattr(self, "preview_header"):
            self.preview_header.schedule_name_layout()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self.title_bar.sync_window_state()
            self._position_resize_handles()

    def _connect_signals(self) -> None:
        self.drop_zone.paths_dropped.connect(self._add_dropped_paths)
        self.drop_zone.files_requested.connect(self._select_files)
        self.drop_zone.folder_requested.connect(self._select_folder)
        self.drop_zone.clear_requested.connect(self._clear_sources)
        self.sequence_mode.toggled.connect(self._change_mode)
        self.pattern_edit.textChanged.connect(self._schedule_preview)
        self.find_edit.textChanged.connect(self._schedule_preview)
        self.replace_edit.textChanged.connect(self._schedule_preview)
        self.preview_timer.timeout.connect(self._refresh_preview)
        self.rename_button.clicked.connect(self._start_rename)
        self.undo_button.clicked.connect(self._undo_last)
        self.open_files_shortcut.activated.connect(self._select_files)
        self.open_folder_shortcut.activated.connect(self._select_folder)
        self.rename_shortcut.activated.connect(self._start_rename)
        self.rename_keypad_shortcut.activated.connect(self._start_rename)
        self.undo_shortcut.activated.connect(self._undo_last)

    @Slot()
    def _select_files(self) -> None:
        if self._busy:
            return
        initial = str(self.settings_store.browse_directory(self.settings))
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Choose files",
            initial,
            "Image Sequences (*.exr *.EXR *.png *.PNG);;All Files (*.*)",
        )
        if not paths:
            return
        selected = [Path(path) for path in paths]
        self._remember_directory(selected[0].parent)
        self._add_paths(selected)

    @Slot()
    def _select_folder(self) -> None:
        if self._busy:
            return
        initial = str(self.settings_store.browse_directory(self.settings))
        selected = QFileDialog.getExistingDirectory(self, "Choose a folder", initial)
        if not selected:
            return
        directory = Path(selected)
        self._remember_directory(directory)
        self._add_paths(self._sequence_files_in_directory(directory))

    @Slot(list)
    def _add_dropped_paths(self, raw_paths: list[str]) -> None:
        if self._busy:
            return
        collected: list[Path] = []
        remembered_directory: Path | None = None
        for raw_path in raw_paths:
            path = Path(raw_path)
            if path.is_dir():
                remembered_directory = remembered_directory or path
                collected.extend(self._sequence_files_in_directory(path))
            elif path.is_file():
                remembered_directory = remembered_directory or path.parent
                collected.append(path)
        if remembered_directory:
            self._remember_directory(remembered_directory)
        self._add_paths(collected)

    def _remember_directory(self, directory: Path) -> None:
        try:
            self.settings_store.remember_directory(self.settings, directory)
        except (OSError, ValueError) as error:
            LOGGER.warning("Could not save the last browse directory: %s", error)

    @staticmethod
    def _sequence_files_in_directory(directory: Path) -> list[Path]:
        try:
            return sorted(
                (
                    path
                    for path in directory.iterdir()
                    if path.is_file()
                    and path.suffix.casefold() in SUPPORTED_SEQUENCE_EXTENSIONS
                ),
                key=lambda path: path.name.casefold(),
            )
        except OSError:
            return []

    def _add_paths(self, paths: Iterable[Path]) -> None:
        if self._busy:
            return
        existing = {canonical_path_key(path) for path in self.source_paths}
        for path in paths:
            key = canonical_path_key(path)
            if path.is_file() and key not in existing:
                self.source_paths.append(path)
                existing.add(key)
        self._redetect_sources(set_default_pattern=True)

    @Slot()
    def _clear_sources(self) -> None:
        if self._busy:
            return
        self.pattern_edit.clear()
        self.find_edit.clear()
        self.replace_edit.clear()
        # Each edit normally starts the short preview debounce. Clearing is an
        # explicit reset, so cancel that queued refresh and publish one empty
        # plan synchronously below.
        self.preview_timer.stop()
        self.source_paths.clear()
        self.detected_files = ()
        self.sequence_groups = ()
        self._refresh_preview()
        # Return the empty preview to its natural starting position with
        # Original Name visible.
        self.table.horizontalScrollBar().setValue(0)

    def _redetect_sources(self, *, set_default_pattern: bool = False) -> None:
        self.detected_files, self.sequence_groups = detect_sequences(self.source_paths)
        if (
            set_default_pattern
            and not self.pattern_edit.text()
            and self.sequence_groups
        ):
            first_pattern = self.sequence_groups[0].files[0].sequence_pattern
            if first_pattern:
                self.pattern_edit.setText(first_pattern)
                self.preview_timer.stop()
        self._refresh_preview()

    @Slot()
    def _change_mode(self) -> None:
        if self._busy:
            return
        self.mode_stack.setCurrentIndex(0 if self.sequence_mode.isChecked() else 1)
        self._schedule_preview()

    @Slot()
    def _schedule_preview(self) -> None:
        if not self._busy:
            self.preview_timer.start()

    @Slot()
    def _refresh_preview(self) -> None:
        selected_row = self.table.currentIndex().row()
        scroll_position = self.table.verticalScrollBar().value()
        mode = (
            RenameMode.SEQUENCE
            if self.sequence_mode.isChecked()
            else RenameMode.REPLACE
        )
        self.current_plan = build_plan(
            self.detected_files,
            mode,
            output_pattern=self.pattern_edit.text(),
            find_text=self.find_edit.text(),
            replacement=self.replace_edit.text(),
        )
        self.preview_model.set_plan(self.current_plan)
        self.preview_header.schedule_name_layout()
        if 0 <= selected_row < self.preview_model.rowCount():
            self.table.setCurrentIndex(self.preview_model.index(selected_row, 0))
        self.table.verticalScrollBar().setValue(scroll_position)
        self._update_summary()

    def _update_summary(self) -> None:
        file_count = len(self.source_paths)
        ready_count = len(self.current_plan.ready_items)
        error_count = len(self.current_plan.error_items)
        waiting_count = len(self.current_plan.waiting_items)
        no_match_count = len(self.current_plan.no_match_items)
        missing_count = sum(group.missing_frame_count for group in self.sequence_groups)
        sequence_count = len(self.sequence_groups)

        self._set_summary_tone("neutral")
        self.drop_zone.set_selection_count(len(self.source_paths))
        if not self.source_paths:
            summary = "No files added"
        else:
            parts = [
                f"{sequence_count} sequence{'s' if sequence_count != 1 else ''}",
                f"{file_count} file{'s' if file_count != 1 else ''}",
            ]
            if ready_count:
                parts.append(f"{ready_count} ready")
            elif waiting_count:
                parts.append("waiting for input")
            elif no_match_count:
                parts.append("no matches")
            if missing_count:
                parts.append(
                    f"{missing_count} missing frame{'s' if missing_count != 1 else ''}"
                )
            if no_match_count and ready_count:
                parts.append(
                    f"{no_match_count} no match{'es' if no_match_count != 1 else ''}"
                )
            if error_count:
                parts.append(f"{error_count} conflict{'s' if error_count != 1 else ''}")
            summary = " · ".join(parts)
        self.summary_label.setText(summary)
        self.rename_button.setText(
            f"Rename {ready_count} file{'s' if ready_count != 1 else ''}"
            if ready_count
            else "Rename files"
        )
        self.rename_button.setEnabled(self.current_plan.can_execute and not self._busy)
        self.undo_button.setEnabled(
            self.transaction_manager.can_undo and not self._busy
        )
        self.undo_shortcut.setEnabled(
            self.transaction_manager.can_undo and not self._busy
        )
        self.clear_button.setEnabled(bool(self.source_paths) and not self._busy)

    def _set_summary_tone(self, tone: str) -> None:
        self.summary_label.setProperty("tone", tone)
        self.summary_label.style().unpolish(self.summary_label)
        self.summary_label.style().polish(self.summary_label)

    @Slot()
    def _start_rename(self) -> None:
        if self._busy:
            return

        # Text edits use a short debounce for normal previewing. A rename must
        # never rely on that delayed state: stop it and capture a fresh,
        # immutable plan synchronously before asking for confirmation.
        self.preview_timer.stop()
        self._refresh_preview()
        plan = self.current_plan
        if not plan.can_execute:
            return

        count = len(plan.ready_items)
        confirmed = self._confirm_action(
            window_title="Renamr",
            heading=f"Rename {count} file{'s' if count != 1 else ''}?",
            body=(
                f"{len(self.sequence_groups)} sequence"
                f"{'s' if len(self.sequence_groups) != 1 else ''} · {count} files\n"
                "Frame numbers and file extensions will be preserved."
            ),
            confirm_text=f"Rename {count} file{'s' if count != 1 else ''}",
        )
        if not confirmed:
            return
        self._begin_operation("rename", plan)

    def _confirm_action(
        self,
        *,
        window_title: str,
        heading: str,
        body: str,
        confirm_text: str,
    ) -> bool:
        """Show an icon-free Renamr confirmation dialog."""

        return RenamrDialog.confirm(
            self,
            window_title=window_title,
            heading=heading,
            body=body,
            confirm_text=confirm_text,
        )

    def _begin_operation(
        self,
        operation: OperationKind,
        plan: RenamePlan | None = None,
    ) -> None:
        if self._busy or self._thread is not None:
            return

        self._operation_kind = operation
        self._active_plan = plan
        self._operation_result = None
        self._set_busy(True)
        try:
            thread = QThread(self)
            worker = TransactionWorker(self.transaction_manager, operation, plan)
            self._thread = thread
            self._worker = worker
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.finished.connect(self._capture_operation_result)
            worker.finished.connect(thread.quit)
            worker.finished.connect(worker.deleteLater)
            thread.finished.connect(self._operation_thread_finished)
            thread.finished.connect(thread.deleteLater)
            thread.start()
        except Exception as error:  # defensive boundary around Qt thread setup
            LOGGER.exception("Could not start the transaction worker")
            self._thread = None
            self._worker = None
            self._operation_kind = None
            self._active_plan = None
            self._operation_result = None
            self._set_busy(False)
            self._show_transaction_error(
                "Operation Failed",
                TransactionResult.failure(f"Could not start the operation: {error}"),
            )

    @Slot(object)
    def _capture_operation_result(self, result: TransactionResult) -> None:
        # Busy intentionally remains true here. The worker thread has emitted
        # its result, but it has not necessarily finished shutting down yet.
        self._operation_result = result
        self._log_transaction_result(self._operation_kind or "unknown", result)

    @Slot()
    def _operation_thread_finished(self) -> None:
        operation = self._operation_kind
        plan = self._active_plan
        result = self._operation_result or TransactionResult.failure(
            "The operation ended without returning a result."
        )
        if self._operation_result is None:
            self._log_transaction_result(operation or "unknown", result)

        self._thread = None
        self._worker = None
        self._operation_kind = None
        self._active_plan = None
        self._operation_result = None

        success_message: str | None = None
        failure_title: str | None = None
        try:
            if operation == "rename" and result.success and plan is not None:
                targets = {
                    canonical_path_key(item.source): item.target
                    for item in plan.ready_items
                }
                self.source_paths = [
                    targets.get(canonical_path_key(path), path)
                    for path in self.source_paths
                ]
                self._redetect_sources()
                success_message = result.message
            elif operation == "undo" and result.status is TransactionStatus.UNDONE:
                self.source_paths = list(result.final_paths)
                self._redetect_sources()
                success_message = result.message
            elif operation == "undo" and result.success:
                self._redetect_sources()
                success_message = result.message
            else:
                self._redetect_sources()
                failure_title = (
                    "Undo Failed" if operation == "undo" else "Rename Failed"
                )
        finally:
            # No source mutation or second transaction is allowed until the
            # QThread has fully emitted finished and this cleanup is complete.
            self._set_busy(False)

        if success_message:
            self.summary_label.setText(success_message)
            self._set_summary_tone("success")
        elif failure_title:
            self._show_transaction_error(failure_title, result)

    @Slot()
    def _undo_last(self) -> None:
        if self._busy or not self.transaction_manager.can_undo:
            return
        confirmed = self._confirm_action(
            window_title="Renamr",
            heading="Undo the last rename?",
            body="The previous file names will be restored safely.",
            confirm_text="Undo rename",
        )
        if not confirmed:
            return
        self._begin_operation("undo")

    def _recover_if_needed(self) -> None:
        results = self.transaction_manager.recover_incomplete()
        for result in results:
            self._log_transaction_result("recovery", result)
        failures = tuple(result for result in results if not result.success)
        if failures:
            QTimer.singleShot(
                0,
                lambda failures=failures: self._show_recovery_failures(failures),
            )

    @staticmethod
    def _log_transaction_result(context: str, result: TransactionResult) -> None:
        LOGGER.info(
            "%s result: status=%s transaction_id=%s journal=%s errors=%s",
            context,
            result.status.value,
            result.transaction_id or "none",
            result.journal_path or "none",
            list(result.errors),
        )

    def _show_transaction_error(
        self,
        title: str,
        result: TransactionResult,
    ) -> None:
        details: list[str] = []
        if result.transaction_id:
            details.append(f"Transaction: {result.transaction_id}")
        if result.journal_path:
            details.append(f"Journal: {result.journal_path}")
        if result.errors:
            details.append("Errors:")
            details.extend(f"- {error}" for error in result.errors)
        RenamrDialog.alert(
            self,
            window_title="Renamr",
            heading=title,
            body=result.message,
            details="\n".join(details),
        )

    def _show_recovery_failures(
        self,
        failures: tuple[TransactionResult, ...],
    ) -> None:
        sections: list[str] = []
        for index, result in enumerate(failures, start=1):
            lines = [f"Recovery issue {index}: {result.message}"]
            if result.transaction_id:
                lines.append(f"Transaction: {result.transaction_id}")
            if result.journal_path:
                lines.append(f"Journal: {result.journal_path}")
            lines.extend(f"- {error}" for error in result.errors)
            sections.append("\n".join(lines))
        RenamrDialog.alert(
            self,
            window_title="Renamr",
            heading="Recovery required",
            body=(
                "One or more interrupted rename transactions could not be "
                "recovered safely. No existing file was overwritten."
            ),
            details="\n\n".join(sections),
        )

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.drop_zone._clear_drag_state()
        self.drop_zone.setAcceptDrops(not busy)
        self.drop_zone.setEnabled(not busy)
        self.setAcceptDrops(not busy)
        self.open_files_shortcut.setEnabled(not busy)
        self.open_folder_shortcut.setEnabled(not busy)
        self.rename_shortcut.setEnabled(not busy)
        self.rename_keypad_shortcut.setEnabled(not busy)
        for widget in (
            self.add_files_button,
            self.add_folder_button,
            self.clear_button,
            self.sequence_mode,
            self.replace_mode,
            self.pattern_edit,
            self.find_edit,
            self.replace_edit,
            self.undo_button,
        ):
            widget.setEnabled(not busy)
        self.rename_button.setEnabled(not busy and self.current_plan.can_execute)
        if busy:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            self._set_summary_tone("neutral")
            self.summary_label.setText(
                "Restoring file names safely…"
                if self._operation_kind == "undo"
                else "Renaming files safely…"
            )
        else:
            QApplication.restoreOverrideCursor()
            self._update_summary()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self._busy or not event.mimeData().hasUrls():
            event.ignore()
            return
        self.drop_zone.set_drag_active(True)
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802, ANN001
        self.drop_zone._clear_drag_state()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        self.drop_zone._clear_drag_state()
        if self._busy:
            event.ignore()
            return
        paths = [
            url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()
        ]
        if paths:
            self._add_dropped_paths(paths)
            event.acceptProposedAction()
            return
        event.ignore()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._busy:
            RenamrDialog.alert(
                self,
                window_title="Renamr",
                heading="Rename in progress",
                body="Wait for the current rename operation to finish before closing.",
            )
            event.ignore()
            return
        event.accept()
