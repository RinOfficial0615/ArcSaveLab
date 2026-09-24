from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from arcsavelab.errors import CommitFailed, SourceChanged
from arcsavelab.interface import SaveKind
from arcsavelab.transaction import ArtifactPayload, TransactionManager


@pytest.mark.parametrize("suffix", ["-wal", "-journal"])
def test_commit_rechecks_sqlite_sidecars_after_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    source = tmp_path / "st3"
    source.write_bytes(b"before")
    manager = TransactionManager()
    prepared = manager.prepare(
        {
            SaveKind.SCORE_DATABASE: ArtifactPayload(
                SaveKind.SCORE_DATABASE, source, b"before", b"after"
            )
        },
        destination=None,
        overwrite_sources=True,
        create_backup=True,
    )
    real_write = manager._write_journal

    def inject(path: Path, journal: dict[str, Any]) -> None:
        real_write(path, journal)
        if journal["state"] == "replacing":
            Path(str(source) + suffix).write_bytes(b"external SQLite transaction")

    monkeypatch.setattr(manager, "_write_journal", inject)
    with pytest.raises(CommitFailed, match="SQLite journal"):
        manager.commit(prepared, revision=1)
    assert source.read_bytes() == b"before"
    assert json.loads(prepared.journal_path.read_text())["state"] == "rolled_back"


@pytest.mark.parametrize("suffix", ["-wal", "-journal"])
def test_recovery_rejects_active_sqlite_sidecars_before_writing(
    tmp_path: Path, suffix: str
) -> None:
    source = tmp_path / "st3"
    source.write_bytes(b"before")
    manager = TransactionManager()
    prepared = manager.prepare(
        {
            SaveKind.SCORE_DATABASE: ArtifactPayload(
                SaveKind.SCORE_DATABASE, source, b"before", b"after"
            )
        },
        destination=None,
        overwrite_sources=True,
        create_backup=True,
    )
    manager.commit(prepared, revision=1)
    journal = json.loads(prepared.journal_path.read_text())
    journal["state"] = "replacing"
    prepared.journal_path.write_text(json.dumps(journal))
    Path(str(source) + suffix).write_bytes(b"external SQLite transaction")
    with pytest.raises(SourceChanged, match="SQLite journal"):
        manager.recover_for_sources({SaveKind.SCORE_DATABASE: source})
    assert source.read_bytes() == b"after"
    assert json.loads(prepared.journal_path.read_text())["state"] == "replacing"
