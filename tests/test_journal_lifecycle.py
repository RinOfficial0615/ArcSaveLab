from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from arcsavelab.backups import list_backups, restore_backup
from arcsavelab.errors import CommitFailed
from arcsavelab.interface import SaveKind
from arcsavelab.transaction import TransactionManager

from .test_transaction import _payloads


def interrupted(root: Path) -> Path:
    manager = TransactionManager()
    prepared = manager.prepare(
        _payloads(root), destination=None, overwrite_sources=True, create_backup=True
    )
    manager.commit(prepared, revision=0)
    data = json.loads(prepared.journal_path.read_text())
    data["state"] = "replacing"
    prepared.journal_path.write_text(json.dumps(data))
    return prepared.journal_path


def test_subset_recovery_respects_unselected_member_lock(tmp_path: Path) -> None:
    journal = interrupted(tmp_path)
    prefs, un = tmp_path / "Cocos2dxPrefsFile.xml", tmp_path / "un"
    lock = prefs.with_name(f".{prefs.name}.arcsavelab-lock")
    lock.touch()
    before = {p: p.read_bytes() for p in (prefs, un, journal)}
    with pytest.raises(CommitFailed, match="locked"):
        TransactionManager.recover_for_sources({SaveKind.UNLOCK_PROGRESS: un})
    assert before == {p: p.read_bytes() for p in before}
    assert lock.exists()
    assert not un.with_name(".un.arcsavelab-lock").exists()


def test_recovery_holds_complete_lock_set_until_journal_is_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = interrupted(tmp_path)
    sources = {SaveKind.UNLOCK_PROGRESS: tmp_path / "un"}
    real = TransactionManager._write_replace
    writes = []

    def checked(path: Path, value: bytes) -> None:
        assert (tmp_path / ".Cocos2dxPrefsFile.xml.arcsavelab-lock").exists()
        assert (tmp_path / ".un.arcsavelab-lock").exists()
        with pytest.raises(CommitFailed, match="locked"):
            TransactionManager.recover_for_sources(sources)
        writes.append(path)
        real(path, value)

    monkeypatch.setattr(TransactionManager, "_write_replace", staticmethod(checked))
    assert TransactionManager.recover_for_sources(sources) == (journal,)
    assert len(writes) == 3
    assert not list(tmp_path.glob(".*.arcsavelab-lock"))
    assert json.loads(journal.read_text())["state"] == "recovered"


@pytest.mark.parametrize("mutation", ["duplicate", "non_object", "empty", "bad_hash", "bad_path"])
def test_journal_contract_shared_by_listing_manual_and_automatic_restore(
    tmp_path: Path, mutation: str
) -> None:
    journal = interrupted(tmp_path)
    data: dict[str, Any] = json.loads(journal.read_text())
    artifacts = data["artifacts"]
    if mutation == "duplicate":
        artifacts.append(dict(artifacts[0]))
    elif mutation == "non_object":
        artifacts.append(None)
    elif mutation == "empty":
        data["artifacts"] = []
    elif mutation == "bad_hash":
        artifacts[0]["before_sha256"] = "not-a-hash"
    else:
        artifacts[0]["destination"] = str(tmp_path.parent / "outside")
    data["state"] = "committed"
    journal.write_text(json.dumps(data))
    before = {p: p.read_bytes() for p in (tmp_path / "un", tmp_path / "Cocos2dxPrefsFile.xml")}
    assert list_backups(tmp_path) == ()
    with pytest.raises(CommitFailed):
        restore_backup(journal.parent)
    data["state"] = "replacing"
    journal.write_text(json.dumps(data))
    with pytest.raises(CommitFailed):
        TransactionManager.recover_for_sources({SaveKind.UNLOCK_PROGRESS: tmp_path / "un"})
    assert before == {p: p.read_bytes() for p in before}
    assert not list(tmp_path.glob(".*.arcsavelab-lock"))
