from __future__ import annotations

import pytest

from arcsavelab.domain import (
    Chart,
    DeviceIdentity,
    Difficulty,
    DomainValidationError,
    GameVersion,
    LocalizedText,
    ScoreGrade,
    UnlockRequirement,
    UnlockRequirementKind,
)


def test_game_version_is_a_validated_value_object() -> None:
    version = GameVersion.parse("7.0.255c")

    assert str(version) == "7.0.255c"
    assert version.release == (7, 0, 255)
    assert version.channel == "c"

    with pytest.raises(DomainValidationError):
        GameVersion.parse("latest")


def test_difficulty_and_chart_use_game_vocabulary() -> None:
    chart = Chart(
        song_id="sayonarahatsukoi",
        difficulty=Difficulty.ETERNAL,
        rating=8,
        rating_plus=True,
        chart_designer="Luxance + Exschwasion",
        jacket_designer="",
        version=GameVersion.parse("5.4"),
        release_date=1_709_856_000,
    )

    assert Difficulty.from_code(4) is Difficulty.ETERNAL
    assert chart.display_rating == "8+"
    assert chart.difficulty.short_name == "ETR"
    assert not hasattr(chart, "ratingClass")


def test_localized_text_has_deterministic_fallbacks() -> None:
    title = LocalizedText.from_mapping({"en": "Light", "zh-Hans": "光"})

    assert title.resolve("zh-Hans") == "光"
    assert title.resolve("zh-CN") == "光"
    assert title.resolve("fr") == "Light"
    assert title.available_locales == ("en", "zh-Hans")


def test_device_identity_requires_both_game_identity_parts() -> None:
    identity = DeviceIdentity(device_id="installation-identity", player_id=123456)

    assert identity.is_complete

    with pytest.raises(DomainValidationError):
        DeviceIdentity(device_id="", player_id=123456)
    with pytest.raises(DomainValidationError):
        DeviceIdentity(device_id="installation-identity", player_id=-1)


def test_unlock_requirement_exposes_typed_game_concepts_not_parameter_keys() -> None:
    requirement = UnlockRequirement(
        kind=UnlockRequirementKind.CLEAR_SONG,
        song_id="snowwhite",
        difficulty=Difficulty.PRESENT,
        minimum_grade=ScoreGrade.D,
    )

    assert requirement.song_id == "snowwhite"
    assert requirement.difficulty is Difficulty.PRESENT
    assert requirement.minimum_grade is ScoreGrade.D
    assert not hasattr(requirement, "params")
