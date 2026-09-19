"""Crash-aware, two-phase file rename transactions.

The transaction engine deliberately knows nothing about the user interface.  It
accepts an already validated :class:`~sequence_renamer.models.RenamePlan`, moves
every source to a unique temporary sibling, and only then moves the temporary
files to their final destinations.  This makes swaps and longer rename cycles
safe.

The full JSON snapshot is written atomically at transaction boundaries. Small
state deltas use a checksummed append-only write-ahead log (WAL), keeping total
journal bytes linear in the number of files. A failed operation is rolled back
without overwriting unrelated files, and an incomplete journal can be recovered
on the next application start.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Iterable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Any

from .models import PlanStatus, RenamePlan, RenamePlanItem
from .settings import compatibility_state_directory


RenameFunction = Callable[[Path, Path], None]

_JOURNAL_VERSION = 1
_JOURNAL_PREFIX = "transaction-"
_JOURNAL_SUFFIX = ".json"
_WAL_SUFFIX = ".wal"
_COMPACT_STATUSES = frozenset({"completed", "rolled_back", "rollback_failed", "undone"})
_INCOMPLETE_STATUSES = frozenset(
    {
        "prepared",
        "staging",
        "staged",
        "committing",
        "rollback_pending",
        "rolling_back",
        "rollback_failed",
    }
)


class TransactionStatus(str, Enum):
    """Final state returned to callers of the transaction engine."""

    COMPLETED = "completed"
    NO_CHANGES = "no_changes"
    INVALID_PLAN = "invalid_plan"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    ROLLBACK_FAILED = "rollback_failed"
    UNDONE = "undone"
    NOTHING_TO_UNDO = "nothing_to_undo"
    RECOVERED = "recovered"
    RECOVERY_FAILED = "recovery_failed"


@dataclass(frozen=True, slots=True)
class TransactionResult:
    """Structured outcome of an execute, undo, or recovery operation."""

    status: TransactionStatus
    message: str
    transaction_id: str | None = None
    completed: tuple[RenamePlanItem, ...] = ()
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    journal_path: Path | None = None
    restored: tuple[RenamePlanItem, ...] = ()

    @classmethod
    def failure(
        cls,
        message: str,
        *,
        errors: Iterable[str] = (),
    ) -> "TransactionResult":
        """Build a generic failure result for UI/controller boundaries."""

        details = tuple(errors)
        return cls(
            status=TransactionStatus.FAILED,
            message=message,
            errors=details or (message,),
        )

    @property
    def success(self) -> bool:
        """Whether the requested high-level operation reached a safe success."""

        return self.status in {
            TransactionStatus.COMPLETED,
            TransactionStatus.NO_CHANGES,
            TransactionStatus.UNDONE,
            TransactionStatus.NOTHING_TO_UNDO,
            TransactionStatus.RECOVERED,
        }

    @property
    def completed_count(self) -> int:
        """Number of mappings applied by a successful operation."""

        return len(self.completed)

    @property
    def target_paths(self) -> tuple[Path, ...]:
        """Final paths produced by a successful operation."""

        return tuple(item.target for item in self.completed)

    @property
    def final_paths(self) -> tuple[Path, ...]:
        """Compatibility alias for paths left by execute or undo."""

        return self.target_paths

    @property
    def rollback_complete(self) -> bool:
        """Whether a failed transaction was restored exactly."""

        return self.status in {
            TransactionStatus.ROLLED_BACK,
            TransactionStatus.RECOVERED,
        }


class TransactionManager:
    """Execute and recover safe rename transactions.

    Args:
        journal_dir: Directory dedicated to transaction JSON journals.
        rename_func: Injectable primitive used for each individual move.  It is
            primarily useful for deterministic failure testing.  The function
            must have the same no-copy semantics as :func:`os.rename`.
    """

    def __init__(
        self,
        journal_dir: Path | str | None = None,
        rename_func: RenameFunction = os.rename,
    ) -> None:
        # Keep existing recovery and Undo history across the product rename.
        directory = journal_dir or (compatibility_state_directory() / "journals")
        self.journal_dir = _absolute_path(directory)
        self._rename_func = rename_func
        self._journal_cache: dict[Path, dict[str, Any]] = {}
        self._journal_sequences: dict[Path, int] = {}
        self._journal_dirty_entries: dict[Path, set[int]] = {}
        self._journal_entries_compared = 0

    @property
    def journal_entries_compared(self) -> int:
        """Number of entry objects examined while producing WAL deltas."""

        return self._journal_entries_compared

    @property
    def can_undo(self) -> bool:
        """Whether a completed, not-yet-undone rename is available."""

        journals, errors = self._load_journals()
        if errors:
            return False
        undone_ids = {
            str(journal.get("undo_of"))
            for _, journal in journals
            if journal.get("kind") == "undo"
            and journal.get("status") == "completed"
            and journal.get("undo_of")
        }
        return any(
            journal.get("kind") == "rename"
            and journal.get("status") == "completed"
            and journal.get("transaction_id") not in undone_ids
            for _, journal in journals
        )

    def execute(self, plan: RenamePlan) -> TransactionResult:
        """Execute all ready mappings in *plan* as one two-phase transaction."""

        validation_errors, items = self._validate_plan(plan)
        if validation_errors:
            return TransactionResult(
                status=TransactionStatus.INVALID_PLAN,
                message="The rename plan is not safe to execute.",
                errors=tuple(validation_errors),
            )

        if not items:
            return TransactionResult(
                status=TransactionStatus.NO_CHANGES,
                message="There are no files to rename.",
            )

        return self._execute_items(items, kind="rename", undo_of=None)

    def undo_last(self) -> TransactionResult:
        """Undo the most recent completed rename transaction, if one exists."""

        journals, load_errors = self._load_journals()
        if load_errors:
            return TransactionResult(
                status=TransactionStatus.FAILED,
                message="The transaction history could not be read safely.",
                errors=tuple(load_errors),
            )

        undone_ids = {
            str(journal.get("undo_of"))
            for _, journal in journals
            if journal.get("kind") == "undo"
            and journal.get("status") == "completed"
            and journal.get("undo_of")
        }
        candidates = [
            (path, journal)
            for path, journal in journals
            if journal.get("kind") == "rename"
            and journal.get("status") == "completed"
            and journal.get("transaction_id") not in undone_ids
        ]
        if not candidates:
            return TransactionResult(
                status=TransactionStatus.NOTHING_TO_UNDO,
                message="There is no completed rename transaction to undo.",
            )

        original_path, original = max(
            candidates,
            key=lambda pair: str(
                pair[1].get("completed_at") or pair[1].get("created_at") or ""
            ),
        )
        identity_errors = _undo_identity_errors(original.get("entries"))
        if identity_errors:
            return TransactionResult(
                status=TransactionStatus.INVALID_PLAN,
                message=(
                    "The last rename cannot be undone because one or more "
                    "target files changed. No files were changed."
                ),
                transaction_id=_optional_string(original.get("transaction_id")),
                errors=tuple(identity_errors),
                journal_path=original_path,
            )
        try:
            inverse_items = tuple(
                _item_from_entry(entry, inverse=True)
                for entry in original.get("entries", [])
            )
        except (KeyError, TypeError, ValueError) as exc:
            return TransactionResult(
                status=TransactionStatus.FAILED,
                message="The last transaction journal is invalid and cannot be undone.",
                transaction_id=_optional_string(original.get("transaction_id")),
                errors=(f"Invalid undo journal data: {exc}",),
                journal_path=original_path,
            )

        inverse_plan = RenamePlan(items=inverse_items)
        validation_errors, ready_items = self._validate_plan(inverse_plan)
        if validation_errors:
            return TransactionResult(
                status=TransactionStatus.INVALID_PLAN,
                message="The last rename cannot be undone safely.",
                transaction_id=_optional_string(original.get("transaction_id")),
                errors=tuple(validation_errors),
                journal_path=original_path,
            )

        undo_result = self._execute_items(
            ready_items,
            kind="undo",
            undo_of=str(original["transaction_id"]),
            expected_fingerprints=tuple(
                dict(entry["fingerprint"]) for entry in original["entries"]
            ),
        )
        if undo_result.status is not TransactionStatus.COMPLETED:
            return undo_result

        warnings = list(undo_result.warnings)
        original["status"] = "undone"
        original["undone_at"] = _utc_now()
        original["undone_by"] = undo_result.transaction_id
        try:
            self._write_journal(original_path, original)
        except OSError as exc:
            # The completed undo journal is authoritative, so another undo will
            # still be prevented even when this convenience update fails.
            warnings.append(f"Could not mark the original journal as undone: {exc}")

        return TransactionResult(
            status=TransactionStatus.UNDONE,
            message=f"Undid {len(undo_result.completed)} renamed file(s).",
            transaction_id=undo_result.transaction_id,
            completed=undo_result.completed,
            errors=undo_result.errors,
            warnings=tuple(warnings),
            journal_path=undo_result.journal_path,
        )

    def recover_incomplete(self) -> tuple[TransactionResult, ...]:
        """Roll back every incomplete journal found in the journal directory.

        Recovery is intentionally conservative: it never overwrites an existing
        path.  If exact restoration is impossible, managed files remain at their
        recorded temporary locations and the result reports every obstruction.
        """

        journals, load_errors = self._load_journals()
        results: list[TransactionResult] = []
        for error in load_errors:
            results.append(
                TransactionResult(
                    status=TransactionStatus.RECOVERY_FAILED,
                    message="A transaction journal could not be read.",
                    errors=(error,),
                )
            )

        # A completed undo journal is sufficient proof that its original rename
        # has been undone, even if the final metadata update was interrupted.
        by_id = {
            str(journal.get("transaction_id")): (path, journal)
            for path, journal in journals
            if journal.get("transaction_id")
        }
        for _, journal in journals:
            if journal.get("kind") != "undo" or journal.get("status") != "completed":
                continue
            undo_of = journal.get("undo_of")
            if not undo_of or str(undo_of) not in by_id:
                continue
            original_path, original = by_id[str(undo_of)]
            if original.get("status") == "completed":
                original["status"] = "undone"
                original["undone_at"] = journal.get("completed_at") or _utc_now()
                original["undone_by"] = journal.get("transaction_id")
                try:
                    self._write_journal(original_path, original)
                except OSError as exc:
                    results.append(
                        TransactionResult(
                            status=TransactionStatus.RECOVERY_FAILED,
                            message="Undo metadata could not be repaired.",
                            transaction_id=_optional_string(undo_of),
                            errors=(str(exc),),
                            journal_path=original_path,
                        )
                    )

        incomplete = [
            (path, journal)
            for path, journal in journals
            if journal.get("status") in _INCOMPLETE_STATUSES
        ]
        incomplete.sort(
            key=lambda pair: str(
                pair[1].get("updated_at") or pair[1].get("created_at") or ""
            ),
            reverse=True,
        )
        for path, journal in incomplete:
            rollback_errors = self._rollback_to_sources(path, journal)
            try:
                items = tuple(
                    _item_from_entry(entry) for entry in journal.get("entries", [])
                )
            except (KeyError, TypeError, ValueError):
                items = ()
            transaction_id = _optional_string(journal.get("transaction_id"))
            if rollback_errors:
                results.append(
                    TransactionResult(
                        status=TransactionStatus.RECOVERY_FAILED,
                        message="The interrupted transaction could not be restored exactly.",
                        transaction_id=transaction_id,
                        errors=tuple(rollback_errors),
                        journal_path=path,
                    )
                )
            else:
                results.append(
                    TransactionResult(
                        status=TransactionStatus.RECOVERED,
                        message=f"Recovered {len(items)} file(s) from an interrupted transaction.",
                        transaction_id=transaction_id,
                        restored=items,
                        journal_path=path,
                    )
                )
        return tuple(results)

    def _execute_items(
        self,
        items: tuple[RenamePlanItem, ...],
        *,
        kind: str,
        undo_of: str | None,
        expected_fingerprints: tuple[dict[str, int], ...] | None = None,
    ) -> TransactionResult:
        transaction_id = uuid.uuid4().hex
        if expected_fingerprints is not None and len(expected_fingerprints) != len(
            items
        ):
            return TransactionResult(
                status=TransactionStatus.FAILED,
                message="Trusted source identities are incomplete; no files were changed.",
                transaction_id=transaction_id,
                errors=("Expected fingerprint count does not match the rename plan.",),
            )
        fingerprints: list[dict[str, int]] = []
        for index, item in enumerate(items):
            try:
                fingerprint = _file_fingerprint(item.source)
            except OSError as exc:
                return TransactionResult(
                    status=TransactionStatus.FAILED,
                    message=(
                        "Source file identity could not be verified; "
                        "no files were changed."
                    ),
                    transaction_id=transaction_id,
                    errors=(
                        f"Could not read source metadata for {item.source}: {exc}",
                    ),
                )
            if (
                expected_fingerprints is not None
                and fingerprint != expected_fingerprints[index]
            ):
                return TransactionResult(
                    status=TransactionStatus.FAILED,
                    message="A source file changed before undo; no files were changed.",
                    transaction_id=transaction_id,
                    errors=(
                        f"Source identity no longer matches the completed rename: {item.source}",
                    ),
                )
            fingerprints.append(fingerprint)

        try:
            self.journal_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return TransactionResult(
                status=TransactionStatus.FAILED,
                message="The transaction journal directory could not be created.",
                transaction_id=transaction_id,
                errors=(str(exc),),
            )

        journal_path = self.journal_dir / (
            f"{_JOURNAL_PREFIX}{transaction_id}{_JOURNAL_SUFFIX}"
        )
        reserved_keys = {
            _path_key(path) for item in items for path in (item.source, item.target)
        }
        entries: list[dict[str, Any]] = []
        for item, fingerprint in zip(items, fingerprints, strict=True):
            temporary = self._unique_temporary_sibling(
                item.source.parent,
                reserved_keys,
                label="stage",
            )
            reserved_keys.add(_path_key(temporary))
            entries.append(
                {
                    "source": str(item.source),
                    "target": str(item.target),
                    "temporary": str(temporary),
                    "rollback_temporary": None,
                    "selection_index": item.selection_index,
                    "frame_number": item.frame_number,
                    "fingerprint": fingerprint,
                    "state": "source",
                }
            )

        journal: dict[str, Any] = {
            "version": _JOURNAL_VERSION,
            "transaction_id": transaction_id,
            "kind": kind,
            "undo_of": undo_of,
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "status": "prepared",
            "operation": None,
            "error": None,
            "rollback_errors": [],
            "entries": entries,
        }
        try:
            self._write_journal(journal_path, journal)
        except OSError as exc:
            return TransactionResult(
                status=TransactionStatus.FAILED,
                message="The transaction journal could not be created; no files were changed.",
                transaction_id=transaction_id,
                errors=(str(exc),),
                journal_path=journal_path,
            )

        try:
            for index, entry in enumerate(entries):
                source = Path(entry["source"])
                temporary = Path(entry["temporary"])
                if not _entry_fingerprint_matches(entry, source):
                    raise OSError(
                        f"Source changed after transaction preparation: {source}"
                    )
                self._record_operation(
                    journal_path,
                    journal,
                    status="staging",
                    kind="stage",
                    index=index,
                    source=source,
                    target=temporary,
                    destination_state="temporary",
                )
                self._move_without_overwrite(source, temporary)
                entry["state"] = "temporary"
                self._mark_entry_dirty(journal_path, index)
                journal["operation"] = None

            journal["status"] = "staged"
            self._write_journal(journal_path, journal)

            # Catch a target created while staging before any final name is used.
            for entry in entries:
                target = Path(entry["target"])
                if _path_exists(target):
                    raise FileExistsError(
                        f"Target appeared during the transaction: {target}"
                    )

            for index, entry in enumerate(entries):
                temporary = Path(entry["temporary"])
                target = Path(entry["target"])
                self._record_operation(
                    journal_path,
                    journal,
                    status="committing",
                    kind="commit",
                    index=index,
                    source=temporary,
                    target=target,
                    destination_state="target",
                )
                self._move_without_overwrite(temporary, target)
                entry["state"] = "target"
                self._mark_entry_dirty(journal_path, index)
                journal["operation"] = None

            journal["status"] = "completed"
            journal["completed_at"] = _utc_now()
            journal["operation"] = None
            self._write_journal(journal_path, journal)
            return TransactionResult(
                status=TransactionStatus.COMPLETED,
                message=f"Renamed {len(items)} file(s).",
                transaction_id=transaction_id,
                completed=items,
                journal_path=journal_path,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self._reconcile_recorded_operation(journal_path, journal)
            original_error = f"Rename transaction failed: {exc}"
            journal["error"] = original_error
            journal["status"] = "rollback_pending"
            journal["operation"] = None
            journal_write_errors: list[str] = []
            try:
                self._write_journal(journal_path, journal)
            except OSError as journal_exc:
                journal_write_errors.append(
                    f"Could not record the failure before rollback: {journal_exc}"
                )

            rollback_errors = self._rollback_to_sources(journal_path, journal)
            all_errors = [original_error, *journal_write_errors, *rollback_errors]
            if rollback_errors:
                return TransactionResult(
                    status=TransactionStatus.ROLLBACK_FAILED,
                    message="The rename failed and exact restoration needs recovery.",
                    transaction_id=transaction_id,
                    errors=tuple(all_errors),
                    journal_path=journal_path,
                )
            return TransactionResult(
                status=TransactionStatus.ROLLED_BACK,
                message="The rename failed; every source file was restored.",
                transaction_id=transaction_id,
                errors=tuple(all_errors),
                journal_path=journal_path,
                restored=items,
            )

    def _validate_plan(
        self,
        plan: RenamePlan,
    ) -> tuple[list[str], tuple[RenamePlanItem, ...]]:
        errors = list(plan.errors)
        errors.extend(
            item.message or f"Invalid rename mapping: {item.source}"
            for item in plan.error_items
        )
        if errors:
            return _deduplicate(errors), ()

        items = tuple(
            RenamePlanItem(
                source=_absolute_path(item.source),
                target=_absolute_path(item.target),
                selection_index=item.selection_index,
                frame_number=item.frame_number,
                status=PlanStatus.READY,
                message=item.message,
            )
            for item in plan.ready_items
        )
        if not items:
            return [], ()

        source_keys: dict[str, Path] = {}
        target_keys: dict[str, Path] = {}
        for item in items:
            source_key = _path_key(item.source)
            target_key = _path_key(item.target)
            # A spelling-only case change is a real rename on Windows.  The
            # two-phase staging move makes it safe even though both spellings
            # share the same case-insensitive path key.
            if source_key == target_key and os.fspath(item.source) == os.fspath(
                item.target
            ):
                errors.append(f"Source and target are the same path: {item.source}")
            if source_key in source_keys:
                errors.append(f"Source is listed more than once: {item.source}")
            else:
                source_keys[source_key] = item.source
            if target_key in target_keys:
                errors.append(
                    f"More than one file would use this target: {item.target}"
                )
            else:
                target_keys[target_key] = item.target

        for item in items:
            source_key = _path_key(item.source)
            target_key = _path_key(item.target)
            if not _path_exists(item.source):
                errors.append(f"Source file does not exist: {item.source}")
            elif item.source.is_dir():
                errors.append(
                    f"Directories cannot be renamed by this tool: {item.source}"
                )
            if not item.target.parent.is_dir():
                errors.append(f"Target directory does not exist: {item.target.parent}")
            if (
                _path_exists(item.target)
                and target_key not in source_keys
                and target_key != source_key
            ):
                errors.append(f"Target already exists: {item.target}")

        return _deduplicate(errors), items if not errors else ()

    def _rollback_to_sources(
        self,
        journal_path: Path,
        journal: dict[str, Any],
    ) -> list[str]:
        errors: list[str] = []
        self._reconcile_recorded_operation(journal_path, journal)
        entries = journal.get("entries")
        if not isinstance(entries, list):
            return ["The journal has no valid transaction entries."]

        journal["operation"] = None
        journal["status"] = "rolling_back"
        try:
            self._write_journal(journal_path, journal)
        except OSError as exc:
            errors.append(f"Could not record rollback start: {exc}")

        reserved_keys = {
            _path_key(Path(str(entry[field])))
            for entry in entries
            if isinstance(entry, dict)
            for field in ("source", "target", "temporary", "rollback_temporary")
            if entry.get(field)
        }

        # First evacuate every managed file that is not already at its source.
        # This second two-phase move makes rollback exact for swaps and cycles.
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                errors.append(f"Journal entry {index} is invalid.")
                continue
            state = self._locate_entry_state(entry)
            if state is None:
                errors.append(
                    f"Managed file for source {entry.get('source', '<unknown>')} could not be located."
                )
                continue
            if entry.get("state") != state:
                entry["state"] = state
                self._mark_entry_dirty(journal_path, index)
            if state == "source":
                continue
            if state == "rollback_temporary":
                continue

            current = _entry_path(entry, state)
            rollback_value = entry.get("rollback_temporary")
            if rollback_value:
                rollback_temporary = Path(str(rollback_value))
            else:
                rollback_temporary = self._unique_temporary_sibling(
                    current.parent,
                    reserved_keys,
                    label="rollback",
                )
                entry["rollback_temporary"] = str(rollback_temporary)
                self._mark_entry_dirty(journal_path, index)
                reserved_keys.add(_path_key(rollback_temporary))

            try:
                self._record_operation(
                    journal_path,
                    journal,
                    status="rolling_back",
                    kind="rollback_stage",
                    index=index,
                    source=current,
                    target=rollback_temporary,
                    destination_state="rollback_temporary",
                )
                self._move_without_overwrite(current, rollback_temporary)
                entry["state"] = "rollback_temporary"
                self._mark_entry_dirty(journal_path, index)
                journal["operation"] = None
            except (OSError, RuntimeError, ValueError) as exc:
                self._reconcile_recorded_operation(journal_path, journal)
                journal["operation"] = None
                errors.append(f"Could not secure {current} for rollback: {exc}")

        # Once all possible current names are free, restore original sources.
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            state = self._locate_entry_state(entry)
            if state is not None and entry.get("state") != state:
                entry["state"] = state
                self._mark_entry_dirty(journal_path, index)
            if entry.get("state") == "source":
                continue
            if entry.get("state") != "rollback_temporary":
                continue
            rollback_temporary = Path(str(entry["rollback_temporary"]))
            source = Path(str(entry["source"]))
            try:
                self._record_operation(
                    journal_path,
                    journal,
                    status="rolling_back",
                    kind="rollback_restore",
                    index=index,
                    source=rollback_temporary,
                    target=source,
                    destination_state="source",
                )
                self._move_without_overwrite(rollback_temporary, source)
                entry["state"] = "source"
                self._mark_entry_dirty(journal_path, index)
                journal["operation"] = None
            except (OSError, RuntimeError, ValueError) as exc:
                self._reconcile_recorded_operation(journal_path, journal)
                journal["operation"] = None
                errors.append(f"Could not restore {source}: {exc}")

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            source_value = entry.get("source")
            if not source_value:
                errors.append("A journal entry is missing its source path.")
                continue
            source = Path(str(source_value))
            if (
                entry.get("state") != "source"
                or not _path_exists(source)
                or not _entry_fingerprint_matches(entry, source)
            ):
                errors.append(f"Source was not restored exactly: {source}")

        errors = _deduplicate(errors)
        journal["operation"] = None
        journal["rollback_errors"] = errors
        journal["status"] = "rollback_failed" if errors else "rolled_back"
        journal["rolled_back_at"] = _utc_now() if not errors else None
        try:
            self._write_journal(journal_path, journal)
        except OSError as exc:
            errors.append(f"Could not record final rollback state: {exc}")
        return _deduplicate(errors)

    def _record_operation(
        self,
        journal_path: Path,
        journal: dict[str, Any],
        *,
        status: str,
        kind: str,
        index: int,
        source: Path,
        target: Path,
        destination_state: str,
    ) -> None:
        journal["status"] = status
        journal["operation"] = {
            "kind": kind,
            "index": index,
            "from": str(source),
            "to": str(target),
            "destination_state": destination_state,
        }
        self._write_journal(journal_path, journal)

    def _reconcile_recorded_operation(
        self,
        journal_path: Path,
        journal: dict[str, Any],
    ) -> None:
        operation = journal.get("operation")
        entries = journal.get("entries")
        if not isinstance(operation, dict) or not isinstance(entries, list):
            return
        try:
            index = int(operation["index"])
            entry = entries[index]
        except (IndexError, KeyError, TypeError, ValueError):
            return
        if not isinstance(entry, dict):
            return
        # The intent is persisted before a move.  If the process stopped after
        # the move but before the next journal write, the stored file identity
        # resolves the otherwise unavoidable crash window.  Mere path presence
        # is not enough: an unrelated file may have appeared at the source.
        located_state = self._locate_entry_state(entry)
        if located_state is not None and entry.get("state") != located_state:
            entry["state"] = located_state
            self._mark_entry_dirty(journal_path, index)

    def _locate_entry_state(self, entry: dict[str, Any]) -> str | None:
        expected_state = str(entry.get("state") or "")
        fingerprint = entry.get("fingerprint")
        has_fingerprint = isinstance(fingerprint, dict)
        candidates_by_path: dict[str, list[str]] = {}
        for state in (
            "rollback_temporary",
            "temporary",
            "target",
            "source",
        ):
            path = _entry_path(entry, state, required=False)
            if path is None or not _path_exists(path):
                continue
            if has_fingerprint and not _fingerprint_matches(path, fingerprint):
                continue
            candidates_by_path.setdefault(_path_key(path), []).append(state)

        # Multiple matching paths can be hard links or an indistinguishable
        # replacement.  Choosing either could overwrite the actual source, so
        # recovery must stop and report the ambiguity.
        if len(candidates_by_path) != 1:
            return None
        states = next(iter(candidates_by_path.values()))
        if expected_state in states:
            return expected_state
        for state in ("rollback_temporary", "temporary", "target", "source"):
            if state in states:
                return state
        return None

    def _move_without_overwrite(self, source: Path, target: Path) -> None:
        if not _path_exists(source):
            raise FileNotFoundError(f"Source disappeared before rename: {source}")
        if _path_exists(target):
            raise FileExistsError(f"Refusing to overwrite existing path: {target}")
        self._rename_func(source, target)
        if _path_exists(source) or not _path_exists(target):
            raise OSError(f"Rename primitive did not move {source} to {target}")

    def _unique_temporary_sibling(
        self,
        parent: Path,
        reserved_keys: set[str],
        *,
        label: str,
    ) -> Path:
        for _ in range(100):
            candidate = parent / f".sequence-renamer-{label}-{uuid.uuid4().hex}.tmp"
            if _path_key(candidate) not in reserved_keys and not _path_exists(
                candidate
            ):
                return candidate
        raise RuntimeError(f"Could not reserve a unique temporary name in {parent}")

    def _mark_entry_dirty(self, journal_path: Path, index: int) -> None:
        path = _absolute_path(journal_path)
        self._journal_dirty_entries.setdefault(path, set()).add(index)

    def _write_journal(self, path: Path, journal: dict[str, Any]) -> None:
        """Persist a journal using one snapshot plus constant-size WAL deltas."""

        path = _absolute_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        journal["updated_at"] = _utc_now()
        if not _path_exists(path):
            journal["wal_sequence"] = 0
            self._atomic_write_snapshot(path, journal)
            self._journal_cache[path] = deepcopy(journal)
            self._journal_sequences[path] = 0
            self._journal_dirty_entries[path] = set()
            return

        previous = self._journal_cache.get(path)
        sequence = self._journal_sequences.get(path)
        if previous is None or sequence is None:
            previous, sequence = self._read_journal(path)

        dirty_entries = self._journal_dirty_entries.setdefault(path, set())
        compared_indices = tuple(sorted(dirty_entries))
        event = _journal_delta(
            previous,
            journal,
            sequence + 1,
            dirty_entry_indices=compared_indices,
        )
        self._journal_entries_compared += len(compared_indices)
        try:
            self._append_wal_event(_wal_path(path), event)
        except OSError:
            # A full event may have reached disk before an fsync error. Force
            # the next attempt to replay the WAL rather than reusing stale state.
            self._journal_cache.pop(path, None)
            self._journal_sequences.pop(path, None)
            raise

        sequence += 1
        journal["wal_sequence"] = sequence
        _apply_wal_event(previous, event)
        previous["wal_sequence"] = sequence
        self._journal_cache[path] = previous
        self._journal_sequences[path] = sequence
        dirty_entries.difference_update(compared_indices)

        if journal.get("status") in _COMPACT_STATUSES:
            # WAL is durable first. If the process stops around compaction, the
            # base sequence makes replay idempotent whether the old WAL remains
            # or not.
            self._atomic_write_snapshot(path, previous)
            try:
                _wal_path(path).unlink(missing_ok=True)
            except OSError:
                pass

    def _atomic_write_snapshot(self, path: Path, journal: dict[str, Any]) -> None:
        temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(journal, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _append_wal_event(self, path: Path, event: dict[str, Any]) -> None:
        payload = (
            json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        with path.open("ab") as stream:
            written = stream.write(payload)
            if written != len(payload):
                raise OSError(
                    f"Incomplete transaction WAL write: {written} of {len(payload)} bytes"
                )
            stream.flush()
            os.fsync(stream.fileno())

    def _read_journal(self, path: Path) -> tuple[dict[str, Any], int]:
        with path.open("r", encoding="utf-8") as stream:
            journal = json.load(stream)
        if not isinstance(journal, dict):
            raise ValueError("journal root is not an object")
        if journal.get("version") != _JOURNAL_VERSION:
            raise ValueError("unsupported journal version")

        base_sequence = int(journal.get("wal_sequence", 0))
        sequence = base_sequence
        wal_path = _wal_path(path)
        if _path_exists(wal_path):
            raw = wal_path.read_bytes()
            valid_length = 0
            for line_number, raw_line in enumerate(
                raw.splitlines(keepends=True), start=1
            ):
                if not raw_line.endswith(b"\n"):
                    # A torn trailing record cannot have authorized a following
                    # file move. Its preceding intent remains sufficient to infer
                    # any move that did complete.
                    with wal_path.open("r+b") as stream:
                        stream.truncate(valid_length)
                        stream.flush()
                        os.fsync(stream.fileno())
                    break
                valid_length += len(raw_line)
                try:
                    event = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"invalid WAL record at line {line_number}: {exc}"
                    ) from exc
                _validate_wal_event(event)
                event_sequence = int(event["sequence"])
                if event_sequence <= base_sequence:
                    continue
                if event_sequence != sequence + 1:
                    raise ValueError(
                        f"non-contiguous WAL sequence {event_sequence}; expected {sequence + 1}"
                    )
                _apply_wal_event(journal, event)
                sequence = event_sequence

        journal["wal_sequence"] = sequence
        return journal, sequence

    def _load_journals(
        self,
    ) -> tuple[list[tuple[Path, dict[str, Any]]], list[str]]:
        if not self.journal_dir.exists():
            return [], []
        journals: list[tuple[Path, dict[str, Any]]] = []
        errors: list[str] = []
        try:
            paths = tuple(self.journal_dir.glob(f"{_JOURNAL_PREFIX}*{_JOURNAL_SUFFIX}"))
        except OSError as exc:
            return [], [f"Could not list transaction journals: {exc}"]
        for path in paths:
            try:
                journal, sequence = self._read_journal(path)
                journals.append((path, journal))
                self._journal_cache[path] = deepcopy(journal)
                self._journal_sequences[path] = sequence
                self._journal_dirty_entries[path] = set()
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                errors.append(f"Could not read {path}: {exc}")
        return journals, errors


def execute_plan(
    plan: RenamePlan,
    journal_dir: Path | str,
    *,
    rename_func: RenameFunction = os.rename,
) -> TransactionResult:
    """Convenience wrapper around :meth:`TransactionManager.execute`."""

    return TransactionManager(journal_dir, rename_func).execute(plan)


def undo_last_transaction(
    journal_dir: Path | str,
    *,
    rename_func: RenameFunction = os.rename,
) -> TransactionResult:
    """Undo the most recent completed transaction in *journal_dir*."""

    return TransactionManager(journal_dir, rename_func).undo_last()


def recover_incomplete_transactions(
    journal_dir: Path | str,
    *,
    rename_func: RenameFunction = os.rename,
) -> tuple[TransactionResult, ...]:
    """Recover all interrupted transactions in *journal_dir*."""

    return TransactionManager(journal_dir, rename_func).recover_incomplete()


def _absolute_path(path: Path | str) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _path_exists(path: Path) -> bool:
    return os.path.lexists(os.fspath(path))


def _wal_path(journal_path: Path) -> Path:
    return journal_path.with_suffix(_WAL_SUFFIX)


def _file_fingerprint(path: Path) -> dict[str, int]:
    """Return identity fields that remain stable across a same-volume rename."""

    stat_result = os.stat(path, follow_symlinks=False)
    return {
        "device": int(stat_result.st_dev),
        "inode": int(stat_result.st_ino),
        "mode": int(stat_result.st_mode),
        "size": int(stat_result.st_size),
        "mtime_ns": int(stat_result.st_mtime_ns),
    }


def _fingerprint_matches(path: Path, expected: object) -> bool:
    if not isinstance(expected, dict):
        return False
    try:
        actual = _file_fingerprint(path)
    except OSError:
        return False
    return actual == expected


def _entry_fingerprint_matches(entry: dict[str, Any], path: Path) -> bool:
    fingerprint = entry.get("fingerprint")
    if fingerprint is None:
        # Legacy version-1 journals did not record file identity. Their recovery
        # remains conservative whenever more than one candidate path exists.
        return _path_exists(path)
    return _fingerprint_matches(path, fingerprint)


def _undo_identity_errors(entries: object) -> list[str]:
    if not isinstance(entries, list) or not entries:
        return ["The completed transaction has no valid file identity records."]

    errors: list[str] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"Undo entry {index} is invalid.")
            continue
        target_value = entry.get("target")
        fingerprint = entry.get("fingerprint")
        if not target_value or not isinstance(fingerprint, dict):
            errors.append(
                f"Undo entry {index} has no trusted target identity metadata."
            )
            continue
        target = Path(str(target_value))
        if not _path_exists(target):
            errors.append(
                f"Undo cancelled because the current target is missing: {target}"
            )
        elif not _fingerprint_matches(target, fingerprint):
            errors.append(
                "Undo cancelled because this target was replaced or modified: "
                f"{target}. Restore the expected file or rename it manually."
            )
    return errors


def _journal_delta(
    previous: dict[str, Any],
    current: dict[str, Any],
    sequence: int,
    *,
    dirty_entry_indices: Iterable[int],
) -> dict[str, Any]:
    """Build a WAL delta by examining only explicitly dirty entries."""

    ignored_keys = {"entries", "wal_sequence"}
    set_values: dict[str, Any] = {}
    unset_values: list[str] = []
    for key in previous.keys() | current.keys():
        if key in ignored_keys:
            continue
        if key not in current:
            unset_values.append(key)
        elif key not in previous or previous[key] != current[key]:
            set_values[key] = current[key]

    previous_entries = previous.get("entries")
    current_entries = current.get("entries")
    if not isinstance(previous_entries, list) or not isinstance(current_entries, list):
        raise ValueError("journal entries must be lists")
    if len(previous_entries) != len(current_entries):
        raise ValueError("journal entry count cannot change during a transaction")

    entry_updates: dict[str, dict[str, Any]] = {}
    for index in dirty_entry_indices:
        if index < 0 or index >= len(current_entries):
            raise ValueError(f"dirty journal entry index is invalid: {index}")
        old_entry = previous_entries[index]
        new_entry = current_entries[index]
        if not isinstance(old_entry, dict) or not isinstance(new_entry, dict):
            raise ValueError(f"journal entry {index} must be an object")
        changes: dict[str, Any] = {}
        for key in old_entry.keys() | new_entry.keys():
            if key not in new_entry:
                changes[key] = {"deleted": True}
            elif key not in old_entry or old_entry[key] != new_entry[key]:
                changes[key] = {"value": new_entry[key]}
        if changes:
            entry_updates[str(index)] = changes

    event: dict[str, Any] = {
        "version": _JOURNAL_VERSION,
        "sequence": sequence,
        "set": set_values,
        "unset": sorted(unset_values),
        "entries": entry_updates,
    }
    event["checksum"] = _wal_checksum(event)
    return event


def _wal_checksum(event: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in event.items() if key != "checksum"}
    payload = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _validate_wal_event(event: object) -> None:
    if not isinstance(event, dict):
        raise ValueError("WAL record is not an object")
    if event.get("version") != _JOURNAL_VERSION:
        raise ValueError("unsupported WAL version")
    sequence = event.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise ValueError("invalid WAL sequence")
    if not isinstance(event.get("set"), dict):
        raise ValueError("invalid WAL top-level delta")
    if not isinstance(event.get("unset"), list):
        raise ValueError("invalid WAL unset delta")
    if not isinstance(event.get("entries"), dict):
        raise ValueError("invalid WAL entry delta")
    checksum = event.get("checksum")
    if not isinstance(checksum, str) or checksum != _wal_checksum(event):
        raise ValueError("WAL checksum mismatch")


def _apply_wal_event(journal: dict[str, Any], event: dict[str, Any]) -> None:
    for key in event["unset"]:
        if not isinstance(key, str):
            raise ValueError("invalid WAL unset key")
        journal.pop(key, None)
    for key, value in event["set"].items():
        journal[str(key)] = value

    entries = journal.get("entries")
    if not isinstance(entries, list):
        raise ValueError("journal entries must be a list")
    for raw_index, changes in event["entries"].items():
        try:
            index = int(raw_index)
            entry = entries[index]
        except (IndexError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid WAL entry index: {raw_index}") from exc
        if not isinstance(entry, dict) or not isinstance(changes, dict):
            raise ValueError(f"invalid WAL entry update: {raw_index}")
        for key, change in changes.items():
            if not isinstance(change, dict):
                raise ValueError(f"invalid WAL field update: {raw_index}.{key}")
            if change.get("deleted") is True:
                entry.pop(str(key), None)
            elif "value" in change:
                entry[str(key)] = change["value"]
            else:
                raise ValueError(f"invalid WAL field operation: {raw_index}.{key}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _optional_string(value: object) -> str | None:
    return None if value is None else str(value)


def _deduplicate(messages: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(message) for message in messages if message))


def _entry_path(
    entry: dict[str, Any],
    state: str,
    *,
    required: bool = True,
) -> Path | None:
    field = {
        "source": "source",
        "temporary": "temporary",
        "target": "target",
        "rollback_temporary": "rollback_temporary",
    }[state]
    value = entry.get(field)
    if value:
        return Path(str(value))
    if required:
        raise ValueError(f"Journal entry has no {field} path")
    return None


def _item_from_entry(
    entry: dict[str, Any],
    *,
    inverse: bool = False,
) -> RenamePlanItem:
    source = Path(str(entry["target"] if inverse else entry["source"]))
    target = Path(str(entry["source"] if inverse else entry["target"]))
    frame_value = entry.get("frame_number")
    frame_number = None if frame_value is None else int(frame_value)
    return RenamePlanItem(
        source=source,
        target=target,
        selection_index=int(entry.get("selection_index", 0)),
        frame_number=frame_number,
        status=PlanStatus.READY,
    )


__all__ = [
    "RenameFunction",
    "TransactionManager",
    "TransactionResult",
    "TransactionStatus",
    "execute_plan",
    "recover_incomplete_transactions",
    "undo_last_transaction",
]
