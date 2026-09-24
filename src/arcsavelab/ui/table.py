"""A row browser with deliberate mouse activation and keyboard parity."""

import time
from typing import cast

from rich.text import Text
from textual.events import Click, MouseDown, MouseMove, MouseUp
from textual.message import Message
from textual.widgets import DataTable


class ItemTable(DataTable[str | Text]):
    """Mouse selection and header resizing, isolated from application item identities."""

    last_click_modifiers: tuple[bool, bool] = (False, False)

    class SelectionClick(Message):
        def __init__(self, item_id: str, *, shift: bool, ctrl: bool) -> None:
            super().__init__()
            self.item_id = item_id
            self.shift = shift
            self.ctrl = ctrl

        @property
        def control(self) -> "ItemTable | None":
            return cast("ItemTable | None", self._sender)

    def configure_widths(self, layout: str) -> None:
        if not hasattr(self, "_saved_widths"):
            self._saved_widths: dict[str, list[int]] = {}
        self._layout_name = layout
        self._drag: tuple[int, int, int] | None = None
        self._separator_click: tuple[int, float] | None = None
        self._resize_tooltip = ""
        self._header_labels = [column.label.plain for column in self.ordered_columns]
        # On first navigation the previously hidden table has no laid-out size yet.
        viewport = max(
            self.size.width, self.app.size.width - (22 if self.app.size.width < 100 else 28)
        )
        available = max(40, viewport - 8)
        name_width, value_width = max(18, available * 2 // 5), max(14, available // 3)
        defaults = (
            [1, name_width, value_width, max(14, available - 1 - name_width - value_width)]
            if layout == "general"
            else [1, 32, 12, 18, 7, 7, 18]
        )
        widths = self._saved_widths.get(layout, defaults)
        for index, width in enumerate(widths):
            self._set_width(index, width)

    def _set_width(self, index: int, width: int) -> None:
        column = self.ordered_columns[index]
        column.auto_width = False
        column.width = max(1 if index == 0 else 6, min(240, width))
        label = Text(self._header_labels[index])
        label.truncate(max(0, column.width - 1), pad=True)
        if index < len(self.ordered_columns) - 1:
            label.append("│")
        column.label = label
        # DataTable currently has no public column-width setter. Keep all internal
        # cache/dimension invalidation here and cover it through real mouse tests.
        self._update_count += 1
        self._clear_caches()
        self._require_update_dimensions = True
        self.refresh(layout=True)

    async def _on_mouse_down(self, event: MouseDown) -> None:
        if event.button == 1 and event.y == 0 and hasattr(self, "_layout_name"):
            right = -round(self.scroll_x)
            for index, column in enumerate(self.ordered_columns):
                right += column.get_render_width(self)
                if 0 < index < len(self.ordered_columns) - 1 and abs(event.x - (right - 2)) <= 1:
                    self._drag = (index, event.screen_x, column.width)
                    self.capture_mouse()
                    event.stop()
                    event.prevent_default()
                    return
        await super()._on_mouse_down(event)

    def _on_mouse_move(self, event: MouseMove) -> None:
        if getattr(self, "_resize_tooltip", ""):
            self.tooltip = self._resize_tooltip if event.y == 0 else None
        drag = getattr(self, "_drag", None)
        if drag is not None:
            index, start_x, width = drag
            self._set_width(index, width + event.screen_x - start_x)
            event.stop()
            event.prevent_default()
            return
        super()._on_mouse_move(event)

    async def _on_mouse_up(self, event: MouseUp) -> None:
        drag = getattr(self, "_drag", None)
        if drag is not None:
            index, start_x, width = drag
            if event.screen_x == start_x:
                now = time.monotonic()
                previous = self._separator_click
                if previous and previous[0] == index and now - previous[1] < 0.5:
                    self._auto_size(index)
                    self._separator_click = None
                else:
                    self._separator_click = (index, now)
            self._drag = None
            self.release_mouse()
            self._saved_widths[self._layout_name] = [
                column.width for column in self.ordered_columns
            ]
            event.stop()
            event.prevent_default()
            return
        await super()._on_mouse_up(event)

    def _auto_size(self, index: int) -> None:
        column = self.ordered_columns[index]
        self._set_width(index, max(6, column.content_width + 2))
        self._saved_widths[self._layout_name] = [item.width for item in self.ordered_columns]

    async def _on_click(self, event: Click) -> None:
        # DataTable activates an already highlighted cell even on a single click.
        # Keep selection separate from activation, including the initial row.
        event.prevent_default()
        event.stop()
        row = event.style.meta.get("row")
        if event.button != 1 or not isinstance(row, int) or not 0 <= row < self.row_count:
            return
        self.focus(scroll_visible=False)
        self.last_click_modifiers = (event.shift, event.ctrl)
        # The default pre-scroll targets the *old* cursor before updating it.
        # A clicked row is already visible; only the new coordinate may scroll.
        self.move_cursor(row=row, animate=False, scroll=False)
        item_id = self.ordered_rows[row].key.value
        if isinstance(item_id, str):
            self.post_message(self.SelectionClick(item_id, shift=event.shift, ctrl=event.ctrl))
        if event.chain == 2:
            self.action_select_cursor()
