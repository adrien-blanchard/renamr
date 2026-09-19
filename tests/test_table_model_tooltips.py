from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt

from sequence_renamer.models import PlanStatus, RenamePlan, RenamePlanItem
from sequence_renamer.table_model import RenamePreviewModel


def _model_with_item(tmp_path: Path) -> tuple[RenamePreviewModel, RenamePlanItem]:
    source = tmp_path / "nested" / "original_plate_v012.1001.exr"
    target = source.with_name("delivery_plate_v013.1001.exr")
    item = RenamePlanItem(
        source=source,
        target=target,
        selection_index=0,
        frame_number=1001,
        status=PlanStatus.ERROR,
        message="The destination already exists and will not be overwritten.",
    )
    model = RenamePreviewModel()
    model.set_plan(RenamePlan(items=(item,)))
    return model, item


def test_name_tooltips_contain_only_the_relevant_full_filename(
    tmp_path: Path,
) -> None:
    model, item = _model_with_item(tmp_path)

    original_tooltip = model.index(0, 0).data(Qt.ItemDataRole.ToolTipRole)
    new_name_tooltip = model.index(0, 1).data(Qt.ItemDataRole.ToolTipRole)

    assert original_tooltip == item.source.name
    assert new_name_tooltip == item.target.name
    assert str(item.source.parent) not in original_tooltip
    assert str(item.target.parent) not in new_name_tooltip


def test_frame_and_status_cells_do_not_expose_path_tooltips(tmp_path: Path) -> None:
    model, _item = _model_with_item(tmp_path)

    assert model.index(0, 2).data(Qt.ItemDataRole.ToolTipRole) is None
    assert model.index(0, 3).data(Qt.ItemDataRole.ToolTipRole) is None
