from dataclasses import replace

from arcsavelab.catalog import load_catalog
from arcsavelab.i18n import Translator
from arcsavelab.interface import BrowseQuery, SaveKind, Section
from arcsavelab.session import SaveSession

from .conftest import SyntheticSaveSet


def test_all_partners_have_official_names_and_skill_text() -> None:
    catalog = load_catalog("7.0.260c")
    for partner in catalog.partners:
        assert partner.display_name.resolve("zh-Hans")
        assert partner.display_name.resolve("en")
        if partner.skill.id:
            assert partner.skill.description.resolve("zh-Hans")
            assert partner.skill.description.resolve("en")
        if partner.skill.uncapped_id:
            assert partner.skill.uncapped_description.resolve("zh-Hans")
    assert catalog.partner(0).display_name.resolve("zh-Hans") == "光"
    assert catalog.partner(7).display_name.resolve("zh-Hans") == "对立 (Grievous Lady)"
    assert catalog.partner(73).display_name.resolve("zh-Hans") == "露恩"
    assert catalog.partner(69).display_name.resolve("zh-Hans") == "Ilith & Ivy"
    assert "[ANS]" not in catalog.partner(92).display_name.resolve("en")
    # No entire game-message catalog is restored by this targeted extraction.
    assert catalog.summary.simplified_chinese_message_count == 0
    assert catalog.translate("Hikari", locale="zh-Hans") == "Hikari"


def test_official_terms_and_judgement_total_are_not_mistranslated() -> None:
    tr = Translator("zh-Hans")
    assert tr("section.finale") == "Axiom of the End"
    assert tr("field.shiny_pure") == "大 Pure（大 P）"
    assert tr("field.pure") == "Pure 总数（大 P + 小 P）"
    assert Translator("en")("field.shiny_pure") == "Shiny PURE"


def test_localized_choices_partner_search_and_explanations(
    synthetic_saves: SyntheticSaveSet,
) -> None:
    prefs = synthetic_saves.preferences
    original = prefs.read_bytes()
    prefs.write_bytes(
        original.replace(
            b"</map>",
            b'<string name="songSort">TITLE</string>'
            b'<string name="songOrder">ASCENDING</string></map>',
        )
    )
    session = SaveSession.open(
        replace(
            synthetic_saves.request(SaveKind.PREFERENCES),
            locale="zh-Hans",
        )
    )
    try:
        settings = session.inspect(BrowseQuery(Section.SETTINGS)).sections[0].items
        by_id = {item.id: item for item in settings}
        assert by_id["setting:songSort"].fields[0].editor.choices == (
            ("TITLE", "标题"),
            ("DIFFICULTY", "难度"),
            ("GRADE", "评级"),
            ("DATE", "日期"),
            ("CLEARTYPE", "通关类型"),
        )
        assert by_id["setting:songOrder"].fields[0].editor.choices == (
            ("ASCENDING", "升序"),
            ("DESCENDING", "降序"),
        )
        partners = session.inspect(BrowseQuery(Section.PARTNERS, search="露恩")).sections[0].items
        assert len(partners) == 1 and partners[0].id == "partner:73"
        assert "觉醒后" in partners[0].subtitle
        assert "%@" not in partners[0].subtitle
        assert "动态参数" in partners[0].subtitle
        skills = session.inspect(BrowseQuery(Section.CHARACTER_SKILLS)).sections[0].items
        assert skills[0].label.startswith("白姬")
        assert "不是搭档等级" in skills[0].subtitle
    finally:
        session.close(discard=True)
