from pathlib import Path

import pytest
from textual.pilot import Pilot
from textual.widgets import DataTable, Input, OptionList, Select, Static, Switch

from arcsavelab.interface import OpenRequest, SaveKind
from arcsavelab.ui.app import ArcSaveLabApp
from arcsavelab.ui.backups import BackupScreen
from arcsavelab.ui.dialogs import ConfirmScreen, OpenScreen, ReviewScreen
from arcsavelab.ui.editor import EditScreen
from arcsavelab.ui.files import FilePicker

from .conftest import SyntheticSaveSet


async def navigate(app: ArcSaveLabApp, pilot: Pilot[int], option: int) -> None:
    nav = app.query_one("#navigation", OptionList)
    nav.focus()
    nav.highlighted = option
    await pilot.press("enter")
    await pilot.pause()
    await pilot.pause()


@pytest.mark.parametrize("size", [(80, 24), (120, 36), (160, 48)])
async def test_welcome_open_cancel_resize(size: tuple[int, int]) -> None:
    app = ArcSaveLabApp()
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        assert app.query_one("#brand").region.width > 0
        await pilot.click("#open")
        await pilot.pause()
        assert isinstance(app.screen, OpenScreen)
        assert app.screen.query_one("#open-files").region.bottom <= size[1]
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, OpenScreen)
        assert app.session is None


async def test_full_open_edit_validate_history_review_export(
    synthetic_saves: SyntheticSaveSet, tmp_path: Path
) -> None:
    original = synthetic_saves.preferences.read_bytes()
    app = ArcSaveLabApp(OpenRequest((), locale="en"))
    async with app.run_test(size=(120, 38)) as pilot:
        await pilot.click("#open")
        await pilot.pause()
        app.screen.query_one("#folder", Input).value = str(synthetic_saves.root)
        await pilot.click("#detect")
        await pilot.pause()
        assert app.screen.query_one("#source-score_database", Input).value.endswith("st3")
        await pilot.click("#open-files")
        await pilot.pause()
        await pilot.pause()
        assert app.session is not None
        assert len(app.session.sources) == 4
        await navigate(app, pilot, 1)
        table = app.query_one("#items", DataTable)
        table.focus()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, EditScreen)
        app.screen.query_one("#edit-0", Input).value = "99999"
        await pilot.click("#apply")
        await pilot.pause()
        assert isinstance(app.screen, EditScreen)
        assert str(app.screen.query_one("#form-error", Static).render())
        app.screen.query_one("#edit-0", Input).value = "42"
        await pilot.press("ctrl+enter")
        await pilot.pause()
        await pilot.pause()
        assert not isinstance(app.screen, EditScreen), (
            str(app.screen.query_one("#form-error", Static).render())
            if isinstance(app.screen, EditScreen)
            else ""
        )
        assert app.session.preferences is not None
        assert app.session.preferences.get("highspeed_int") == 42
        assert synthetic_saves.preferences.read_bytes() == original
        await pilot.click("#undo")
        await pilot.pause()
        assert app.session.preferences.get("highspeed_int") == 30
        await pilot.click("#redo")
        await pilot.pause()
        assert app.session.preferences.get("highspeed_int") == 42
        await pilot.click("#review")
        await pilot.pause()
        assert isinstance(app.screen, ReviewScreen)
        output = tmp_path / "exported"
        app.screen.query_one("#output-path", Input).value = str(output)
        await pilot.click("#save")
        await pilot.pause()
        await pilot.pause()
        assert app.last_receipt is not None and app.last_receipt.verified
        assert not app.session.dirty
        assert (output / "st3").is_file()
        assert synthetic_saves.preferences.read_bytes() == original
        assert app.session.sources[SaveKind.PREFERENCES] == output / "Cocos2dxPrefsFile.xml"


async def test_navigation_search_selection_and_batch(synthetic_saves: SyntheticSaveSet) -> None:
    app = ArcSaveLabApp(synthetic_saves.request(SaveKind.PREFERENCES))
    async with app.run_test(size=(120, 36)) as pilot:
        await navigate(app, pilot, 5)  # Favorites, > 100 rows.
        assert len(app.items) > 500
        app.query_one("#search", Input).value = "sayonara"
        await pilot.pause()
        assert "favorite-song:sayonarahatsukoi" in app.items
        app.query_one("#items").focus()
        await pilot.press("space")
        await pilot.pause()
        assert app.marked
        await pilot.click("#batch")
        await pilot.pause()
        assert isinstance(app.screen, EditScreen)
        assert not app.screen.query("#use-0")
        await pilot.press("escape")
        await pilot.pause()
        app.query_one("#search", Input).value = "no-such-song"
        await pilot.pause()
        assert not app.marked and app.selected_id is None
        assert app.query_one("#empty").display
        await navigate(app, pilot, 1)
        assert app.items
        await pilot.click("#language")
        await pilot.pause()
        assert app.tr.locale == "zh-Hans"
        assert "游玩设置" in str(app.query_one("#page-title", Static).render())


