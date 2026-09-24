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


# Difficulty label styles follow the game's own difficulty hues. PST/PRS/FTR/BYD
# are the native constants of SongDifficulty::ColorForDifficultyClass in the game
# binary (#0a82be / #648c3c / #50194b / #822328); ETR and INS follow the 7.0
# multiplayer difficulty-tag textures (#705080 / #203090). The game targets its
# light song-select background, so every hue is re-exposed for this app's dark
# surfaces (#10141e) in OKLCH. Within the purple family the pairs are pulled
# apart on purpose: FTR is the vivid pink-leaning magenta (h~335), ETR a muted
# deeper blue-violet (h~299, 36 degrees away), and INS the light blue-leaning
# periwinkle (h~269).
DIFFICULTY_STYLES = {
    "PST": "#5bb1ea",
    "PRS": "#85bb4f",
    "FTR": "#e377ce",
    "BYD": "#ef7977",
    "ETR": "#a48dd3",
    "INS": "#8da5ea",
}

# Clear-type styles re-expose the hues of the game's clear-badge textures on the
# dark background in OKLCH. The purple-family trio is split across hue AND
# lightness: Track Lost is the deep crimson (h~15, L~0.60), Hard Clear the
# bright bubblegum pink (h~350, L~0.74), Full Recall the violet in between
# (h~310); Pure Memory stays pale cyan, Easy Clear teal-green, and normal
# clears neutral.
CLEAR_TYPE_STYLES = {
    0: "#bd5f69",  # Track Lost
    1: "#9ca5b5",  # Normal Clear
    2: "#ba89de",  # Full Recall
    3: "#b0dbdb",  # Pure Memory
    4: "#5cb999",  # Easy Clear
    5: "#f080b8",  # Hard Clear
}

# Judgement columns: shiny PURE renders one green step deeper than PURE, FAR and
# LOST reuse the app's existing amber/red semantics.
PURE_STYLE = "#77dd99"
SHINY_PURE_STYLE = "#30c060"
FAR_STYLE = "#ffd479"
LOST_STYLE = "#ff8585"


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


def clear_type_rich(field: FieldView, tr: Translator) -> Text:
    """Color a clear-type choice with the game's own badge hues."""
    text = Text(field_text(field, tr))
    style = CLEAR_TYPE_STYLES.get(field.value) if isinstance(field.value, int) else None
    if style:
        text.style = style
    return text


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
