from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .errors import Diagnostic

if TYPE_CHECKING:
    from .session import SaveSession


class SaveKind(StrEnum):
    PREFERENCES = "preferences"
    UNLOCK_PROGRESS = "unlock_progress"
    MISSION_PROGRESS = "mission_progress"
    SCORE_DATABASE = "score_database"


class Section(StrEnum):
    SETTINGS = "settings"
    FRAGMENTS = "fragments"
    OWNERSHIP = "ownership"
    PARTNERS = "partners"
    FAVORITES = "favorites"
    STORY = "story"
    CHARACTER_SKILLS = "character_skills"
    FINALE = "finale"
    UNLOCKS = "unlocks"
    MISSIONS = "missions"
    SCORES = "scores"
    ACCOUNT_DIAGNOSTICS = "account_diagnostics"


class Availability(StrEnum):
    ENABLED = "enabled"
    READ_ONLY = "read_only"
    NEEDS_SOURCE = "needs_source"
    NEEDS_CONTEXT = "needs_context"
    EXPERT_ONLY = "expert_only"


@dataclass(frozen=True)
class SourceSpec:
    kind: SaveKind
    path: Path


@dataclass(frozen=True)
class OpenRequest:
    sources: Sequence[SourceSpec]
    game_version: str = "7.0.260c"
    locale: str = "en"
    device_id: str | None = None
    user_id: int | None = None
    expert: bool = False
    allow_version_override: bool = False


@dataclass(frozen=True)
class BrowseQuery:
    section: Section | None = None
    search: str = ""
    offset: int = 0
    limit: int = 100


@dataclass(frozen=True)
class EditorSpec:
    kind: str
    choices: tuple[tuple[Any, str], ...] = ()
    minimum: int | float | None = None
    maximum: int | float | None = None
    expert_minimum: int | float | None = None
    expert_maximum: int | float | None = None


@dataclass(frozen=True)
class FieldView:
    id: str
    label_id: str
    value: Any
    editor: EditorSpec
    availability: Availability = Availability.ENABLED
    derived: bool = False
    sensitive: bool = False
    detail_only: bool = False


@dataclass(frozen=True)
class ItemView:
    id: str
    label: str
    subtitle: str = ""
    fields: tuple[FieldView, ...] = ()
    availability: Availability = Availability.ENABLED
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class SectionView:
    section: Section
    label_id: str
    availability: Availability
    items: tuple[ItemView, ...]
    total: int
    blocked_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkspaceView:
    revision: int
    game_version: str
    sources: Mapping[SaveKind, Path]
    sections: tuple[SectionView, ...]
    diagnostics: tuple[Diagnostic, ...] = ()
    dirty: bool = False
    can_undo: bool = False
    can_redo: bool = False


@dataclass(frozen=True)
class SetField:
    item_id: str
    field_id: str
    value: Any
    expected_revision: int | None = None
    expert_override: bool = False


@dataclass(frozen=True)
class BatchSet:
    changes: Sequence[SetField]
    expected_revision: int | None = None
    description: str = "batch_edit"


@dataclass(frozen=True)
class Undo:
    expected_revision: int | None = None


@dataclass(frozen=True)
class Redo:
    expected_revision: int | None = None


@dataclass(frozen=True)
class SetDeviceIdentity:
    device_id: str
    user_id: int | None = None
    expected_revision: int | None = None


@dataclass(frozen=True)
class RepairIntegrity:
    expected_revision: int | None = None


type Operation = SetField | BatchSet | Undo | Redo | SetDeviceIdentity | RepairIntegrity


@dataclass(frozen=True)
class SemanticChange:
    section: Section
    item_label: str
    field_label_id: str
    before: Any
    after: Any
    derived: bool = False
    item_id: str = ""


@dataclass(frozen=True)
class OperationReport:
    revision: int
    changes: tuple[SemanticChange, ...]
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class ArtifactPlan:
    kind: SaveKind
    source: Path
    destination: Path
    changed: bool
    before_sha256: str
    after_sha256: str


@dataclass(frozen=True)
class CommitPreview:
    token: str
    revision: int
    semantic_changes: tuple[SemanticChange, ...]
    artifacts: tuple[ArtifactPlan, ...]
    diagnostics: tuple[Diagnostic, ...]
    can_commit: bool


@dataclass(frozen=True)
class CommitRequest:
    destination: Path | None = None
    overwrite_sources: bool = False
    create_backup: bool = True
    preview_token: str | None = None


@dataclass(frozen=True)
class CommitReceipt:
    transaction_id: str
    revision: int
    artifacts: tuple[ArtifactPlan, ...]
    backup_directory: Path | None
    verified: bool
    warnings: tuple[Diagnostic, ...] = ()


def open_save(request: OpenRequest) -> SaveSession:
    from .session import SaveSession

    return SaveSession.open(request)
