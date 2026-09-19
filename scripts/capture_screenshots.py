"""Capture the actual UI with synthetic sequences and isolated application state."""

from pathlib import Path
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "2")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtTest import QTest
from sequence_renamer.app import _create_application
from sequence_renamer.settings import SettingsStore
from sequence_renamer.transaction import TransactionManager
from sequence_renamer.ui import MainWindow


def main():
    output = Path(__file__).resolve().parents[1] / "docs" / "media"
    output.mkdir(parents=True, exist_ok=True)
    app = _create_application(["renamr-screenshots"])
    with tempfile.TemporaryDirectory(prefix="renamr-demo-") as directory:
        root = Path(directory)
        frames = root / "frames"
        frames.mkdir()
        sources = []
        for frame in range(1001, 1013):
            path = frames / f"sh010_plate_v003.{frame}.exr"
            path.write_bytes(b"synthetic filename-only demo")
            sources.append(path)
        window = MainWindow(
            settings_store=SettingsStore(root / "settings"),
            transaction_manager=TransactionManager(root / "journals"),
        )
        window.show()

        def capture(name):
            window.setFocus()
            app.processEvents()
            QTest.qWait(250)
            if not window.grab().save(str(output / name), "JPG", 95):
                raise RuntimeError("Screenshot could not be saved")

        capture("renamr-empty.jpg")
        window._add_paths(sources)
        window.pattern_edit.setText("sh010_comp_v004.####")
        window._refresh_preview()
        assert window.current_plan.can_execute
        capture("renamr-sequence.jpg")
        window.replace_mode.click()
        window.find_edit.setText("plate_v003")
        window.replace_edit.setText("comp_v004")
        window._refresh_preview()
        assert window.current_plan.can_execute
        capture("renamr-replace.jpg")
        # Exercise the real filesystem transaction and Undo on disposable data.
        result = window.transaction_manager.execute(window.current_plan)
        assert result.success
        assert all(path.exists() for path in result.target_paths)
        undone = window.transaction_manager.undo_last()
        assert undone.success and all(path.exists() for path in sources)
        (frames / "sh010_comp_v004.1001.exr").write_bytes(b"existing destination")
        window._refresh_preview()
        assert not window.current_plan.can_execute
        capture("renamr-conflict.jpg")
        window.close()
    print(
        "Captured four real UI screenshots; rename, Undo and collision protection verified."
    )


if __name__ == "__main__":
    main()
