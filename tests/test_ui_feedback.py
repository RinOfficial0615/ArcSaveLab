from dataclasses import replace

import pytest
from rich.text import Text
from textual.widgets import Button, DataTable, Input, Static

from arcsavelab.formats.prefs_xml import SharedPrefsDocument
from arcsavelab.i18n import Translator
from arcsavelab.integrity import direct_value_hash, value_map_hash
from arcsavelab.interface import Availability, BrowseQuery, EditorSpec, FieldView, SaveKind, Section
from arcsavelab.session import SaveSession
from arcsavelab.ui.app import ArcSaveLabApp
from arcsavelab.ui.common import availability_rich, field_rich
from arcsavelab.ui.editor import EditScreen
from arcsavelab.ui.table import ItemTable

from .conftest import SyntheticSaveSet
from .test_tui import navigate


@pytest.mark.parametrize("size", [(80, 24), (120, 36)])
@pytest.mark.parametrize("refocus", [False, True])
async def test_click_offscreen_selection_keeps_viewport(
    synthetic_saves: SyntheticSaveSet, size: tuple[int, int], refocus: bool
) -> None:
    app = ArcSaveLabApp(synthetic_saves.request(SaveKind.PREFERENCES))
    async with app.run_test(size=size) as pilot:
        await navigate(app, pilot, 5)
        table = app.query_one("#items", DataTable)
        await pilot.click("#items", offset=(4, 1))
        if refocus:
            app.query_one("#search").focus()
            await pilot.pause()
        for scroll, row in [(50, 52), (10, 12), (80, 82)]:
            table.scroll_to(y=scroll, animate=False, force=True)
            await pilot.pause()
            before = table.scroll_offset
            expected = table.ordered_rows[row].key.value
            await pilot.click("#items", offset=(4, 3))
            await pilot.pause()
            assert table.scroll_offset == before
            assert app.selected_id == expected


@pytest.mark.parametrize("locale", ["en", "zh-Hans"])
@pytest.mark.parametrize("size", [(80, 24), (120, 36)])
async def test_drag_header_preserves_selection_and_widths(
    synthetic_saves: SyntheticSaveSet, locale: str, size: tuple[int, int]
) -> None:
    app = ArcSaveLabApp(replace(synthetic_saves.request(SaveKind.PREFERENCES), locale=locale))
    async with app.run_test(size=size) as pilot:
        await navigate(app, pilot, 5)
        table = app.query_one("#items", ItemTable)
        await pilot.click("#items", offset=(4, 2))
        await pilot.pause()
        selected = app.selected_id
        table.scroll_to(y=50, animate=False, force=True)
        await pilot.pause()
        for index in (1, 2):
            widths = [column.width for column in table.ordered_columns]
            edge = sum(c.get_render_width(table) for c in table.ordered_columns[: index + 1]) - 2
            table.scroll_to(x=max(0, edge - table.size.width + 10), animate=False, force=True)
            await pilot.pause()
            x = edge - round(table.scroll_x)
            before = table.scroll_offset
            await pilot.mouse_down("#items", offset=(x, 0))
            await pilot.hover("#items", offset=(x + 4, 0))
            await pilot.mouse_up("#items", offset=(x + 4, 0))
            await pilot.pause()
            assert table.ordered_columns[index].width == widths[index] + 4
            assert table.scroll_y == before.y
            assert app.selected_id == selected
            assert not isinstance(app.screen, EditScreen)
        widths = [column.width for column in table.ordered_columns]
        app.query_one("#search", Input).value = "sayonara"
        await pilot.pause()
        assert [column.width for column in table.ordered_columns] == widths
        app.action_language()
        await pilot.pause()
        assert [column.width for column in table.ordered_columns] == widths


async def test_ctrl_shift_mouse_selection_and_auto_fit(
    synthetic_saves: SyntheticSaveSet,
) -> None:
    app = ArcSaveLabApp(synthetic_saves.request(SaveKind.PREFERENCES))
    async with app.run_test(size=(120, 36)) as pilot:
        await navigate(app, pilot, 5)
        table = app.query_one("#items", ItemTable)
        table.focus()
        await pilot.click("#items", offset=(4, 2))
        await pilot.click("#items", offset=(4, 3), control=True)
        await pilot.pause()
        assert len(app.marked) == 2
        await pilot.click("#items", offset=(4, 5), shift=True)
        await pilot.pause()
        assert len(app.marked) >= 2
        index = 1
        before = table.ordered_columns[index].width
        table._auto_size(index)
        assert table.ordered_columns[index].width >= 6
        assert (
            table.ordered_columns[index].width != before
            or table.ordered_columns[index].content_width + 2 == before
        )


