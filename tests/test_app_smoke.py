import logging
from pathlib import Path

import pytest
from PySide6.QtGui import QFont

import sequence_renamer.app as app_module


def test_application_smoke(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(
        app_module,
        "_acquire_instance_lock",
        lambda: (_ for _ in ()).throw(AssertionError("smoke test used the real lock")),
    )
    assert app_module.main(["--smoke-test"]) == 0


def test_logging_failure_falls_back_without_blocking_startup(monkeypatch) -> None:
    logger = logging.getLogger("sequence_renamer")
    previous_handlers = list(logger.handlers)
    logger.handlers.clear()
    monkeypatch.setattr(
        app_module,
        "configure_logging",
        lambda: (_ for _ in ()).throw(PermissionError("read-only app data")),
    )
    try:
        fallback, log_path = app_module._configure_startup_logging()
        assert log_path is None
        assert fallback is logger
        assert any(
            isinstance(handler, logging.NullHandler) for handler in logger.handlers
        )
    finally:
        logger.handlers.clear()
        logger.handlers.extend(previous_handlers)


def test_cli_version_uses_renamr_brand(capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        app_module._build_parser().parse_args(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out == "Renamr 1.1.6\n"


def test_qt_application_metadata_uses_renamr(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    application = app_module._create_application(["renamr-test"])

    assert application.applicationName() == "Renamr"
    assert application.organizationName() == "Renamr"
    assert application.applicationVersion() == "1.1.6"


def test_bundled_inter_assets_are_available() -> None:
    for filename in app_module.BUNDLED_INTER_FILES:
        assert app_module._resource_path(f"assets/fonts/Inter-4.1/{filename}").is_file()
    assert app_module._resource_path("assets/fonts/Inter-4.1/LICENSE.txt").is_file()


def test_application_font_preferences_are_deterministic() -> None:
    assert (
        app_module._preferred_font_family(
            ["Segoe UI", "Inter", "Segoe UI Variable Text"]
        )
        == "Inter"
    )
    assert (
        app_module._preferred_font_family(["segoe ui", "SEGOE UI VARIABLE TEXT"])
        == "SEGOE UI VARIABLE TEXT"
    )
    assert app_module._preferred_font_family([]) == "Segoe UI"


def test_application_font_configuration_preserves_point_size(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    application = app_module._create_application(["renamr-font-test"])
    original_font = QFont(application.font())
    original_font.setPointSizeF(11.75)
    application.setFont(original_font)

    app_module._configure_application_font(application)

    assert application.font().pointSizeF() == 11.75


def test_instance_lock_keeps_legacy_identity_for_safe_upgrades(
    tmp_path: Path,
    monkeypatch,
) -> None:
    legacy_state = tmp_path / "SequenceRenamer"
    monkeypatch.setattr(
        app_module,
        "compatibility_state_directory",
        lambda: legacy_state,
    )

    lock = app_module._acquire_instance_lock()
    try:
        assert Path(lock.fileName()) == legacy_state / "sequence-renamer.lock"
    finally:
        lock.unlock()
