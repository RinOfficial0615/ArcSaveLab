from __future__ import annotations

from pathlib import Path

import pytest

from arcsavelab.errors import CapabilityUnavailable, CommitBlocked, InvalidEdit
from arcsavelab.formats.plist_map import PlistMapDocument
from arcsavelab.formats.prefs_xml import SharedPrefsDocument
from arcsavelab.formats.st3_sqlite import ChartKey, St3Document, grade_for_score
from arcsavelab.integrity import (
    device_bound_value_hash,
    direct_value_hash,
    validity_hash_for_int,
    value_map_hash,
)
from arcsavelab.interface import (
    Availability,
    BrowseQuery,
    CommitRequest,
    Redo,
    RepairIntegrity,
    SaveKind,
    Section,
    SetField,
    Undo,
)
from arcsavelab.session import SaveSession
from arcsavelab.verification import verify_userdefaults

ALL_KINDS = tuple(SaveKind)


@pytest.mark.parametrize("mask", range(1, 16))
def test_all_fifteen_nonempty_file_combinations_expose_independent_capabilities(
    synthetic_saves: object, mask: int
) -> None:
    kinds = tuple(kind for index, kind in enumerate(ALL_KINDS) if mask & (1 << index))
    session = SaveSession.open(synthetic_saves.request(*kinds))  # type: ignore[attr-defined]
    try:
        sections = {view.section: view for view in session.inspect().sections}
        has_prefs = SaveKind.PREFERENCES in kinds
        has_unlocks = SaveKind.UNLOCK_PROGRESS in kinds
        has_missions = SaveKind.MISSION_PROGRESS in kinds
        has_scores = SaveKind.SCORE_DATABASE in kinds

        for section in (
            Section.SETTINGS,
            Section.FRAGMENTS,
            Section.OWNERSHIP,
            Section.PARTNERS,
            Section.FAVORITES,
            Section.STORY,
            Section.CHARACTER_SKILLS,
        ):
            expected = Availability.ENABLED if has_prefs else Availability.NEEDS_SOURCE
            assert sections[section].availability is expected
        assert sections[Section.FINALE].availability is (
            Availability.READ_ONLY if has_prefs else Availability.NEEDS_SOURCE
        )
        assert sections[Section.ACCOUNT_DIAGNOSTICS].availability is (
            Availability.READ_ONLY if has_prefs else Availability.NEEDS_SOURCE
        )
        assert sections[Section.UNLOCKS].availability is (
            Availability.ENABLED
            if has_unlocks and has_prefs
            else Availability.READ_ONLY
            if has_unlocks
            else Availability.NEEDS_SOURCE
        )
        assert sections[Section.MISSIONS].availability is (
            Availability.ENABLED
            if has_missions and has_prefs
            else Availability.READ_ONLY
            if has_missions
            else Availability.NEEDS_SOURCE
        )
        assert sections[Section.SCORES].availability is (
            Availability.ENABLED if has_scores else Availability.NEEDS_SOURCE
        )
    finally:
        session.close(discard=True)


def test_session_edit_undo_redo_tracks_semantic_revision(synthetic_saves: object) -> None:
    session = SaveSession.open(
        synthetic_saves.request(SaveKind.PREFERENCES)  # type: ignore[attr-defined]
    )
    try:
        report = session.perform(SetField("fragments", "amount", 321, expected_revision=0))
        assert report.revision == 1
        assert session.preferences is not None
        assert session.preferences.get("fr_v") == 321
        assert session.inspect().can_undo

        undone = session.perform(Undo(expected_revision=1))
        assert undone.revision == 2
        assert session.preferences.get("fr_v") == 100
        assert session.inspect().can_redo

        redone = session.perform(Redo(expected_revision=2))
        assert redone.revision == 3
        assert session.preferences.get("fr_v") == 321
        assert session.inspect().dirty
    finally:
        session.close(discard=True)