@pytest.mark.parametrize("locale", ["en", "zh-Hans"])
async def test_private_details_and_finale_raw_view(
    synthetic_saves: SyntheticSaveSet, locale: str
) -> None:
    prefs = SharedPrefsDocument.from_path(synthetic_saves.preferences)
    prefs.set("a_t", "synthetic-[red]-token", kind="string")
    synthetic_saves.preferences.write_bytes(prefs.to_bytes())
    original = synthetic_saves.preferences.read_bytes()
    app = ArcSaveLabApp(replace(synthetic_saves.request(SaveKind.PREFERENCES), locale=locale))
    async with app.run_test(size=(80, 24)) as pilot:
        await navigate(app, pilot, 12)
        table = app.query_one("#items", DataTable)
        assert "synthetic-[red]-token" not in str(app.items)
        assert "123" not in str(table.get_row_at(0))
        for row, expected, field in [
            (0, "123", "player_id"),
            (1, "synthetic-[red]-token", "masked"),
        ]:
            table.move_cursor(row=row)
            table.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, EditScreen)
            assert expected in str(app.screen.query_one(f"#detail-{field}", Static).render())
            assert app.screen.query_one("#apply", Button).disabled
            await pilot.press("escape")
            await pilot.pause()
            assert expected not in str(table.get_row_at(row))
        await navigate(app, pilot, 8)
        table.focus()
        await pilot.press("enter")
        await pilot.pause()
        assert "0|1|1337" in str(app.screen.query_one("#detail-raw", Static).render())
        assert "[2]  1337" in str(app.screen.query_one("#detail-entries", Static).render())
        assert app.screen.query_one("#apply", Button).disabled
        assert app.session and not app.session.dirty
    assert synthetic_saves.preferences.read_bytes() == original


@pytest.mark.parametrize("raw", ["", "0|oops", "0||1337", "0|1|1337"])
def test_finale_preserves_uninterpreted_entries(
    synthetic_saves: SyntheticSaveSet, raw: str
) -> None:
    prefs = SharedPrefsDocument.from_path(synthetic_saves.preferences)
    prefs.set("fin_v", raw, kind="string")
    prefs.set("fin_k", direct_value_hash(raw) if raw else "", kind="string")
    synthetic_saves.preferences.write_bytes(prefs.to_bytes())
    with SaveSession.open(synthetic_saves.request(SaveKind.PREFERENCES)) as session:
        item = session.inspect(BrowseQuery(Section.FINALE)).sections[0].items[0]
        fields = {field.id: field for field in item.fields}
        assert fields["raw"].value == raw
        assert fields["raw"].detail_only
        assert fields["count"].value == (len(raw.split("|")) if raw else 0)
        assert "not the full layout" in item.subtitle
        assert all(field.availability is Availability.READ_ONLY for field in item.fields)


def test_challenge_labels_keep_target_and_raw_key(synthetic_saves: SyntheticSaveSet) -> None:
    keys = [
        "testify_challenge|1|102",
        "deinosphaineinChallenge6_challenge|2|102",
        "synthetic_unknown_challenge|0|102",
    ]
    path = synthetic_saves.unlocks
    entries = "".join(f"<key>{key}</key><integer>0</integer>" for key in keys)
    path.write_bytes(path.read_bytes().replace(b"</dict>", entries.encode() + b"</dict>"))
    prefs = SharedPrefsDocument.from_path(synthetic_saves.preferences)
    prefs.set("un_k", value_map_hash(path), kind="string")
    synthetic_saves.preferences.write_bytes(prefs.to_bytes())
    with SaveSession.open(
        synthetic_saves.request(SaveKind.PREFERENCES, SaveKind.UNLOCK_PROGRESS)
    ) as session:
        for key in keys:
            items = session.inspect(BrowseQuery(Section.UNLOCKS, search=key)).sections[0].items
            assert len(items) == 1
            assert key.split("|")[0] in items[0].label
            assert key in items[0].subtitle
        items = session.inspect(BrowseQuery(Section.UNLOCKS, search="testify_challenge"))
        assert "Testify" in items.sections[0].items[0].label


def test_semantic_colors_do_not_interpret_markup() -> None:
    tr = Translator("zh-Hans")
    for value, word, color in [(True, "是", "#77dd99"), (False, "否", "#ff8585")]:
        field = FieldView("x", "field.value", value, EditorSpec("boolean"))
        rendered = field_rich(field, tr)
        assert rendered.plain == word and rendered.style == color
    assert availability_rich(Availability.NEEDS_CONTEXT, tr).style == "#ffd479"
    field = FieldView("x", "field.value", "[red]literal", EditorSpec("text"))
    assert field_rich(field, tr).plain == "[red]literal"
    assert isinstance(field_rich(field, tr), Text)
