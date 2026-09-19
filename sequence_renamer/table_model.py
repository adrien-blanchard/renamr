"""Qt table model for the immutable rename preview."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from .models import PlanStatus, RenamePlan, RenamePlanItem


class RenamePreviewModel(QAbstractTableModel):
    """Read-only preview model that never controls frame numbering."""

    HEADERS = ("Original Name", "New Name", "Frame", "Status")

    def __init__(self) -> None:
        super().__init__()
        self._plan = RenamePlan(items=())

    @property
    def plan(self) -> RenamePlan:
        return self._plan

    def set_plan(self, plan: RenamePlan) -> None:
        self.beginResetModel()
        self._plan = plan
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._plan.items)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object | None:
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
        ):
            if 0 <= section < len(self.HEADERS):
                return self.HEADERS[section]
        return None

    def data(
        self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object | None:
        if not index.isValid() or not 0 <= index.row() < len(self._plan.items):
            return None
        item = self._plan.items[index.row()]

        if role == Qt.ItemDataRole.DisplayRole:
            return self._display_value(item, index.column())
        if role == Qt.ItemDataRole.ToolTipRole:
            if index.column() == 0:
                return item.source.name
            if index.column() == 1:
                return item.target.name
            return None
        if role == Qt.ItemDataRole.ForegroundRole:
            if item.status is PlanStatus.ERROR:
                return QColor("#C53929")
            if (
                item.status
                in {
                    PlanStatus.WAITING,
                    PlanStatus.NO_MATCH,
                    PlanStatus.UNCHANGED,
                }
                and index.column() != 0
            ):
                return QColor("#61605B")
            return QColor("#2C2C2B")
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if index.column() in (2, 3):
                return int(Qt.AlignmentFlag.AlignCenter)
            return int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        return None

    @staticmethod
    def _display_value(item: RenamePlanItem, column: int) -> str:
        if column == 0:
            return item.source.name
        if column == 1:
            return item.target.name
        if column == 2:
            return str(item.frame_number) if item.frame_number is not None else "—"
        if column == 3:
            return item.status.value
        return ""