async def test_dirty_quit_and_open_cancel_preserve_session(
    synthetic_saves: SyntheticSaveSet,
) -> None:
    app = ArcSaveLabApp(synthetic_saves.request(SaveKind.PREFERENCES))
    async with app.run_test(size=(100, 30)) as pilot:
        await navigate(app, pilot, 2)
        app.query_one("#items").focus()
        await pilot.press("enter")
        await pilot.pause()
        app.screen.query_one("#edit-0", Input).value = "888"
        await pilot.click("#apply")
        await pilot.pause()
        session = app.session
        assert session is not None and session.dirty
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("escape")
        await pilot.pause()
        await pilot.click("#open")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click("#confirm")
        await pilot.pause()
        assert isinstance(app.screen, OpenScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert app.session is session and session.dirty


async def test_read_only_source_and_failed_open_keep_workspace(
    synthetic_saves: SyntheticSaveSet,
) -> None:
    app = ArcSaveLabApp(synthetic_saves.request(SaveKind.UNLOCK_PROGRESS))
    async with app.run_test(size=(100, 32)) as pilot:
        await navigate(app, pilot, 9)
        assert app.query_one("#edit").disabled
        session = app.session
        bad = synthetic_saves.root / "bad.xml"
        bad.write_text("not XML", encoding="utf-8")
        await pilot.click("#open")
        await pilot.pause()
        app.screen.query_one("#source-preferences", Input).value = str(bad)
        await pilot.click("#open-files")
        await pilot.pause()
        await pilot.pause()
        assert app.session is session


async def test_nested_file_browser_selects_folder(synthetic_saves: SyntheticSaveSet) -> None:
    app = ArcSaveLabApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.click("#open")
        await pilot.pause()
        app.screen.query_one("#folder", Input).value = str(synthetic_saves.root)
        await pilot.click("#browse")
        await pilot.pause()
        assert isinstance(app.screen, FilePicker)
        await pilot.click("#use-folder")
        await pilot.pause()
        assert isinstance(app.screen, OpenScreen)
        assert app.screen.query_one("#source-preferences", Input).value.endswith(
            "Cocos2dxPrefsFile.xml"
        )
        await pilot.click("#open-files")
        await pilot.pause()
        assert app.session and len(app.session.sources) == 4


async def test_batch_edit_common_and_mixed_values(synthetic_saves: SyntheticSaveSet) -> None:
    app = ArcSaveLabApp(synthetic_saves.request(SaveKind.PREFERENCES))
    async with app.run_test(size=(100, 30)) as pilot:
        await navigate(app, pilot, 5)
        app.query_one("#items").focus()
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("down", "space")
        await pilot.pause()
        assert len(app.marked) == 2
        await pilot.click("#batch")
        await pilot.pause()
        assert isinstance(app.screen, EditScreen)
        assert not app.screen.query("#use-0")
        app.screen.query_one("#edit-0", Switch).value = True
        await pilot.press("ctrl+enter")
        await pilot.pause()
        assert app.session.dirty
        assert len(app.session.preview().semantic_changes) == 2
        await pilot.click("#undo")
        await pilot.pause()
        assert not app.session.dirty


async def test_compact_review_inplace_and_backup_restore(synthetic_saves: SyntheticSaveSet) -> None:
    original = synthetic_saves.preferences.read_bytes()
    app = ArcSaveLabApp(synthetic_saves.request(SaveKind.PREFERENCES))
    async with app.run_test(size=(80, 24)) as pilot:
        await navigate(app, pilot, 2)
        app.query_one("#items").focus()
        await pilot.press("enter")
        await pilot.pause()
        app.screen.query_one("#edit-0", Input).value = "789"
        await pilot.press("ctrl+enter")
        await pilot.pause()
        await pilot.click("#review")
        await pilot.pause()
        assert app.screen.query_one("#save").region.bottom <= 24
        app.screen.query_one("#save-mode", Select).value = "replace"
        await pilot.pause()
        await pilot.click("#save")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, ReviewScreen)
        assert synthetic_saves.preferences.read_bytes() == original
        await pilot.click("#save")
        await pilot.pause()
        await pilot.click("#confirm")
        await pilot.pause()
        assert app.last_receipt and app.last_receipt.backup_directory
        assert synthetic_saves.preferences.read_bytes() != original
        await pilot.press("ctrl+h")
        await pilot.pause()
        assert isinstance(app.screen, BackupScreen)
        await pilot.click("#restore")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click("#confirm")
        await pilot.pause()
        assert synthetic_saves.preferences.read_bytes() == original
        assert app.session and not app.session.dirty