def test_full_session_edit_derives_hashes_and_commits_to_output(
    synthetic_saves: object, tmp_path: Path
) -> None:
    session = SaveSession.open(synthetic_saves.request(*ALL_KINDS))  # type: ignore[attr-defined]
    original_preferences = synthetic_saves.preferences.read_bytes()  # type: ignore[attr-defined]

    session.perform(SetField("fragments", "amount", 777))
    session.perform(SetField("pack:alice", "owned", True))
    session.perform(SetField("favorite-song:sayonarahatsukoi", "favorite", True))

    unlocks = session.inspect(BrowseQuery(section=Section.UNLOCKS, limit=20)).sections[0]
    assert unlocks.items
    session.perform(SetField(unlocks.items[0].id, "progress", 1))
    session.perform(SetField("mission:mission_1_1_tutorial", "claimed", True))
    session.perform(SetField("score:sayonarahatsukoi:2:0", "far", 30))

    preview = session.preview()
    assert preview.can_commit
    assert {plan.kind for plan in preview.artifacts} == set(ALL_KINDS)
    assert all(plan.changed for plan in preview.artifacts)

    destination = tmp_path / "output"
    receipt = session.commit(CommitRequest(destination=destination, preview_token=preview.token))
    assert receipt.verified
    assert synthetic_saves.preferences.read_bytes() == original_preferences  # type: ignore[attr-defined]

    prefs = SharedPrefsDocument.from_path(destination / "Cocos2dxPrefsFile.xml")
    un = PlistMapDocument.from_path(destination / "un")
    ms = PlistMapDocument.from_path(destination / "ms")
    st3 = St3Document.from_path(destination / "st3")

    assert prefs.get("fr_v") == 777
    assert prefs.get("fr_k") == validity_hash_for_int(777)
    assert prefs.get("p_v") == "alice"
    assert prefs.get("p_k") == device_bound_value_hash("alice", "synthetic-device", 123)
    assert prefs.get("fs_v") == "sayonarahatsukoi"
    assert prefs.get("fs_k") == direct_value_hash("sayonarahatsukoi")
    assert un.get("sayonarahatsukoi|2|0") == 1
    assert ms.get("mission_1_1_tutorial") == "claimed"
    assert prefs.get("un_k") == value_map_hash(un)
    assert prefs.get("ms_k") == value_map_hash(ms)

    score = st3.get_score(ChartKey("sayonarahatsukoi", 2, 0))
    assert score is not None
    assert score.near_count == 30
    assert prefs.get("cs_v") == f"sayonarahatsukoi|2|{grade_for_score(score.score)}"
    assert prefs.get("cs_k") == direct_value_hash(str(prefs.get("cs_v")))

    verification = verify_userdefaults(
        destination / "Cocos2dxPrefsFile.xml",
        un=destination / "un",
        ms=destination / "ms",
        device_id="synthetic-device",
        user_id=123,
    )
    assert verification.strict_ok
    assert not session.dirty
    session.close()


def test_public_boolean_and_catalog_ids_are_strict(synthetic_saves: object) -> None:
    session = SaveSession.open(
        synthetic_saves.request(SaveKind.PREFERENCES)  # type: ignore[attr-defined]
    )
    try:
        known_pack = session.catalog.packs[0].id
        with pytest.raises(InvalidEdit, match="boolean"):
            session.perform(SetField(f"pack:{known_pack}", "owned", "false"))
        with pytest.raises(InvalidEdit, match="unknown song pack"):
            session.perform(SetField("pack:future_unknown", "owned", True))
        with pytest.raises(InvalidEdit, match="unknown song"):
            session.perform(SetField("single:future_unknown", "owned", True))
    finally:
        session.close(discard=True)


def test_expert_override_keeps_machine_bounds(synthetic_saves: object) -> None:
    request = synthetic_saves.request(SaveKind.PREFERENCES, expert=True)  # type: ignore[attr-defined]
    session = SaveSession.open(request)
    try:
        with pytest.raises(InvalidEdit, match="2147483647"):
            session.perform(SetField("fragments", "amount", 2**40))
    finally:
        session.close(discard=True)


def test_profile_mismatch_is_read_only_until_force_profile(synthetic_saves: object) -> None:
    prefs = synthetic_saves.preferences  # type: ignore[attr-defined]
    unlocks = synthetic_saves.unlocks  # type: ignore[attr-defined]
    unlocks.write_bytes(unlocks.read_bytes().replace(b"|2|0</key>", b"|2|999</key>"))
    prefs_doc = SharedPrefsDocument.from_path(prefs)
    prefs_doc.set("un_k", value_map_hash(PlistMapDocument.from_path(unlocks)), kind="string")
    prefs.write_bytes(prefs_doc.to_bytes())
    session = SaveSession.open(
        synthetic_saves.request(SaveKind.PREFERENCES, SaveKind.UNLOCK_PROGRESS)  # type: ignore[attr-defined]
    )
    try:
        unlock_view = session.inspect(BrowseQuery(section=Section.UNLOCKS)).sections[0]
        assert unlock_view.availability is Availability.READ_ONLY
        with pytest.raises(CapabilityUnavailable) as exc_info:
            session.perform(RepairIntegrity())
        assert exc_info.value.details["reason"] == "version profile mismatch"
        preview = session.preview()
        assert not preview.can_commit
        assert any(item.code == "version.profile_mismatch" for item in preview.diagnostics)
    finally:
        session.close(discard=True)


