"""Small, privacy-conscious persistent settings store."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


APP_DIR_NAME = "Renamr"
LEGACY_APP_DIR_NAME = "SequenceRenamer"


def app_data_directory() -> Path:
    """Return the per-user writable application data directory."""

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / APP_DIR_NAME
    return Path.home() / ".renamr"


def compatibility_state_directory() -> Path:
    """Return the legacy state directory retained for upgrade safety.

    Transaction journals and the single-instance lock intentionally continue
    to use this hidden location.  Sharing that identity with older releases
    prevents concurrent old/new processes and preserves crash recovery and
    Undo history without moving live safety data.
    """

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / LEGACY_APP_DIR_NAME
    return Path.home() / ".sequence-renamer"


def default_browse_directory() -> Path:
    """Return a predictable existing directory for the first file dialog."""

    pictures = Path.home() / "Pictures"
    return pictures if pictures.is_dir() else Path.home()


@dataclass(slots=True)
class AppSettings:
    """Settings intentionally limited to non-sensitive convenience values."""

    schema_version: int = 1
    last_directory: str | None = None


class SettingsStore:
    """Load and atomically save the application's JSON settings."""

    def __init__(
        self,
        directory: Path | None = None,
        *,
        legacy_directory: Path | None = None,
    ) -> None:
        use_default_directory = directory is None
        self.directory = directory or app_data_directory()
        self.path = self.directory / "settings.json"
        if legacy_directory is not None:
            self.legacy_path: Path | None = legacy_directory / "settings.json"
        elif use_default_directory:
            self.legacy_path = compatibility_state_directory() / "settings.json"
        else:
            self.legacy_path = None

    def load(self) -> AppSettings:
        """Load current settings, importing a valid legacy file once.

        The current Renamr file always wins, even if it is malformed.  This is
        deliberate: an older settings file must never overwrite state already
        created by a newer release.
        """

        if self.path.exists():
            return self._load_path(self.path) or AppSettings()

        if self.legacy_path is None:
            return AppSettings()
        legacy_settings = self._load_path(self.legacy_path)
        if legacy_settings is None:
            return AppSettings()

        try:
            created = self._save_if_missing(legacy_settings)
        except OSError:
            # Loading the remembered folder is still useful on a read-only or
            # temporarily unavailable profile.  A later run can retry safely.
            return legacy_settings

        if created:
            return legacy_settings
        # Another process created Renamr settings between the existence check
        # and the exclusive write.  Its value is authoritative.
        return self._load_path(self.path) or AppSettings()

    @staticmethod
    def _load_path(path: Path) -> AppSettings | None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return None
            last_directory = raw.get("last_directory")
            if not isinstance(last_directory, str):
                last_directory = None
            return AppSettings(last_directory=last_directory)
        except (OSError, ValueError, TypeError):
            return None

    def _save_if_missing(self, settings: AppSettings) -> bool:
        """Atomically create settings without replacing an existing file."""

        self.directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(settings), indent=2, ensure_ascii=False) + "\n"
        temporary_path = self.directory / f".settings-{uuid.uuid4().hex}.tmp"
        try:
            with temporary_path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                # A hard link publishes the fully written file atomically and
                # fails instead of replacing a Renamr settings file that won a
                # concurrent race.
                os.link(temporary_path, self.path)
                return True
            except FileExistsError:
                return False
        finally:
            try:
                temporary_path.unlink()
            except OSError:
                pass

    def save(self, settings: AppSettings) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(".json.tmp")
        payload = json.dumps(asdict(settings), indent=2, ensure_ascii=False)
        temporary_path.write_text(payload + "\n", encoding="utf-8")
        os.replace(temporary_path, self.path)

    def browse_directory(self, settings: AppSettings) -> Path:
        if settings.last_directory:
            candidate = Path(settings.last_directory)
            if candidate.is_dir():
                return candidate
        return default_browse_directory()

    def remember_directory(self, settings: AppSettings, directory: Path) -> None:
        if directory.is_dir():
            settings.last_directory = str(directory.resolve())
            self.save(settings)
