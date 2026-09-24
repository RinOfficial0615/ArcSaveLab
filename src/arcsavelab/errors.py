from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .interface import CommitReceipt


@dataclass(frozen=True)
class Diagnostic:
    """Language-neutral issue returned through the public interface."""

    code: str
    severity: str
    message_id: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    recoverable: bool = True


class ArcSaveError(Exception):
    code = "arcsavelab.error"
    message_id = "error.unknown"

    def __init__(self, message: str = "", **details: Any) -> None:
        super().__init__(message or self.message_id)
        self.details = details


class OpenFailed(ArcSaveError):
    code = "open.failed"
    message_id = "error.open_failed"


class UnsupportedSave(OpenFailed):
    code = "open.unsupported_format"
    message_id = "error.unsupported_format"


class CapabilityUnavailable(ArcSaveError):
    code = "capability.unavailable"
    message_id = "error.capability_unavailable"


class InvalidEdit(ArcSaveError):
    code = "edit.invalid"
    message_id = "error.invalid_edit"


class StaleRevision(ArcSaveError):
    code = "edit.stale_revision"
    message_id = "error.stale_revision"


class CommitBlocked(ArcSaveError):
    code = "commit.blocked"
    message_id = "error.commit_blocked"


class SourceChanged(ArcSaveError):
    code = "commit.source_changed"
    message_id = "error.source_changed"


class CommitFailed(ArcSaveError):
    code = "commit.failed"
    message_id = "error.commit_failed"


class ReloadRequired(CommitFailed):
    """Disk writes succeeded; callers must not treat the old draft as an active session."""

    code = "workspace.reload_required"

    def __init__(self, receipt: CommitReceipt, reason: Exception) -> None:
        super().__init__(
            f"Files were written and verified, but reload failed: {reason}. "
            "The previous draft is preserved; reopen the saved files before editing."
        )
        self.receipt = receipt


class ClosedSession(ArcSaveError):
    code = "session.closed"
    message_id = "error.closed_session"


class UncommittedChanges(ArcSaveError):
    code = "session.dirty"
    message_id = "error.uncommitted_changes"
