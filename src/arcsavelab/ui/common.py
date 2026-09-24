from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.events import Resize
from textual.screen import ModalScreen

from arcsavelab.errors import ArcSaveError
from arcsavelab.i18n import Translator
from arcsavelab.interface import Availability, FieldView, Section

SECTION_NAMES = {
    Section.SETTINGS: ("Settings", "游玩设置"),
    Section.FRAGMENTS: ("Fragments", "残片"),
    Section.OWNERSHIP: ("Owned content", "拥有内容"),
    Section.PARTNERS: ("Partners", "搭档"),
    Section.FAVORITES: ("Favorites", "收藏"),
    Section.STORY: ("Story", "故事"),
    Section.CHARACTER_SKILLS: ("Partner skills", "搭档技能"),
    Section.FINALE: ("Axiom of the End", "Axiom of the End"),
    Section.UNLOCKS: ("Unlock progress", "解锁进度"),
    Section.MISSIONS: ("Missions", "新手任务"),
    Section.SCORES: ("Scores", "成绩记录"),
    Section.ACCOUNT_DIAGNOSTICS: ("Account", "账号诊断"),
}


class ResponsiveModal[T](ModalScreen[T]):
    """Modal screens are separate roots; inherit compact styling explicitly."""

    def on_resize(self, event: Resize) -> None:
        self.set_class(event.size.width < 100, "compact")


def local(tr: Translator, english: str, chinese: str) -> str:
    return chinese if tr.locale == "zh-Hans" else english


def value_text(value: Any, tr: Translator) -> str:
    if isinstance(value, bool):
        return local(tr, "Yes", "是") if value else local(tr, "No", "否")
    if value is None or value == "":
        return "—"
    return str(value).replace("\n", " ↵ ").replace("\r", "")


def field_text(field: FieldView, tr: Translator) -> str:
    if field.sensitive:
        return "••••••••"
    for value, label in field.editor.choices:
        if value == field.value:
            return label
    return value_text(field.value, tr)


def field_rich(field: FieldView, tr: Translator, *, detail: bool = False) -> Text:
    """Keep literal data out of Rich markup and retain semantic colors on selection."""
    value = field_text(field, tr)
    if detail and not field.sensitive and isinstance(field.value, str) and not field.editor.choices:
        value = field.value or "—"
    style = ""
    if not field.sensitive:
        if isinstance(field.value, bool):
            style = "#77dd99" if field.value else "#ff8585"
        elif isinstance(field.value, (int, float)):
            style = "#80caff"
    return Text(value, style=style)


def availability_rich(value: Availability, tr: Translator) -> Text:
    colors = {
        Availability.ENABLED: "#77dd99",
        Availability.READ_ONLY: "#a8b9d6",
        Availability.NEEDS_CONTEXT: "#ffd479",
        Availability.NEEDS_SOURCE: "#ffd479",
        Availability.EXPERT_ONLY: "#ffd479",
    }
    return Text(tr(f"status.{value.value}"), style=colors[value])


def editable(field: FieldView) -> bool:
    return not field.derived and not field.sensitive and field.availability is Availability.ENABLED


def error_text(exc: Exception, tr: Translator) -> str:
    if isinstance(exc, ArcSaveError):
        detail = str(exc)
        if not detail or detail == exc.message_id:
            detail = str(exc.details.get("reason", ""))
        return f"{tr(exc.message_id)} · {detail}".rstrip(" ·")
    return str(exc)
