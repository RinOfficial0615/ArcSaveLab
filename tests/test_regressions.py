from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from arcsavelab.backups import list_backups, restore_backup
from arcsavelab.errors import CommitFailed, InvalidEdit, OpenFailed
from arcsavelab.formats.prefs_xml import SharedPrefsDocument
from arcsavelab.formats.st3_sqlite import ChartKey, St3Document
from arcsavelab.integrity import value_map_hash
from arcsavelab.interface import (
    BatchSet,
    BrowseQuery,
    CommitRequest,
    OpenRequest,
    SaveKind,
    Section,
    SetField,
    SourceSpec,
)
from arcsavelab.session import SaveSession
from arcsavelab.transaction import ArtifactPayload, TransactionManager

from .conftest import SyntheticSaveSet


def test_noop_keeps_valid_uppercase_digests(synthetic_saves: SyntheticSaveSet) -> None:
    path = synthetic_saves.preferences
    doc = SharedPrefsDocument.from_path(path)
    for key in ("fr_k", "ac_k", "st_k", "un_k", "ms_k"):
        doc.set(key, str(doc.get(key)).upper(), kind="string")
    path.write_bytes(doc.to_bytes())
    with SaveSession.open(synthetic_saves.request(*SaveKind)) as session:
        preview = session.preview()
        assert preview.can_commit
        assert not session.dirty
        assert not any(p.changed for p in preview.artifacts)


@pytest.mark.parametrize("pure,shiny", [(200, 150), (3, 2)])
def test_score_form_validates_combined_counts(
    synthetic_saves: SyntheticSaveSet, pure: int, shiny: int
) -> None:
    session = SaveSession.open(synthetic_saves.request(SaveKind.SCORE_DATABASE))
    try:
        session.perform(
            BatchSet(
                (
                    SetField("score:sayonarahatsukoi:2:0", "shiny_pure", shiny),
                    SetField("score:sayonarahatsukoi:2:0", "pure", pure),
                )
            )
        )
        assert session.score_database
        score = session.score_database.get_score(ChartKey("sayonarahatsukoi", 2, 0))
        assert score and score.perfect_count == pure and score.shiny_perfect_count == shiny
    finally:
        session.close(discard=True)


