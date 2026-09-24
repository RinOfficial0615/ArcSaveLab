"""Private persisted contract for complete in-place transaction journals."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import CommitFailed
from .interface import SaveKind

FORMAT = "arcsavelab.transaction/v1"
TERMINAL_STATES = frozenset({"committed", "rolled_back", "recovered"})
STATES = TERMINAL_STATES | {"preparing", "replacing", "manual_required"}


@dataclass(frozen=True)
class JournalArtifact:
    kind: SaveKind
    source: Path
    backup: Path
    before_sha256: str
    after_sha256: str


@dataclass(frozen=True)
class Journal:
    path: Path
    raw: bytes
    data: dict[str, Any]
    artifacts: tuple[JournalArtifact, ...]

    @property
    def sources(self) -> set[Path]:
        return {item.source for item in self.artifacts}

    @property
    def state(self) -> str:
        return str(self.data["state"])

    def check_unchanged(self) -> None:
        if self.path.read_bytes() != self.raw:
            raise CommitFailed("transaction journal changed; reopen before retrying")

    def read_backups(self) -> dict[SaveKind, bytes]:
        """Validate the entire backup set before callers perform any write."""
        payloads = {}
        for item in self.artifacts:
            value = item.backup.read_bytes()
            if hashlib.sha256(value).hexdigest() != item.before_sha256:
                raise CommitFailed(f"backup hash mismatch: {item.backup.name}")
            payloads[item.kind] = value
        return payloads


def load_journal(path: Path) -> Journal:
    """Fail closed on malformed or redirected manifests; readers share this contract."""
    try:
        path = path.resolve()
        directory = path.parent
        if directory.parent.name != ".arcsavelab-backups":
            raise ValueError("journal is not in a backup directory")
        root = directory.parent.parent
        raw = path.read_bytes()
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            raise ValueError("unknown journal format")
        if data.get("state") not in STATES or not isinstance(data.get("id"), str):
            raise ValueError("invalid journal state or identity")
        records = data.get("artifacts")
        if not isinstance(records, list) or not records:
            raise ValueError("journal has no artifacts")
        artifacts = []
        kinds: set[SaveKind] = set()
        sources: set[Path] = set()
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("journal artifact is not an object")
            kind = SaveKind(record["kind"])
            source = Path(record["source"]).resolve()
            destination = Path(record["destination"]).resolve()
            backup = Path(record["backup"]).resolve()
            if (
                source != destination
                or source.parent != root
                or backup.parent != directory
                or source.name != backup.name
                or kind in kinds
                or source in sources
            ):
                raise ValueError("journal path or artifact identity mismatch")
            digests = []
            for field in ("before_sha256", "after_sha256"):
                digest = record[field]
                if (
                    not isinstance(digest, str)
                    or len(digest) != 64
                    or any(c not in "0123456789abcdefABCDEF" for c in digest)
                ):
                    raise ValueError("invalid journal hash")
                digests.append(digest.lower())
            artifacts.append(JournalArtifact(kind, source, backup, *digests))
            kinds.add(kind)
            sources.add(source)
        return Journal(path, raw, data, tuple(artifacts))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise CommitFailed(f"invalid recovery/backup journal: {exc}") from exc
