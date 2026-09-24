from dataclasses import replace

import pytest
from textual.widgets import Button, Input, Static

from arcsavelab.interface import SaveKind
from arcsavelab.ui.app import ArcSaveLabApp
from arcsavelab.ui.dialogs import ConfirmScreen, IdentityScreen
from arcsavelab.ui.editor import EditScreen

from .conftest import SyntheticSaveSet
from .test_tui import navigate


@pytest.mark.parametrize("locale", ["en", "zh-Hans"])
@pytest.mark.parametrize("size", [(80, 24), (120, 36)])
async def test_deliberate_mouse_activation_and_explanation(
    synthetic_saves: SyntheticSaveSet, locale: str, size: tuple[int, int]
) -> None:
    app = ArcSaveLabApp(replace(synthetic_saves.request(SaveKind.PREFERENCES), locale=locale))
    async with app.run_test(size=size) as pilot:
        await navigate(app, pilot, 1)
        await pilot.click("#items", offset=(4, 1))
        await pilot.pause()
        assert not isinstance(app.screen, EditScreen)
        await pilot.click("#items", offset=(4, 1), times=2)
        await pilot.pause()
        assert isinstance(app.screen, EditScreen)
        assert "30" in str(app.screen.query_one("#item-explanation", Static).render())
        assert app.screen.query_one("#apply").region.bottom <= size[1]
        if size[0] == 80:
            assert app.screen.has_class("compact")
            assert (
                app.screen.query_one("#edit-0").region.bottom
                <= app.screen.query_one("#form-fields").region.bottom
            )
        await pilot.press("escape")
        await pilot.pause()
        app.query_one("#items").focus()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, EditScreen)
        assert app.session and not app.session.dirty


async def test_readonly_details_are_available_without_editing(
    synthetic_saves: SyntheticSaveSet,
) -> None:
    app = ArcSaveLabApp(synthetic_saves.request(SaveKind.PREFERENCES))
    async with app.run_test(size=(80, 24)) as pilot:
        await navigate(app, pilot, 8)
        app.query_one("#items").focus()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, EditScreen)
        assert "Axiom of the End" in str(app.screen.query_one("#item-explanation", Static).render())
        assert app.screen.query_one("#apply", Button).disabled
        assert app.session and not app.session.dirty


@pytest.mark.parametrize("locale", ["en", "zh-Hans"])
async def test_compact_identity_guide_preserves_failed_input(
    synthetic_saves: SyntheticSaveSet,
    locale: str,
) -> None:
    app = ArcSaveLabApp(replace(synthetic_saves.request(SaveKind.PREFERENCES), locale=locale))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("ctrl+i")
        await pilot.pause()
        assert isinstance(app.screen, IdentityScreen)
        assert app.screen.query_one("#device-id", Input).value == "synthetic-device"
        assert not app.screen.query_one("#device-id", Input).password
        guide = str(app.screen.query_one("#identity-guide", Static).render())
        assert "adb shell settings get secure android_id" in guide
        assert "Android 8+" in guide
        assert app.screen.query_one("#identity-apply").region.bottom <= 24

        def fail(value: tuple[str, int | None]) -> None:
            raise ValueError("synthetic identity error")

        app.screen.apply_operation = fail
        app.screen.query_one("#device-id", Input).value = "preserve-this-draft"
        await pilot.click("#identity-apply")
        await pilot.pause()
        assert isinstance(app.screen, IdentityScreen)
        assert app.screen.query_one("#device-id", Input).value == "preserve-this-draft"
        assert "synthetic identity error" in str(
            app.screen.query_one("#identity-error", Static).render()
        )


async def test_clear_search_and_help_on_compact_screen(synthetic_saves: SyntheticSaveSet) -> None:
    app = ArcSaveLabApp(synthetic_saves.request(SaveKind.PREFERENCES))
    async with app.run_test(size=(80, 24)) as pilot:
        await navigate(app, pilot, 1)
        app.query_one("#search", Input).value = "not-a-record"
        await pilot.pause()
        assert not app.items
        await pilot.click("#clear-search")
        await pilot.pause()
        assert app.items
        await pilot.press("f1")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        assert app.screen.information
        assert app.screen.query_one("#confirm").region.bottom <= 24
        await pilot.click("#confirm")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmScreen)


async def test_language_switch_keeps_one_localized_footer_binding() -> None:
    app = ArcSaveLabApp()
    async with app.run_test(size=(80, 24)) as pilot:
        for _ in range(4):
            await pilot.click("#language")
            await pilot.pause(0.4)  # Button's press animation intentionally debounces clicks.
        assert app.tr.locale == "zh-Hans"
        bindings = app._bindings.get_bindings_for_key("ctrl+o")
        assert len(bindings) == 1
        assert bindings[0].description == "打开工作区"
