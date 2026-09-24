from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from textual.widgets import Button, Static

from arcsavelab.errors import CommitFailed
from arcsavelab.formats.prefs_xml import SharedPrefsDocument
from arcsavelab.interface import CommitRequest, OpenRequest, SaveKind, SetField
from arcsavelab.session import SaveSession
from arcsavelab.ui.app import ArcSaveLabApp
from arcsavelab.ui.dialogs import ConfirmScreen
from arcsavelab.workspace import WorkspaceLifecycle

from .conftest import DEVICE_ID, SyntheticSaveSet


def make_backup(saves: SyntheticSaveSet) -> Path:
    with SaveSession.open(saves.request(*SaveKind)) as session:
        session.perform(SetField("fragments", "amount", 200))
        receipt = session.commit(
            CommitRequest(overwrite_sources=True, preview_token=session.preview().token)
        )
        assert receipt.backup_directory
        return receipt.backup_directory


async def test_restore_reload_failure_suspends_stale_session_and_preserves_draft(
    synthetic_saves: SyntheticSaveSet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup = make_backup(synthetic_saves)
    app = ArcSaveLabApp(synthetic_saves.request(*SaveKind))
    events: list[tuple[str, Any]] = []
    async with app.run_test(size=(100, 32)) as pilot:
        assert app.session
        previous = app.session
        previous.perform(SetField("fragments", "amount", 333))
        with monkeypatch.context() as failing:

            def fail_open(cls: type[SaveSession], request: OpenRequest) -> SaveSession:
                raise OSError("injected reopen failure")

            failing.setattr(SaveSession, "open", classmethod(fail_open))
            failing.setattr(app, "notify", lambda message, **kw: events.append((str(message), kw)))
            app._restore_backup(backup)
            await pilot.pause()
        assert not any("restored and verified" in message for message, _ in events)
        assert app.session is None
        assert app.lifecycle.suspended_session is previous
        assert previous.preferences and previous.preferences.get("fr_v") == 333
        with pytest.raises(CommitFailed, match="reload"):
            previous.perform(SetField("fragments", "amount", 555))
        assert app.lifecycle.has_uncommitted_changes
        assert app.query_one("#review", Button).disabled
        assert "reload" in str(app.query_one("#status", Static).render()).lower()
        assert SharedPrefsDocument.from_path(synthetic_saves.preferences).get("fr_v") == 100
        await app.action_quit()
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("escape")
        await pilot.pause()
        app.open_workspace(app.request)
        assert app.session and app.session.preferences
        assert app.session.preferences.get("fr_v") == 100
        assert app.lifecycle.suspended_session is None


async def test_restore_uses_current_session_identity(synthetic_saves: SyntheticSaveSet) -> None:
    backup = make_backup(synthetic_saves)
    app = ArcSaveLabApp(synthetic_saves.request(*SaveKind, device_id=None))
    async with app.run_test(size=(100, 32)):
        app._apply_identity((DEVICE_ID, 123))
        app.refresh_workspace()
        app._restore_backup(backup)
        assert app.session and app.session.device_id == DEVICE_ID
        assert app.request.device_id == DEVICE_ID
        assert app.last_receipt and app.last_receipt.verified


def test_commit_reload_failure_preserves_receipt_and_blocks_stale_session(
    synthetic_saves: SyntheticSaveSet,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SaveSession.open(synthetic_saves.request(*SaveKind))
    session.perform(SetField("fragments", "amount", 444))
    before_sources = dict(session.sources)
    before_prefs = session.preferences
    request = CommitRequest(destination=tmp_path / "export", preview_token=session.preview().token)
    try:
        with monkeypatch.context() as failing:

            def fail_parse(cls: type[SharedPrefsDocument], path: Path) -> SharedPrefsDocument:
                raise OSError("injected reload parse failure")

            failing.setattr(SharedPrefsDocument, "from_path", classmethod(fail_parse))
            with pytest.raises(Exception) as caught:
                session.commit(request)
        assert getattr(caught.value, "receipt", None) is not None
        assert session.sources == before_sources
        assert session.preferences is before_prefs
        assert before_prefs and before_prefs.get("fr_v") == 444
        with pytest.raises(CommitFailed, match="reload"):
            session.preview()
        assert (
            SharedPrefsDocument.from_path(tmp_path / "export" / "Cocos2dxPrefsFile.xml").get("fr_v")
            == 444
        )
    finally:
        session.close(discard=True)


def test_lifecycle_commit_failure_can_reopen_the_verified_export(
    synthetic_saves: SyntheticSaveSet,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = WorkspaceLifecycle(synthetic_saves.request(*SaveKind))
    workspace.open(workspace.request)
    workspace.perform(SetField("fragments", "amount", 321))
    assert workspace.session
    old = workspace.session
    try:
        request = CommitRequest(
            destination=tmp_path / "verified-output", preview_token=old.preview().token
        )
        with monkeypatch.context() as failing:

            def fail_parse(cls: type[SharedPrefsDocument], path: Path) -> SharedPrefsDocument:
                raise OSError("injected parse failure")

            failing.setattr(SharedPrefsDocument, "from_path", classmethod(fail_parse))
            with pytest.raises(CommitFailed, match="reload"):
                workspace.commit(request)
        assert workspace.session is None and workspace.suspended_session is old
        assert workspace.last_receipt and workspace.last_receipt.verified
        assert all(s.path.parent == tmp_path / "verified-output" for s in workspace.request.sources)
        workspace.open(workspace.request, discard=True)
        assert workspace.session and workspace.session.preferences
        assert workspace.session.preferences.get("fr_v") == 321
        assert not workspace.has_uncommitted_changes
    finally:
        workspace.close(discard=True)


def test_lifecycle_refuses_to_discard_without_confirmation(
    synthetic_saves: SyntheticSaveSet,
) -> None:
    from arcsavelab.errors import UncommittedChanges

    backup = make_backup(synthetic_saves)
    workspace = WorkspaceLifecycle(synthetic_saves.request(*SaveKind))
    workspace.open(workspace.request)
    workspace.perform(SetField("fragments", "amount", 999))
    before = synthetic_saves.preferences.read_bytes()
    try:
        with pytest.raises(UncommittedChanges):
            workspace.restore(backup)
        with pytest.raises(UncommittedChanges):
            workspace.open(workspace.request)
        assert synthetic_saves.preferences.read_bytes() == before
        assert workspace.session and workspace.session.preferences
        assert workspace.session.preferences.get("fr_v") == 999
    finally:
        workspace.close(discard=True)
