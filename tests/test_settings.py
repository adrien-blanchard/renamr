import json
from pathlib import Path

from sequence_renamer.settings import (
    AppSettings,
    SettingsStore,
    app_data_directory,
    compatibility_state_directory,
)


def test_settings_round_trip(tmp_path: Path) -> None:
    source_directory = tmp_path / "shots"
    source_directory.mkdir()
    store = SettingsStore(tmp_path / "settings")
    settings = AppSettings()

    store.remember_directory(settings, source_directory)
    loaded = store.load()

    assert loaded.last_directory == str(source_directory.resolve())
    assert store.browse_directory(loaded) == source_directory.resolve()


def test_cancelled_or_invalid_directory_is_not_remembered(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings")
    settings = AppSettings(last_directory="kept")

    store.remember_directory(settings, tmp_path / "missing")

    assert settings.last_directory == "kept"


def test_corrupt_settings_fall_back_to_defaults(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    store.path.write_text("not-json", encoding="utf-8")

    assert store.load() == AppSettings()


def test_default_paths_separate_renamr_settings_from_legacy_safety_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert app_data_directory() == tmp_path / "Renamr"
    assert compatibility_state_directory() == tmp_path / "SequenceRenamer"


def test_valid_legacy_settings_are_imported_once(tmp_path: Path) -> None:
    current = tmp_path / "Renamr"
    legacy = tmp_path / "SequenceRenamer"
    legacy.mkdir()
    remembered = tmp_path / "shots"
    remembered.mkdir()
    legacy_payload = {
        "schema_version": 1,
        "last_directory": str(remembered),
    }
    (legacy / "settings.json").write_text(
        json.dumps(legacy_payload),
        encoding="utf-8",
    )

    store = SettingsStore(current, legacy_directory=legacy)

    assert store.load().last_directory == str(remembered)
    assert json.loads(store.path.read_text(encoding="utf-8")) == legacy_payload


def test_existing_renamr_settings_are_never_replaced_by_legacy(
    tmp_path: Path,
) -> None:
    current = tmp_path / "Renamr"
    legacy = tmp_path / "SequenceRenamer"
    current.mkdir()
    legacy.mkdir()
    current_path = current / "settings.json"
    current_payload = '{"schema_version": 1, "last_directory": "new"}\n'
    current_path.write_text(current_payload, encoding="utf-8")
    (legacy / "settings.json").write_text(
        '{"schema_version": 1, "last_directory": "old"}\n',
        encoding="utf-8",
    )

    loaded = SettingsStore(current, legacy_directory=legacy).load()

    assert loaded.last_directory == "new"
    assert current_path.read_text(encoding="utf-8") == current_payload


def test_corrupt_current_settings_still_block_legacy_overwrite(tmp_path: Path) -> None:
    current = tmp_path / "Renamr"
    legacy = tmp_path / "SequenceRenamer"
    current.mkdir()
    legacy.mkdir()
    current_path = current / "settings.json"
    current_path.write_text("not-json", encoding="utf-8")
    (legacy / "settings.json").write_text(
        '{"schema_version": 1, "last_directory": "old"}\n',
        encoding="utf-8",
    )

    assert SettingsStore(current, legacy_directory=legacy).load() == AppSettings()
    assert current_path.read_text(encoding="utf-8") == "not-json"
