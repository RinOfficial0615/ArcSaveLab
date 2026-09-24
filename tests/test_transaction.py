from __future__ import annotations

import json
from pathlib import Path

import pytest

import arcsavelab.transaction as transaction_module
from arcsavelab.errors import CommitFailed, SourceChanged
from arcsavelab.interface import SaveKind
from arcsavelab.transaction import ArtifactPayload, TransactionManager


def _payloads(directory: Path) -> dict[SaveKind, ArtifactPayload]:
    prefs = directory / "Cocos2dxPrefsFile.xml"
    unlocks = directory / "un"
    prefs.write_bytes(b"prefs-before")
    unlocks.write_bytes(b"un-before")
    return {
        SaveKind.PREFERENCES: ArtifactPayload(
            SaveKind.PREFERENCES, prefs, b"prefs-before", b"prefs-after"
        ),
        SaveKind.UNLOCK_PROGRESS: ArtifactPayload(
            SaveKind.UNLOCK_PROGRESS, unlocks, b"un-before", b"un-after"
        ),
    }


def test_output_commit_keeps_sources_and_verifies_destinations(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    payloads = _payloads(source)
    destination = tmp_path / "output"
    manager = TransactionManager()
    prepared = manager.prepare(
        payloads,
        destination=destination,
        overwrite_sources=False,
        create_backup=True,
    )
    receipt = manager.commit(prepared, revision=7)

    assert receipt.verified
    assert receipt.revision == 7
    assert receipt.backup_directory is None
    assert (source / "Cocos2dxPrefsFile.xml").read_bytes() == b"prefs-before"
    assert (source / "un").read_bytes() == b"un-before"
    assert (destination / "Cocos2dxPrefsFile.xml").read_bytes() == b"prefs-after"
    assert (destination / "un").read_bytes() == b"un-after"
    journal = json.loads((destination / "transaction.json").read_text(encoding="utf-8"))
    assert journal["state"] == "committed"
    assert all(item["replaced"] for item in journal["artifacts"])


def test_overwrite_commit_creates_verified_backup(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    payloads = _payloads(source)
    manager = TransactionManager()
    prepared = manager.prepare(
        payloads,
        destination=None,
        overwrite_sources=True,
        create_backup=True,
    )
    receipt = manager.commit(prepared, revision=1)

    assert (source / "Cocos2dxPrefsFile.xml").read_bytes() == b"prefs-after"
    assert (source / "un").read_bytes() == b"un-after"
    assert receipt.backup_directory is not None
    assert (receipt.backup_directory / "Cocos2dxPrefsFile.xml").read_bytes() == b"prefs-before"
    assert (receipt.backup_directory / "un").read_bytes() == b"un-before"
    journal = json.loads(prepared.journal_path.read_text(encoding="utf-8"))
    assert journal["state"] == "committed"


def test_overwrite_failure_rolls_back_every_replaced_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    payloads = _payloads(source)
    manager = TransactionManager()
    prepared = manager.prepare(
        payloads,
        destination=None,
        overwrite_sources=True,
        create_backup=True,
    )

    real_replace = transaction_module.os.replace
    prefs_path = source / "Cocos2dxPrefsFile.xml"
    injected = False

    def fail_preferences_once(src: str | Path, dst: str | Path) -> None:
        nonlocal injected
        source_name = Path(src).name
        if (
            not injected
            and Path(dst) == prefs_path
            and source_name.startswith(".Cocos2dxPrefsFile.xml.arcsavelab-")
        ):
            injected = True
            raise OSError("synthetic replacement failure")
        real_replace(src, dst)

    monkeypatch.setattr(transaction_module.os, "replace", fail_preferences_once)
    with pytest.raises(CommitFailed, match="synthetic replacement failure"):
        manager.commit(prepared, revision=2)

    assert injected
    assert prefs_path.read_bytes() == b"prefs-before"
    assert (source / "un").read_bytes() == b"un-before"
    journal = json.loads(prepared.journal_path.read_text(encoding="utf-8"))
    assert journal["state"] == "rolled_back"
    assert journal["rollback_errors"] == []


def test_output_failure_removes_newly_created_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    payloads = _payloads(source)
    destination = tmp_path / "output"
    manager = TransactionManager()
    prepared = manager.prepare(
        payloads,
        destination=destination,
        overwrite_sources=False,
        create_backup=False,
    )

    real_replace = transaction_module.os.replace
    prefs_destination = destination / "Cocos2dxPrefsFile.xml"
    injected = False

    def fail_preferences_once(src: str | Path, dst: str | Path) -> None:
        nonlocal injected
        if (
            not injected
            and Path(dst) == prefs_destination
            and Path(src).name.startswith(".Cocos2dxPrefsFile.xml.arcsavelab-")
        ):
            injected = True
            raise OSError("synthetic output failure")
        real_replace(src, dst)

    monkeypatch.setattr(transaction_module.os, "replace", fail_preferences_once)
    with pytest.raises(CommitFailed, match="synthetic output failure"):
        manager.commit(prepared, revision=3)

    assert injected
    assert not prefs_destination.exists()
    assert not (destination / "un").exists()
    assert (source / "Cocos2dxPrefsFile.xml").read_bytes() == b"prefs-before"
    assert (source / "un").read_bytes() == b"un-before"
    journal = json.loads(prepared.journal_path.read_text(encoding="utf-8"))
    assert journal["state"] == "rolled_back"


def test_export_rejects_preexisting_destination_without_touching_it(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    payloads = _payloads(source)
    destination = tmp_path / "output"
    destination.mkdir()
    (destination / "un").write_bytes(b"destination-un-before")
    (destination / "Cocos2dxPrefsFile.xml").write_bytes(b"destination-prefs-before")
    manager = TransactionManager()
    with pytest.raises(CommitFailed, match="new directory"):
        manager.prepare(
            payloads,
            destination=destination,
            overwrite_sources=False,
            create_backup=False,
        )

    assert (destination / "un").read_bytes() == b"destination-un-before"
    assert (destination / "Cocos2dxPrefsFile.xml").read_bytes() == b"destination-prefs-before"
    assert (source / "un").read_bytes() == b"un-before"


def test_external_source_change_stops_before_replacement(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    payloads = _payloads(source)
    manager = TransactionManager()
    prepared = manager.prepare(
        payloads,
        destination=tmp_path / "output",
        overwrite_sources=False,
        create_backup=False,
    )
    (source / "un").write_bytes(b"externally-changed")

    with pytest.raises(SourceChanged):
        manager.commit(prepared, revision=0)
    assert not (tmp_path / "output" / "Cocos2dxPrefsFile.xml").exists()


def test_recovery_restores_entire_multi_file_journal_when_subset_selected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    prefs = source / "Cocos2dxPrefsFile.xml"
    unlocks = source / "un"
    prefs.write_bytes(b"prefs-after")
    unlocks.write_bytes(b"un-after")
    root = source / ".arcsavelab-backups" / "tx"
    root.mkdir(parents=True)
    prefs_backup = root / prefs.name
    unlocks_backup = root / unlocks.name
    prefs_backup.write_bytes(b"prefs-before")
    unlocks_backup.write_bytes(b"un-before")
    journal = {
        "format": "arcsavelab.transaction/v1",
        "id": "tx",
        "state": "replacing",
        "artifacts": [
            {
                "kind": "preferences",
                "source": str(prefs.resolve()),
                "destination": str(prefs.resolve()),
                "backup": str(prefs_backup.resolve()),
                "before_sha256": transaction_module.sha256_bytes(b"prefs-before"),
                "after_sha256": transaction_module.sha256_bytes(b"prefs-after"),
                "replaced": True,
            },
            {
                "kind": "unlock_progress",
                "source": str(unlocks.resolve()),
                "destination": str(unlocks.resolve()),
                "backup": str(unlocks_backup.resolve()),
                "before_sha256": transaction_module.sha256_bytes(b"un-before"),
                "after_sha256": transaction_module.sha256_bytes(b"un-after"),
                "replaced": True,
            },
        ],
    }
    journal_path = root / "transaction.json"
    journal_path.write_text(json.dumps(journal), encoding="utf-8")

    recovered = TransactionManager.recover_for_sources({SaveKind.UNLOCK_PROGRESS: unlocks})

    assert recovered == (journal_path,)
    assert prefs.read_bytes() == b"prefs-before"
    assert unlocks.read_bytes() == b"un-before"
    assert json.loads(journal_path.read_text(encoding="utf-8"))["state"] == "recovered"
