"""Own active-session replacement, reload failure and the preserved draft lifecycle."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .backups import Backup, list_backups, restore_backup
from .errors import OpenFailed, ReloadRequired, UncommittedChanges
from .interface import (
    CommitReceipt,
    CommitRequest,
    OpenRequest,
    Operation,
    OperationReport,
    SourceSpec,
    open_save,
)
from .session import SaveSession


class WorkspaceLifecycle:
    def __init__(self, request: OpenRequest) -> None:
        self._request = request
        self.session: SaveSession | None = None
        self.suspended_session: SaveSession | None = None
        self.last_receipt: CommitReceipt | None = None
        self.reload_error: ReloadRequired | None = None

    @property
    def request(self) -> OpenRequest:
        if self.session is None:
            return self._request
        return replace(
            self.session.request,
            sources=tuple(SourceSpec(k, p) for k, p in self.session.sources.items()),
            device_id=self.session.device_id,
            user_id=self.session.user_id,
        )

    @property
    def has_uncommitted_changes(self) -> bool:
        return any(s is not None and s.dirty for s in (self.session, self.suspended_session))

    def _approve_discard(self, discard: bool) -> None:
        if self.has_uncommitted_changes and not discard:
            raise UncommittedChanges()

    def _install(self, candidate: SaveSession) -> None:
        for old in (self.session, self.suspended_session):
            if old is not None:
                old.close(discard=True)
        self.session = candidate
        self.suspended_session = None
        self._request = self.request
        self.reload_error = None

    def open(self, request: OpenRequest, *, discard: bool = False) -> None:
        self._approve_discard(discard)
        candidate = open_save(request)  # A failed open never replaces the old draft.
        self._install(candidate)
        self.last_receipt = None

    def set_locale(self, locale: str) -> None:
        self._request = replace(self.request, locale=locale)
        if self.session is not None:
            self.session.request = replace(self.session.request, locale=locale)

    def perform(self, operation: Operation) -> OperationReport:
        if self.session is None:
            raise self.reload_error or OpenFailed("open a workspace first")
        return self.session.perform(operation)

    def _suspend(self, error: ReloadRequired, request: OpenRequest) -> None:
        self._request = request
        if self.session is not None:
            self.session.suspend(error)
            self.suspended_session = self.session
        self.session = None
        self.last_receipt = error.receipt
        self.reload_error = error

    def commit(self, request: CommitRequest) -> CommitReceipt:
        if self.session is None:
            raise self.reload_error or OpenFailed("open a workspace first")
        current = self.request
        try:
            receipt = self.session.commit(request)
        except ReloadRequired as exc:
            current = replace(
                current,
                sources=tuple(SourceSpec(a.kind, a.destination) for a in exc.receipt.artifacts),
            )
            self._suspend(exc, current)
            raise
        self.last_receipt = receipt
        self._request = self.request
        return receipt

    def backups(self) -> tuple[Backup, ...]:
        if self.session is None:
            return ()
        roots = {p.parent for p in self.session.sources.values()}
        return tuple(backup for root in sorted(roots) for backup in list_backups(root))

    def restore(self, directory: Path, *, discard: bool = False) -> CommitReceipt:
        self._approve_discard(discard)
        current = self.request
        receipt = restore_backup(directory)
        try:
            candidate = open_save(current)
        except Exception as exc:
            failure = ReloadRequired(receipt, exc)
            self._suspend(failure, current)
            raise failure from exc
        self._install(candidate)
        self.last_receipt = receipt
        return receipt

    def close(self, *, discard: bool = False) -> None:
        self._approve_discard(discard)
        for session in (self.session, self.suspended_session):
            if session is not None:
                session.close(discard=discard)