def test_force_profile_explicitly_reenables_matching_sections(synthetic_saves: object) -> None:
    unlocks = synthetic_saves.unlocks  # type: ignore[attr-defined]
    unlocks.write_bytes(unlocks.read_bytes().replace(b"|2|0</key>", b"|2|999</key>"))
    request = synthetic_saves.request(  # type: ignore[attr-defined]
        SaveKind.PREFERENCES,
        SaveKind.UNLOCK_PROGRESS,
    )
    request = request.__class__(
        sources=request.sources,
        device_id=request.device_id,
        user_id=request.user_id,
        allow_version_override=True,
    )
    session = SaveSession.open(request)
    try:
        unlock_view = session.inspect(BrowseQuery(section=Section.UNLOCKS)).sections[0]
        assert unlock_view.availability is Availability.ENABLED
    finally:
        session.close(discard=True)


def test_repair_recomputes_device_bound_hashes_and_commit_requires_preview(
    synthetic_saves: object, tmp_path: Path
) -> None:
    prefs = synthetic_saves.preferences  # type: ignore[attr-defined]
    prefs_doc = SharedPrefsDocument.from_path(prefs)
    prefs_doc.set("ac_k", "corrupt", kind="string")
    prefs.write_bytes(prefs_doc.to_bytes())
    session = SaveSession.open(
        synthetic_saves.request(SaveKind.PREFERENCES)  # type: ignore[attr-defined]
    )
    try:
        session.perform(RepairIntegrity())
        preview = session.preview()
        assert preview.can_commit
        with pytest.raises(CommitBlocked, match="preview token"):
            session.commit(CommitRequest(destination=tmp_path / "out"))
        receipt = session.commit(
            CommitRequest(destination=tmp_path / "out", preview_token=preview.token)
        )
        assert receipt.verified
        repaired = SharedPrefsDocument.from_path(tmp_path / "out" / "Cocos2dxPrefsFile.xml")
        assert repaired.get("ac_k") == device_bound_value_hash("0", "synthetic-device", 123)
    finally:
        session.close(discard=True)


def test_account_identity_is_masked(synthetic_saves: object) -> None:
    session = SaveSession.open(
        synthetic_saves.request(SaveKind.PREFERENCES)  # type: ignore[attr-defined]
    )
    try:
        account = session.inspect(BrowseQuery(section=Section.ACCOUNT_DIAGNOSTICS)).sections[0]
        player = next(item for item in account.items if item.id == "account-player")
        rendered = str(player.fields[0].value)
        assert "123" not in rendered
        assert player.fields[0].sensitive
    finally:
        session.close(discard=True)


def test_score_rows_label_inscribed_charts(tmp_path: Path) -> None:
    """7.0 Inscribed charts occupy the BYD slot and are labeled INS, not BYD."""
    import sqlite3

    from arcsavelab.formats.st3_sqlite import calculate_score
    from arcsavelab.interface import OpenRequest, SourceSpec

    st3 = tmp_path / "st3"
    connection = sqlite3.connect(st3)
    try:
        connection.executescript(
            """
            CREATE TABLE scores(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version TEXT NOT NULL,
                score INTEGER NOT NULL,
                shinyPerfectCount INTEGER NOT NULL,
                perfectCount INTEGER NOT NULL,
                nearCount INTEGER NOT NULL,
                missCount INTEGER NOT NULL,
                date INTEGER NOT NULL,
                songId TEXT NOT NULL,
                songDifficulty INTEGER NOT NULL,
                modifier INTEGER NOT NULL,
                health INTEGER NOT NULL,
                ct INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE cleartypes(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                songId TEXT NOT NULL,
                songDifficulty INTEGER NOT NULL,
                clearType INTEGER NOT NULL,
                ct INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE schemaversion(appliedVersion INTEGER NOT NULL);
            INSERT INTO schemaversion(appliedVersion) VALUES(4);
            """
        )
        for song_id, difficulty in (("deinosphainein", 3), ("sayonarahatsukoi", 3)):
            connection.execute(
                "INSERT INTO scores(version,score,shinyPerfectCount,perfectCount,"
                "nearCount,missCount,date,songId,songDifficulty,modifier,health,ct) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "7.0.260c",
                    calculate_score(90, 8, 2, 5),
                    5,
                    90,
                    8,
                    2,
                    1_700_000_000_000,
                    song_id,
                    difficulty,
                    0,
                    100,
                    0,
                ),
            )
        connection.commit()
    finally:
        connection.close()

    session = SaveSession.open(OpenRequest(sources=(SourceSpec(SaveKind.SCORE_DATABASE, st3),)))
    try:
        scores = session.inspect(BrowseQuery(section=Section.SCORES)).sections[0]
        labels = [item.label for item in scores.items]
        assert any(" · INS · " in label for label in labels)
        assert any(" · BYD · " in label for label in labels)
    finally:
        session.close(discard=True)