def test_invalid_single_edit_rolls_back_partial_state(
    synthetic_saves: SyntheticSaveSet, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = SaveSession.open(synthetic_saves.request(*SaveKind))
    original = session.preview()

    def fail_cache(*args: object) -> None:
        raise InvalidEdit("synthetic dependent-field failure")

    monkeypatch.setattr(session, "_sync_grade_cache", fail_cache)
    with pytest.raises(InvalidEdit):
        session.perform(SetField("score:sayonarahatsukoi:2:0", "far", 20))
    assert not session.dirty
    assert session.revision == 0
    assert session.preview().token == original.token
    session.close()


def test_same_label_unlock_changes_are_not_merged(synthetic_saves: SyntheticSaveSet) -> None:
    path = synthetic_saves.unlocks
    path.write_bytes(
        path.read_bytes().replace(
            b"</dict>", b"<key>lostcivilization|2|0</key><integer>0</integer></dict>"
        )
    )
    prefs = SharedPrefsDocument.from_path(synthetic_saves.preferences)
    prefs.set("un_k", value_map_hash(path), kind="string")
    synthetic_saves.preferences.write_bytes(prefs.to_bytes())
    session = SaveSession.open(
        synthetic_saves.request(SaveKind.PREFERENCES, SaveKind.UNLOCK_PROGRESS)
    )
    try:
        items = session.inspect(BrowseQuery(Section.UNLOCKS)).sections[0].items
        assert len(items) == 2
        for item in items:
            session.perform(SetField(item.id, "progress", 1))
        changes = session.preview().semantic_changes
        assert len(changes) == 2
        assert len({c.item_id for c in changes}) == 2
    finally:
        session.close(discard=True)


def test_zero_user_id_and_duplicate_source_paths(synthetic_saves: SyntheticSaveSet) -> None:
    with SaveSession.open(synthetic_saves.request(SaveKind.PREFERENCES, user_id=0)) as session:
        assert session.user_id == 0
    with pytest.raises(OpenFailed, match="same file"):
        SaveSession.open(
            OpenRequest(
                (
                    SourceSpec(SaveKind.PREFERENCES, synthetic_saves.preferences),
                    SourceSpec(SaveKind.UNLOCK_PROGRESS, synthetic_saves.preferences),
                )
            )
        )


def test_sqlite_path_special_characters_and_live_wal(synthetic_saves: SyntheticSaveSet) -> None:
    path = synthetic_saves.root / "score # snapshot"
    shutil.copyfile(synthetic_saves.scores, path)
    assert St3Document.from_path(path).schema_version == 4
    Path(str(path) + "-wal").write_bytes(b"active journal")
    with pytest.raises(ValueError, match="journal"):
        St3Document.from_path(path)


def test_sqlite_duplicate_keys_are_not_silently_collapsed(
    synthetic_saves: SyntheticSaveSet,
) -> None:
    with sqlite3.connect(synthetic_saves.scores) as connection:
        connection.execute("UPDATE scores SET ct=0")
    with pytest.raises(ValueError, match="duplicate"):
        St3Document.from_path(synthetic_saves.scores)


def test_inplace_backup_restore_and_restore_undo(synthetic_saves: SyntheticSaveSet) -> None:
    before = {kind: path.read_bytes() for kind, path in synthetic_saves.paths.items()}
    session = SaveSession.open(synthetic_saves.request(*SaveKind))
    session.perform(SetField("fragments", "amount", 654))
    receipt = session.commit(
        CommitRequest(overwrite_sources=True, preview_token=session.preview().token)
    )
    session.close()
    after = synthetic_saves.preferences.read_bytes()
    assert after != before[SaveKind.PREFERENCES]
    assert receipt.backup_directory
    assert list_backups(synthetic_saves.root)[0].directory == receipt.backup_directory
    restored = restore_backup(receipt.backup_directory)
    assert restored.verified and restored.backup_directory
    for kind, path in synthetic_saves.paths.items():
        assert path.read_bytes() == before[kind]
    undo = restore_backup(restored.backup_directory)
    assert undo.verified
    assert synthetic_saves.preferences.read_bytes() == after


def test_backup_corruption_stops_before_any_restore(synthetic_saves: SyntheticSaveSet) -> None:
    session = SaveSession.open(synthetic_saves.request(*SaveKind))
    session.perform(SetField("fragments", "amount", 800))
    receipt = session.commit(
        CommitRequest(overwrite_sources=True, preview_token=session.preview().token)
    )
    session.close()
    assert receipt.backup_directory
    (receipt.backup_directory / "st3").write_bytes(b"broken")
    before = {k: p.read_bytes() for k, p in synthetic_saves.paths.items()}
    with pytest.raises(CommitFailed, match="hash mismatch"):
        restore_backup(receipt.backup_directory)
    assert {k: p.read_bytes() for k, p in synthetic_saves.paths.items()} == before


def test_inplace_requires_backup_and_respects_lock(synthetic_saves: SyntheticSaveSet) -> None:
    path = synthetic_saves.preferences
    payloads = {
        SaveKind.PREFERENCES: ArtifactPayload(SaveKind.PREFERENCES, path, path.read_bytes(), b"new")
    }
    manager = TransactionManager()
    with pytest.raises(CommitFailed, match="require a backup"):
        manager.prepare(payloads, destination=None, overwrite_sources=True, create_backup=False)
    prepared = manager.prepare(
        payloads, destination=None, overwrite_sources=True, create_backup=True
    )
    lock = path.with_name(f".{path.name}.arcsavelab-lock")
    lock.touch()
    with pytest.raises(CommitFailed, match="locked"):
        manager.commit(prepared, revision=0)
    assert path.read_bytes() == payloads[SaveKind.PREFERENCES].original


def test_recovery_rejects_outside_paths_before_any_write(
    synthetic_saves: SyntheticSaveSet, tmp_path: Path
) -> None:
    session = SaveSession.open(synthetic_saves.request(SaveKind.PREFERENCES))
    receipt = session.commit(
        CommitRequest(overwrite_sources=True, preview_token=session.preview().token)
    )
    session.close()
    assert receipt.backup_directory
    journal = receipt.backup_directory / "transaction.json"
    data = json.loads(journal.read_text(encoding="utf-8"))
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"unrelated")
    item = dict(data["artifacts"][0])
    item.update(source=str(outside), destination=str(outside))
    data["artifacts"].append(item)
    data["state"] = "replacing"
    journal.write_text(json.dumps(data), encoding="utf-8")
    before = synthetic_saves.preferences.read_bytes()
    with pytest.raises(CommitFailed, match="invalid recovery"):
        TransactionManager.recover_for_sources({SaveKind.PREFERENCES: synthetic_saves.preferences})
    assert outside.read_bytes() == b"unrelated"
    assert synthetic_saves.preferences.read_bytes() == before


def test_export_does_not_alias_sources(synthetic_saves: SyntheticSaveSet) -> None:
    session = SaveSession.open(synthetic_saves.request(SaveKind.PREFERENCES))
    with pytest.raises(CommitFailed, match="new directory"):
        session.commit(
            CommitRequest(destination=synthetic_saves.root, preview_token=session.preview().token)
        )
    session.close()


def test_open_rejects_source_change_between_parse_and_baseline(
    synthetic_saves: SyntheticSaveSet, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = SaveSession._detect_version_profile

    def mutate_source(self: SaveSession) -> None:
        real(self)
        synthetic_saves.preferences.write_bytes(synthetic_saves.preferences.read_bytes() + b"\n")

    monkeypatch.setattr(SaveSession, "_detect_version_profile", mutate_source)
    with pytest.raises(OpenFailed, match="changed while opening"):
        SaveSession.open(synthetic_saves.request(SaveKind.PREFERENCES))
