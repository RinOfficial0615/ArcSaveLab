from __future__ import annotations

import pytest

from arcsavelab.formats.prefs_xml import SharedPrefsDocument
from arcsavelab.interface import CommitRequest, RepairIntegrity, SaveKind, Undo
from arcsavelab.session import SaveSession
from arcsavelab.verification import VerificationStatus, verify_userdefaults

from .conftest import SyntheticSaveSet


@pytest.mark.parametrize("digest", ["fin_k", "fr_k", "ac_k", "un_k", "ms_k"])
def test_missing_digest_blocks_session_until_explicit_repair(
    synthetic_saves: SyntheticSaveSet, digest: str
) -> None:
    path = synthetic_saves.preferences
    doc = SharedPrefsDocument.from_path(path)
    doc.set("fin_v", "synthetic-final", kind="string")
    from arcsavelab.integrity import direct_value_hash

    doc.set("fin_k", direct_value_hash("synthetic-final"), kind="string")
    # Lossless adapter has no deletion command: remove only the generated digest element.
    import re

    raw = re.sub(rb'<string name="' + digest.encode() + rb'">[^<]*</string>', b"", doc.to_bytes())
    assert raw != doc.to_bytes()
    path.write_bytes(raw)
    report = verify_userdefaults(path, device_id="synthetic-device", user_id=123)
    result = report.for_key(digest)
    assert result and result.status is VerificationStatus.MISSING
    session = SaveSession.open(synthetic_saves.request(*SaveKind))
    try:
        assert not session.preview().can_commit
        assert any(d.code == "integrity.digest_missing" for d in session.inspect().diagnostics)
        session.perform(RepairIntegrity())
        assert session.preview().can_commit
        session.perform(Undo())
        assert not session.preview().can_commit
        assert path.read_bytes() == raw
        session.perform(RepairIntegrity())
        receipt = session.commit(CommitRequest(preview_token=session.preview().token))
        assert receipt.verified
        assert session.preferences
        repaired = verify_userdefaults(
            session.preferences,
            un=session.unlocks,
            ms=session.missions,
            device_id="synthetic-device",
            user_id=123,
        )
        assert repaired.for_key(digest).status is VerificationStatus.MATCH
    finally:
        session.close(discard=True)


def test_repair_empty_device_values_needs_no_identity(synthetic_saves: SyntheticSaveSet) -> None:
    path = synthetic_saves.preferences
    doc = SharedPrefsDocument.from_path(path)
    for prefix in ("p", "s", "wu", "ac"):
        doc.set(prefix + "_v", "", kind="string")
        doc.set(prefix + "_k", "", kind="string")
    path.write_bytes(doc.to_bytes())
    session = SaveSession.open(synthetic_saves.request(*SaveKind, device_id=None, user_id=None))
    try:
        assert session.preview().can_commit
        session.perform(RepairIntegrity())
        assert session.preview().can_commit
        assert not any(a.changed for a in session.preview().artifacts)
    finally:
        session.close(discard=True)


def test_repair_nonempty_device_value_still_requires_identity(
    synthetic_saves: SyntheticSaveSet,
) -> None:
    session = SaveSession.open(synthetic_saves.request(*SaveKind, device_id=None, user_id=None))
    try:
        assert session.preview().can_commit
        session.perform(RepairIntegrity())
        assert not session.preview().can_commit
        assert any(d.code == "capability.identity_required" for d in session.preview().diagnostics)
    finally:
        session.close(discard=True)


def test_preferences_only_does_not_read_unselected_maps(synthetic_saves: SyntheticSaveSet) -> None:
    synthetic_saves.unlocks.write_bytes(b"not a plist")
    with SaveSession.open(synthetic_saves.request(SaveKind.PREFERENCES)) as session:
        assert session.preview().can_commit
