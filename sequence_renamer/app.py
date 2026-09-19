"""Application bootstrap, diagnostics, and packaged smoke test."""

from __future__ import annotations

import argparse
import logging
import multiprocessing
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QLockFile
from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication

from . import __version__
from .logging_config import configure_logging
from .settings import SettingsStore, compatibility_state_directory
from .transaction import TransactionManager
from .ui import MainWindow, RenamrDialog


INSTANCE_LOCK_FILENAME = "sequence-renamer.lock"
PREFERRED_FONT_FAMILIES = (
    "Inter",
    "Segoe UI Variable Text",
    "Segoe UI",
)
BUNDLED_INTER_FILES = (
    "Inter-Regular.ttf",
    "Inter-Medium.ttf",
    "Inter-SemiBold.ttf",
    "Inter-Bold.ttf",
)


class InstanceLockError(RuntimeError):
    """The per-user application lock could not be acquired safely."""


class InstanceAlreadyRunningError(InstanceLockError):
    """Another live Renamr process owns the application lock."""


def _resource_path(relative_path: str) -> Path:
    package_path = Path(__file__).resolve().parent / relative_path
    if package_path.exists():
        return package_path
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return bundle_root / "sequence_renamer" / relative_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safe image-sequence batch renamer")
    parser.add_argument("--version", action="version", version=f"Renamr {__version__}")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Initialize the packaged UI and exit without changing files.",
    )
    return parser


def _install_exception_boundary(logger: logging.Logger) -> None:
    def handle_exception(exception_type, exception, traceback) -> None:  # noqa: ANN001
        if issubclass(exception_type, KeyboardInterrupt):
            sys.__excepthook__(exception_type, exception, traceback)
            return
        logger.critical(
            "Unhandled application exception",
            exc_info=(exception_type, exception, traceback),
        )
        if QApplication.instance() is not None:
            RenamrDialog.alert(
                None,
                window_title="Unexpected Error",
                heading="Unexpected Error",
                body="Renamr encountered an unexpected error. "
                "Details were logged when file logging was available.",
            )

    sys.excepthook = handle_exception


def _preferred_font_family(installed_families: list[str]) -> str:
    """Return the first installed UI font, with a safe Windows fallback."""

    installed_by_name = {family.casefold(): family for family in installed_families}
    for family in PREFERRED_FONT_FAMILIES:
        installed_name = installed_by_name.get(family.casefold())
        if installed_name is not None:
            return installed_name

    # Font discovery can be empty under Qt's offscreen backend and in some
    # frozen startup environments. Requesting Segoe UI remains safe: Qt will
    # transparently substitute the platform default if it is unavailable.
    return "Segoe UI"


def _configure_application_font(application: QApplication) -> None:
    bundled_families: list[str] = []
    for filename in BUNDLED_INTER_FILES:
        font_path = _resource_path(f"assets/fonts/Inter-4.1/{filename}")
        if not font_path.exists():
            continue
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id >= 0:
            bundled_families.extend(QFontDatabase.applicationFontFamilies(font_id))

    try:
        installed_families = QFontDatabase.families()
    except RuntimeError:
        installed_families = []

    font = QFont(application.font())
    font.setFamily(_preferred_font_family(bundled_families + installed_families))
    application.setFont(font)


def _create_application(arguments: list[str]) -> QApplication:
    application = QApplication.instance() or QApplication(arguments)
    application.setApplicationName("Renamr")
    application.setApplicationVersion(__version__)
    application.setOrganizationName("Renamr")
    application.setStyle("Fusion")
    _configure_application_font(application)
    icon_path = _resource_path("assets/renamr.svg")
    if icon_path.exists():
        application.setWindowIcon(QIcon(str(icon_path)))
    return application


def _configure_startup_logging() -> tuple[logging.Logger, Path | None]:
    """Prefer file logging, but keep the application usable without it."""

    try:
        return configure_logging()
    except OSError as error:
        logger = logging.getLogger("sequence_renamer")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        if not logger.handlers:
            logger.addHandler(logging.NullHandler())
        logger.warning("File logging is unavailable: %s", error)
        return logger, None


def _acquire_instance_lock(directory: Path | None = None) -> QLockFile:
    """Acquire and return the per-user lock held for the GUI lifetime."""

    # Keep the previous lock identity so an installed legacy build and Renamr
    # cannot run concurrently against the same recovery journals.
    lock_directory = directory or compatibility_state_directory()
    try:
        lock_directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise InstanceLockError(
            "Renamr could not create its application data directory."
        ) from error

    lock = QLockFile(str(lock_directory / INSTANCE_LOCK_FILENAME))
    # A GUI may legitimately stay open for days. Zero disables time-based
    # expiry while QLockFile still detects a lock whose owning process died.
    lock.setStaleLockTime(0)
    if lock.tryLock(100):
        return lock
    if lock.error() == QLockFile.LockError.LockFailedError:
        raise InstanceAlreadyRunningError("Renamr is already running.")
    raise InstanceLockError("Renamr could not create its single-instance lock.")


def _run_smoke_test(application: QApplication) -> int:
    if application.windowIcon().isNull():
        return 3
    with tempfile.TemporaryDirectory(prefix="sequence-renamer-smoke-") as temporary:
        root = Path(temporary)
        window = MainWindow(
            settings_store=SettingsStore(root / "settings"),
            transaction_manager=TransactionManager(root / "journals"),
        )
        window.show()
        application.processEvents()
        if window.windowTitle() != "Renamr":
            return 2
        window.close()
        application.processEvents()
    return 0


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    arguments = list(sys.argv[1:] if argv is None else argv)
    options = _build_parser().parse_args(arguments)
    application = _create_application([sys.argv[0], *arguments])
    logger, log_path = _configure_startup_logging()
    _install_exception_boundary(logger)
    logger.info(
        "Starting Renamr %s; log=%s",
        __version__,
        log_path or "disabled",
    )

    if options.smoke_test:
        return _run_smoke_test(application)

    try:
        instance_lock = _acquire_instance_lock()
    except InstanceAlreadyRunningError as error:
        logger.info("Startup stopped: %s", error)
        RenamrDialog.alert(
            None,
            window_title="Renamr",
            heading="Renamr Is Already Running",
            body=str(error),
        )
        return 0
    except InstanceLockError as error:
        logger.error("Startup stopped: %s", error, exc_info=True)
        RenamrDialog.alert(
            None,
            window_title="Renamr Could Not Start",
            heading="Renamr Could Not Start",
            body=f"{error}\n\nCheck that your user application data folder is writable.",
        )
        return 1

    try:
        window = MainWindow()
        window.show()
        return application.exec()
    finally:
        instance_lock.unlock()
