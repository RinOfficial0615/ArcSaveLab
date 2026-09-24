from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import uuid
from collections.abc import Iterable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._journal import FORMAT, TERMINAL_STATES, load_journal
from .errors import CommitFailed, SourceChanged
from .interface import ArtifactPlan, CommitReceipt, SaveKind


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class ArtifactPayload:
    kind: SaveKind
    source: Path
    original: bytes
    rendered: bytes


@dataclass(frozen=True)
class PreparedTransaction:
    transaction_id: str
    plans: tuple[ArtifactPlan, ...]
    payloads: tuple[ArtifactPayload, ...]
    backup_directory: Path | None
    journal_path: Path
    destination_snapshots: tuple[DestinationSnapshot, ...]


@dataclass(frozen=True)
class DestinationSnapshot:
    kind: SaveKind
    path: Path
    existed: bool
    content: bytes | None


class TransactionManager:
    """Recoverable multi-file commit implementation for local save artifacts."""

    def prepare(
        self,
        payloads: Mapping[SaveKind, ArtifactPayload],
        *,
        destination: Path | None,
        overwrite_sources: bool,
        create_backup: bool,
    ) -> PreparedTransaction:
        if not payloads:
            raise CommitFailed("no artifacts to commit")
        names = [p.source.name.casefold() for p in payloads.values()]
        if len(set(names)) != len(names) or "transaction.json" in names:
            raise CommitFailed("save filenames must be unique and not transaction.json")
        transaction_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        if overwrite_sources:
            if not create_backup:
                raise CommitFailed("in-place commits require a backup")
            if destination is not None:
                raise CommitFailed("choose either export or in-place commit, not both")
            if len({p.source.resolve().parent for p in payloads.values()}) != 1:
                raise CommitFailed(
                    "in-place commits require files in one directory; export instead"
                )
            destinations = {kind: payload.source for kind, payload in payloads.items()}
            common_parent = Path(
                os.path.commonpath([str(p.source.parent) for p in payloads.values()])
            )
            backup_directory = common_parent / ".arcsavelab-backups" / transaction_id
            journal_root = backup_directory
        else:
            if destination is None:
                first = next(iter(payloads.values())).source.parent
                destination = first / f"ArcSaveLab-output-{transaction_id}"
            destination = Path(destination)
            if destination.exists() or destination.is_symlink():
                raise CommitFailed(
                    "export requires a new directory; choose a different output path"
                )
            destinations = {
                kind: destination / payload.source.name for kind, payload in payloads.items()
            }
            backup_directory = None
            journal_root = destination

        plans = tuple(
            ArtifactPlan(
                kind=kind,
                source=payload.source,
                destination=destinations[kind],
                changed=payload.original != payload.rendered,
                before_sha256=sha256_bytes(payload.original),
                after_sha256=sha256_bytes(payload.rendered),
            )
            for kind, payload in payloads.items()
        )
        destination_snapshots = tuple(
            DestinationSnapshot(
                kind,
                path,
                path.is_file(),
                path.read_bytes() if path.is_file() else None,
            )
            for kind, path in destinations.items()
        )
        return PreparedTransaction(
            transaction_id=transaction_id,
            plans=plans,
            payloads=tuple(payloads.values()),
            backup_directory=backup_directory if (overwrite_sources and create_backup) else None,
            journal_path=journal_root / "transaction.json",
            destination_snapshots=destination_snapshots,
        )

    @staticmethod
    @contextmanager
    def _source_locks(sources: Iterable[Path]) -> Iterator[None]:
        # Every writer owns the complete set until its final journal is durable.
        with ExitStack() as stack:
            for source in sorted({p.resolve() for p in sources}):
                lock = source.with_name(f".{source.name}.arcsavelab-lock")
                try:
                    fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError as exc:
                    raise CommitFailed(f"save is locked: {lock}") from exc
                os.close(fd)
                stack.callback(lock.unlink, missing_ok=True)
            yield

    def commit(self, prepared: PreparedTransaction, *, revision: int) -> CommitReceipt:
        with self._source_locks(p.source for p in prepared.payloads):
            if prepared.backup_directory is None:
                prepared.journal_path.parent.mkdir(parents=True, exist_ok=False)
            return self._commit_locked(prepared, revision=revision)

    @staticmethod
    def _check_sqlite_quiescent(kind: SaveKind, path: Path) -> None:
        if kind is SaveKind.SCORE_DATABASE:
            for suffix in ("-wal", "-journal"):
                sidecar = Path(str(path) + suffix)
                if sidecar.exists() and sidecar.stat().st_size:
                    raise SourceChanged("SQLite journal appeared after opening the save")

    def _commit_locked(self, prepared: PreparedTransaction, *, revision: int) -> CommitReceipt:
        plan_by_kind = {plan.kind: plan for plan in prepared.plans}
        destination_by_kind = {
            snapshot.kind: snapshot for snapshot in prepared.destination_snapshots
        }
        for payload in prepared.payloads:
            current = payload.source.read_bytes()
            if sha256_bytes(current) != sha256_bytes(payload.original):
                raise SourceChanged(str(payload.source), path=str(payload.source))
            self._check_sqlite_quiescent(payload.kind, payload.source)

        prepared.journal_path.parent.mkdir(parents=True, exist_ok=True)
        backup_directory = prepared.backup_directory
        if backup_directory is not None:
            backup_directory.mkdir(parents=True, exist_ok=True)

        journal: dict[str, Any] = {
            "format": FORMAT,
            "id": prepared.transaction_id,
            "state": "preparing",
            "artifacts": [],
        }
        for payload in prepared.payloads:
            plan = plan_by_kind[payload.kind]
            backup_path = None
            if backup_directory is not None:
                backup_path = backup_directory / payload.source.name
                self._write_exact(backup_path, payload.original)
            journal["artifacts"].append(
                {
                    "kind": payload.kind.value,
                    "source": str(payload.source.resolve()),
                    "destination": str(plan.destination.resolve()),
                    "backup": str(backup_path.resolve()) if backup_path else None,
                    "before_sha256": plan.before_sha256,
                    "after_sha256": plan.after_sha256,
                    "replaced": False,
                }
            )
        self._write_journal(prepared.journal_path, journal)

        staged: dict[SaveKind, Path] = {}
        replaced: list[tuple[ArtifactPayload, ArtifactPlan]] = []
        try:
            for payload in prepared.payloads:
                plan = plan_by_kind[payload.kind]
                plan.destination.parent.mkdir(parents=True, exist_ok=True)
                fd, raw_path = tempfile.mkstemp(
                    prefix=f".{plan.destination.name}.arcsavelab-",
                    dir=plan.destination.parent,
                )
                path = Path(raw_path)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(payload.rendered)
                        stream.flush()
                        os.fsync(stream.fileno())
                except Exception:
                    path.unlink(missing_ok=True)
                    raise
                if sha256_bytes(path.read_bytes()) != plan.after_sha256:
                    raise CommitFailed("staged file hash mismatch", path=str(path))
                staged[payload.kind] = path

            journal["state"] = "replacing"
            self._write_journal(prepared.journal_path, journal)
            # Hash-bearing preferences are replaced last.
            ordered = sorted(
                prepared.payloads,
                key=lambda item: item.kind is SaveKind.PREFERENCES,
            )
            for payload in ordered:
                plan = plan_by_kind[payload.kind]
                self._check_sqlite_quiescent(payload.kind, payload.source)
                self._check_sqlite_quiescent(payload.kind, plan.destination)
                if payload.source.read_bytes() != payload.original:
                    raise SourceChanged(str(payload.source))
                snapshot = destination_by_kind[payload.kind]
                if snapshot.existed:
                    if plan.destination.read_bytes() != snapshot.content:
                        raise SourceChanged(str(plan.destination))
                elif plan.destination.exists():
                    raise SourceChanged(str(plan.destination))
                os.replace(staged[payload.kind], plan.destination)
                replaced.append((payload, plan))
                for item in journal["artifacts"]:
                    if item["kind"] == payload.kind.value:
                        item["replaced"] = True
                self._write_journal(prepared.journal_path, journal)

            for _, plan in replaced:
                if sha256_bytes(plan.destination.read_bytes()) != plan.after_sha256:
                    raise CommitFailed("post-commit hash mismatch", path=str(plan.destination))
            journal["state"] = "committed"
            self._write_journal(prepared.journal_path, journal)
        except Exception as exc:
            rollback_errors: list[str] = []
            for payload, plan in reversed(replaced):
                try:
                    destination_before = destination_by_kind[payload.kind]
                    if destination_before.existed:
                        assert destination_before.content is not None
                        self._write_replace(plan.destination, destination_before.content)
                        expected = sha256_bytes(destination_before.content)
                        if sha256_bytes(plan.destination.read_bytes()) != expected:
                            raise OSError("rollback hash mismatch")
                    else:
                        plan.destination.unlink(missing_ok=True)
                        if plan.destination.exists():
                            raise OSError("rollback could not remove new destination")
                except Exception as rollback_exc:  # pragma: no cover - rare OS failure
                    rollback_errors.append(f"{plan.destination}: {rollback_exc}")
            journal["state"] = "manual_required" if rollback_errors else "rolled_back"
            journal["error"] = str(exc)
            journal["rollback_errors"] = rollback_errors
            self._write_journal(prepared.journal_path, journal)
            for path in staged.values():
                path.unlink(missing_ok=True)
            raise CommitFailed(
                str(exc),
                recovery_manifest=str(prepared.journal_path),
                rollback_errors=rollback_errors,
            ) from exc
        finally:
            for path in staged.values():
                path.unlink(missing_ok=True)

        return CommitReceipt(
            transaction_id=prepared.transaction_id,
            revision=revision,
            artifacts=prepared.plans,
            backup_directory=backup_directory,
            verified=True,
        )

    @classmethod
    def recover_for_sources(cls, sources: Mapping[SaveKind, Path]) -> tuple[Path, ...]:
        """Recover whole journals under the same complete lock set as ordinary commits."""
        selected = {p.resolve() for p in sources.values()}
        for source in selected:
            lock = source.with_name(f".{source.name}.arcsavelab-lock")
            if lock.exists():
                raise CommitFailed(f"save is locked; close the writer before recovery: {lock}")
        journals = {
            journal
            for root in {p.parent for p in selected}
            for journal in (root / ".arcsavelab-backups").glob("*/transaction.json")
        }
        recovered = []
        for path in sorted(journals):
            journal = load_journal(path)
            if journal.state in TERMINAL_STATES or not journal.sources.intersection(selected):
                continue
            with cls._source_locks(journal.sources):
                # The full set was discovered before locks. Do not use stale membership.
                journal.check_unchanged()
                backups = journal.read_backups()
                for item in journal.artifacts:
                    cls._check_sqlite_quiescent(item.kind, item.source)
                    if item.source.exists() and sha256_bytes(item.source.read_bytes()) not in {
                        item.before_sha256,
                        item.after_sha256,
                    }:
                        raise CommitFailed(
                            "recovery destination changed externally; no files restored"
                        )
                errors = []
                for item in reversed(journal.artifacts):
                    try:
                        cls._check_sqlite_quiescent(item.kind, item.source)
                        if item.source.exists() and sha256_bytes(item.source.read_bytes()) not in {
                            item.before_sha256,
                            item.after_sha256,
                        }:
                            raise OSError("recovery destination changed externally")
                        cls._write_replace(item.source, backups[item.kind])
                        if sha256_bytes(item.source.read_bytes()) != item.before_sha256:
                            raise OSError("restored file hash mismatch")
                    except (OSError, SourceChanged) as exc:
                        errors.append(f"{item.source}: {exc}")
                journal.data["state"] = "manual_required" if errors else "recovered"
                journal.data["recovery_errors"] = errors
                cls._write_journal(path, journal.data)
                if errors:
                    raise CommitFailed(
                        "interrupted transaction recovery failed",
                        recovery_manifest=str(path),
                        recovery_errors=errors,
                    )
                recovered.append(path)
        return tuple(recovered)

    @staticmethod
    def _write_exact(path: Path, value: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())

    @classmethod
    def _write_replace(cls, path: Path, value: bytes) -> None:
        fd, raw_path = tempfile.mkstemp(prefix=f".{path.name}.restore-", dir=path.parent)
        staged = Path(raw_path)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staged, path)
        finally:
            staged.unlink(missing_ok=True)

    @classmethod
    def _write_journal(cls, path: Path, value: Mapping[str, Any]) -> None:
        data = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        cls._write_replace(path, data)


@dataclass(frozen=True)
class Backup:
    directory: Path
    files: tuple[str, ...]


def list_backups(save_directory: Path) -> tuple[Backup, ...]:
    result = []
    for path in sorted(
        (save_directory / ".arcsavelab-backups").glob("*/transaction.json"), reverse=True
    ):
        try:
            journal = load_journal(path)
            if journal.state == "committed":
                result.append(Backup(path.parent, tuple(a.backup.name for a in journal.artifacts)))
        except CommitFailed:
            continue
    return tuple(result)


def restore_backup(directory: Path) -> CommitReceipt:
    """Restore a validated complete journal, backing up the current files first."""
    journal = load_journal(directory / "transaction.json")
    if journal.state != "committed":
        raise CommitFailed("backup is not a completed transaction")
    manager = TransactionManager()
    with manager._source_locks(journal.sources):
        journal.check_unchanged()
        backups = journal.read_backups()
        payloads = {
            a.kind: ArtifactPayload(a.kind, a.source, a.source.read_bytes(), backups[a.kind])
            for a in journal.artifacts
        }
        prepared = manager.prepare(
            payloads, destination=None, overwrite_sources=True, create_backup=True
        )
        return manager._commit_locked(prepared, revision=0)
