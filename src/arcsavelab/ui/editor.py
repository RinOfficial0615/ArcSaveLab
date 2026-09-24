from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Label, Select, Static, Switch

from arcsavelab.formats.st3_sqlite import calculate_score
from arcsavelab.i18n import Translator
from arcsavelab.interface import BatchSet, FieldView, ItemView, SetField

from .common import ResponsiveModal, editable, error_text, field_rich, local
from .help import item_help


class EditScreen(ResponsiveModal[bool]):
    """One isolated form per edit; validation errors never dismiss or lose the draft."""

    BINDINGS = [("escape", "cancel", "Cancel"), ("ctrl+enter", "apply", "Apply")]

    _SCORE_PREVIEW_FIELDS = frozenset({"shiny_pure", "pure", "far", "lost"})

    def __init__(
        self,
        items: Sequence[ItemView],
        revision: int,
        tr: Translator,
        apply: Callable[[BatchSet], object],
        *,
        read_only: bool = False,
    ) -> None:
        super().__init__()
        self.items = tuple(items)
        self.revision = revision
        self.tr = tr
        self.apply_operation = apply
        self.read_only = read_only
        self._mixed_fields = {
            field.id
            for field in items[0].fields
            if len(items) > 1
            and any(
                next((other.value for other in item.fields if other.id == field.id), field.value)
                != field.value
                for item in items[1:]
            )
        }
        self._score_field = (
            next((field for field in items[0].fields if field.id == "score"), None)
            if len(items) == 1
            else None
        )
        self.fields = tuple(
            field
            for field in items[0].fields
            if all(
                any(
                    other.id == field.id and other.editor == field.editor and editable(other)
                    for other in item.fields
                )
                for item in items
            )
            and editable(field)
            and not read_only
        )

    def compose(self) -> ComposeResult:
        title = (
            self.items[0].label
            if len(self.items) == 1
            else local(
                self.tr, f"Batch edit · {len(self.items)} items", f"批量编辑 · {len(self.items)} 项"
            )
        )
        with Vertical(id="edit-dialog", classes="dialog"):
            yield Label(title, classes="dialog-title", markup=False)
            yield Static(
                local(
                    self.tr,
                    "Changes stay in memory until you review and save. Esc cancels this draft.",
                    "修改先暂存于内存；审阅保存后才写入文件。Esc 取消本次草稿。",
                ),
                classes="muted",
            )
            with VerticalScroll(id="form-fields"):
                if len(self.items) == 1:
                    yield Static(
                        item_help(self.items[0], self.tr),
                        id="item-explanation",
                        classes="explanation",
                        markup=False,
                    )
                    yield Static(self.items[0].id, classes="muted", markup=False)
                    if self.read_only:
                        yield Static(
                            local(
                                self.tr,
                                "Read-only · viewing does not change this record.",
                                "只读 · 查看说明不会修改该记录。",
                            ),
                            classes="readonly",
                        )
                    for field in self.items[0].fields:
                        if self.read_only or not editable(field):
                            yield Static(
                                Text(f"{self.tr(field.label_id)}  ").append_text(
                                    field_rich(field, self.tr, detail=True)
                                ),
                                id=f"detail-{field.id}",
                                markup=False,
                                classes="readonly",
                            )
                for index, field in enumerate(self.fields):
                    yield Label(self.tr(field.label_id), markup=False)
                    yield self._editor(field, index)
                    if field.editor.minimum is not None or field.editor.maximum is not None:
                        minimum = field.editor.minimum
                        maximum = field.editor.maximum
                        yield Static(
                            f"{minimum if minimum is not None else '…'}"
                            + local(self.tr, " ≤ value ≤ ", " ≤ 数值 ≤ ")
                            + f"{maximum if maximum is not None else '…'}",
                            classes="muted",
                        )
                if not self.fields and len(self.items) > 1:
                    yield Static(
                        local(self.tr, "No shared editable fields.", "没有共同的可编辑字段。")
                    )
            yield Static("", id="form-error", classes="error", markup=False)
            with Horizontal(classes="dialog-actions"):
                yield Button(self.tr("action.cancel"), id="cancel")
                yield Button(
                    local(self.tr, "Stage changes", "暂存修改"),
                    variant="primary",
                    id="apply",
                    disabled=not self.fields,
                )

    def _editor(self, field: FieldView, index: int) -> Input | Select[Any] | Switch:
        widget_id = f"edit-{index}"
        if field.editor.kind == "boolean":
            if field.id in self._mixed_fields:
                return Select(
                    [
                        (local(self.tr, "Different", "[不同]"), "__mixed__"),
                        ("Yes", True),
                        ("No", False),
                    ],
                    value="__mixed__",
                    allow_blank=False,
                    id=widget_id,
                )
            return Switch(bool(field.value), id=widget_id)
        if field.editor.kind == "choice":
            choices = [(label, value) for value, label in field.editor.choices]
            if field.id in self._mixed_fields:
                choices.insert(0, (local(self.tr, "Different", "[不同]"), "__mixed__"))
            if not any(value == field.value for _, value in choices):
                choices.insert(0, (local(self.tr, "Keep current", "保持原值"), field.value))
            return Select(choices, value=field.value, allow_blank=False, id=widget_id)
        return Input(
            local(self.tr, "[different]", "[不同]")
            if field.id in self._mixed_fields
            else str(field.value),
            id=widget_id,
            select_on_focus=True,
        )

    def on_mount(self) -> None:
        if self.fields:
            self.query_one("#edit-0").focus(scroll_visible=False)
        self.call_after_refresh(
            self.query_one("#form-fields", VerticalScroll).scroll_home, animate=False
        )

    @on(Input.Changed)
    def preview_score(self, event: Input.Changed) -> None:
        """Live-update the derived score while judgement inputs change.

        The static explanation above the form is intentionally left alone.
        """
        if self._score_field is None:
            return
        changed_index = next(
            (index for index, field in enumerate(self.fields) if f"edit-{index}" == event.input.id),
            None,
        )
        if changed_index is None or self.fields[changed_index].id not in self._SCORE_PREVIEW_FIELDS:
            return
        judgements: dict[str, int] = {}
        for index, field in enumerate(self.fields):
            if field.id not in self._SCORE_PREVIEW_FIELDS:
                continue
            widget = self.query_one(f"#edit-{index}", Input)
            try:
                judgements[field.id] = int(widget.value.strip())
            except ValueError:
                return
        score = calculate_score(
            judgements["pure"], judgements["far"], judgements["lost"], judgements["shiny_pure"]
        )
        self.query_one("#detail-score", Static).update(
            Text(f"{self.tr(self._score_field.label_id)}  ").append_text(
                Text(str(score), style="#80caff")
            )
        )

    def action_cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#cancel")
    def cancel_button(self) -> None:
        self.action_cancel()

    @on(Button.Pressed, "#apply")
    def action_apply(self) -> None:
        if not self.fields:
            return
        changes: list[SetField] = []
        try:
            for index, field in enumerate(self.fields):
                widget = self.query_one(f"#edit-{index}")
                value: Any
                if isinstance(widget, Switch):
                    value = widget.value
                elif isinstance(widget, Select):
                    value = widget.value
                    if value == "__mixed__":
                        continue
                else:
                    assert isinstance(widget, Input)
                    raw = widget.value.strip()
                    if field.id in self._mixed_fields and raw in {"[不同]", "[different]"}:
                        continue
                    value = (
                        int(raw)
                        if field.editor.kind == "integer"
                        else (float(raw) if field.editor.kind in {"float", "real"} else raw)
                    )
                for item in self.items:
                    old = next(f.value for f in item.fields if f.id == field.id)
                    if value != old:
                        changes.append(SetField(item.id, field.id, value, self.revision))
            if changes:
                self.apply_operation(BatchSet(changes, self.revision, "form_edit"))
            self.dismiss(bool(changes))
        except Exception as exc:
            self.query_one("#form-error", Static).update(error_text(exc, self.tr))
