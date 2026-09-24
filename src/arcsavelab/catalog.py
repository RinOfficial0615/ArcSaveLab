"""Verified package-resource adapter for versioned game catalogs."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any, Protocol, cast

from .domain import (
    Chart,
    CoreRequirement,
    Difficulty,
    GameCatalog,
    GameVersion,
    LocalizedText,
    Mission,
    MissionRequirement,
    MissionRequirementKind,
    MissionReward,
    MissionRewardKind,
    Partner,
    PartnerSkill,
    PartnerStats,
    ScoreGrade,
    Song,
    SongPack,
    SongUnlock,
    StoryEntry,
    StoryEntryKey,
    StoryPath,
    StoryRequirement,
    UnlockRequirement,
    UnlockRequirementKind,
)
from .errors import ArcSaveError

CATALOG_FORMAT = "arcsavelab.catalog/v1"
MANIFEST_FORMAT = "arcsavelab.catalog-manifest/v1"
REQUIRED_FILES = ("catalog.json", "schema.json", "manifest.json")


class CatalogError(ArcSaveError):
    code = "catalog.error"
    message_id = "error.catalog"


class CatalogNotFoundError(CatalogError):
    code = "catalog.not_found"
    message_id = "error.catalog_not_found"


class CatalogIntegrityError(CatalogError):
    code = "catalog.integrity"
    message_id = "error.catalog_integrity"


class UnsupportedCatalogSchemaError(CatalogError):
    code = "catalog.unsupported_schema"
    message_id = "error.catalog_unsupported_schema"


@dataclass(frozen=True, slots=True)
class CatalogVerification:
    version: str
    valid: bool
    verified_files: tuple[str, ...]
    source_asset_count: int
    source_apk_sha256: str
    schema_fingerprint_sha256: str
    catalog_file_sha256: str
    schema_file_sha256: str


class _CatalogDirectory(Protocol):
    @property
    def name(self) -> str: ...

    def joinpath(self, descendant: str) -> _CatalogDirectory: ...

    def is_file(self) -> bool: ...

    def is_dir(self) -> bool: ...

    def iterdir(self) -> Iterator[_CatalogDirectory]: ...

    def read_bytes(self) -> bytes: ...


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _content_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_file(directory: _CatalogDirectory, filename: str) -> bytes:
    item = directory.joinpath(filename)
    try:
        is_file = item.is_file()
    except OSError as exc:
        raise CatalogIntegrityError(f"cannot inspect {filename}: {exc}", filename=filename) from exc
    if not is_file:
        raise CatalogIntegrityError(f"catalog is missing {filename}", filename=filename)
    try:
        return item.read_bytes()
    except OSError as exc:
        raise CatalogIntegrityError(f"cannot read {filename}: {exc}", filename=filename) from exc


def _read_json(filename: str, data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogIntegrityError(
            f"{filename} is not valid UTF-8 JSON", filename=filename
        ) from exc
    if not isinstance(value, dict):
        raise CatalogIntegrityError(f"{filename} root must be an object", filename=filename)
    return cast(dict[str, Any], value)


def _require(condition: bool, message: str, **details: Any) -> None:
    if not condition:
        raise CatalogIntegrityError(message, **details)


def _package_catalog_root() -> _CatalogDirectory:
    root = resources.files("arcsavelab").joinpath("resources").joinpath("catalog")
    return cast(_CatalogDirectory, root)


def list_catalog_versions() -> tuple[str, ...]:
    """List complete catalog versions shipped as package resources."""

    root = _package_catalog_root()
    try:
        entries = tuple(root.iterdir())
    except (FileNotFoundError, OSError):
        return ()
    versions: list[GameVersion] = []
    for entry in entries:
        if not entry.is_dir():
            continue
        try:
            version = GameVersion.parse(entry.name)
        except ValueError:
            continue
        if all(entry.joinpath(filename).is_file() for filename in REQUIRED_FILES):
            versions.append(version)
    return tuple(version.value for version in sorted(versions))


def _packaged_directory(version: str | GameVersion) -> _CatalogDirectory:
    requested = GameVersion.parse(version).value
    if requested not in list_catalog_versions():
        raise CatalogNotFoundError(
            f"catalog version is not packaged: {requested}", version=requested
        )
    return _package_catalog_root().joinpath(requested)


def _walk_normalized_conditions(
    conditions: list[dict[str, Any]],
) -> Iterator[dict[str, Any]]:
    for condition in conditions:
        yield condition
        children = condition.get("conditions", [])
        _require(isinstance(children, list), "unlock condition children must be a list")
        yield from _walk_normalized_conditions(children)


def _assert_unique(
    rows: list[dict[str, Any]],
    key: Callable[[dict[str, Any]], Any],
    label: str,
) -> None:
    seen: set[Any] = set()
    for row in rows:
        try:
            value = key(row)
        except (KeyError, TypeError) as exc:
            raise CatalogIntegrityError(f"malformed {label} record") from exc
        _require(value not in seen, f"duplicate {label}: {value!r}")
        seen.add(value)


def _validate_normalized_catalog(catalog: dict[str, Any], schema: dict[str, Any]) -> None:
    entities = catalog.get("entities")
    _require(isinstance(entities, dict), "catalog entities must be an object")
    assert isinstance(entities, dict)
    required_entities = {
        "songs",
        "charts",
        "packs",
        "partners",
        "story_paths",
        "story_entries",
        "missions",
        "unlocks",
    }
    _require(
        required_entities <= set(entities),
        "catalog is missing one or more game entity collections",
    )
    for name in required_entities:
        _require(isinstance(entities[name], list), f"catalog {name} must be a list")

    songs = cast(list[dict[str, Any]], entities["songs"])
    charts = cast(list[dict[str, Any]], entities["charts"])
    packs = cast(list[dict[str, Any]], entities["packs"])
    partners = cast(list[dict[str, Any]], entities["partners"])
    paths = cast(list[dict[str, Any]], entities["story_paths"])
    entries = cast(list[dict[str, Any]], entities["story_entries"])
    missions = cast(list[dict[str, Any]], entities["missions"])
    unlocks = cast(list[dict[str, Any]], entities["unlocks"])
    _assert_unique(songs, lambda row: row["id"], "song id")
    _assert_unique(songs, lambda row: row["index"], "song index")
    _assert_unique(
        charts,
        lambda row: (row["song_id"], row["difficulty"]),
        "chart key",
    )
    _assert_unique(packs, lambda row: row["id"], "song pack id")
    _assert_unique(partners, lambda row: row["id"], "partner id")
    _assert_unique(paths, lambda row: row["main_id"], "story path")
    _assert_unique(
        entries,
        lambda row: (row["main_id"], row["minor_id"]),
        "story entry",
    )
    _assert_unique(missions, lambda row: row["id"], "mission id")
    _assert_unique(
        unlocks,
        lambda row: (row["song_id"], row["difficulty"]),
        "song unlock",
    )

    live_song_ids = {row["id"] for row in songs if not row.get("deleted", False)}
    chart_keys = {(row["song_id"], row["difficulty"]) for row in charts}
    _require(
        all(row["song_id"] in live_song_ids for row in charts),
        "chart references an unknown or deleted song",
    )
    _require(
        all((row["song_id"], row["difficulty"]) in chart_keys for row in unlocks),
        "song unlock references an unknown chart",
    )
    path_ids = {row["main_id"] for row in paths}
    entry_path_ids = {row["main_id"] for row in entries}
    _require(path_ids == entry_path_ids, "story path and entry collections disagree")

    localization = catalog.get("localization", {})
    zh_hans = localization.get("zh-Hans", {}) if isinstance(localization, dict) else {}
    messages = zh_hans.get("messages", []) if isinstance(zh_hans, dict) else []
    _require(isinstance(messages, list), "zh-Hans messages must be a list")
    assert isinstance(messages, list)
    typed_messages = cast(list[dict[str, Any]], messages)
    _assert_unique(
        typed_messages,
        lambda row: (row["msgid"], row.get("plural_index", -1)),
        "zh-Hans message",
    )

    direct_conditions = sum(len(row["conditions"]) for row in unlocks)
    recursive_conditions = sum(
        1 for unlock in unlocks for _condition in _walk_normalized_conditions(unlock["conditions"])
    )
    counts = catalog.get("counts")
    _require(isinstance(counts, dict), "catalog counts must be an object")
    assert isinstance(counts, dict)
    expected_counts = {
        "songs_total": len(songs),
        "songs_live": len(live_song_ids),
        "songs_deleted": len(songs) - len(live_song_ids),
        "charts": len(charts),
        "packs": len(packs),
        "partners": len(partners),
        "story_paths": len(paths),
        "story_entries": len(entries),
        "missions": len(missions),
        "unlocks": len(unlocks),
        "unlock_conditions_direct": direct_conditions,
        "unlock_conditions_recursive": recursive_conditions,
        "zh_hans_messages": len(typed_messages),
    }
    for name, actual in expected_counts.items():
        _require(
            counts.get(name) == actual,
            f"catalog count mismatch for {name}",
            expected=counts.get(name),
            actual=actual,
        )

    chart_difficulty_counts: dict[str, int] = {}
    for chart in charts:
        difficulty = str(chart["difficulty"])
        chart_difficulty_counts[difficulty] = chart_difficulty_counts.get(difficulty, 0) + 1
    _require(
        counts.get("charts_by_difficulty") == chart_difficulty_counts,
        "catalog chart difficulty counts do not match records",
    )

    schema_counts = {
        "song": len(songs),
        "chart": len(charts),
        "pack": len(packs),
        "partner": len(partners),
        "story_path": len(paths),
        "story_entry": len(entries),
        "mission": len(missions),
        "unlock": len(unlocks),
        "unlock_condition": recursive_conditions,
        "zh_hans_message": len(typed_messages),
    }
    for section, count in schema_counts.items():
        value = schema.get(section)
        _require(isinstance(value, dict), f"schema is missing {section}")
        assert isinstance(value, dict)
        _require(
            value.get("record_count") == count,
            f"schema record count mismatch for {section}",
        )


def _verify_directory(
    directory: _CatalogDirectory,
    expected_version: str | GameVersion | None,
) -> tuple[
    CatalogVerification,
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    raw = {filename: _read_file(directory, filename) for filename in REQUIRED_FILES}
    catalog = _read_json("catalog.json", raw["catalog.json"])
    schema = _read_json("schema.json", raw["schema.json"])
    manifest = _read_json("manifest.json", raw["manifest.json"])

    _require(catalog.get("format") == CATALOG_FORMAT, "unsupported catalog format")
    _require(manifest.get("format") == MANIFEST_FORMAT, "unsupported catalog manifest format")
    version = catalog.get("game_version")
    _require(isinstance(version, str), "catalog game version is missing")
    assert isinstance(version, str)
    parsed_version = GameVersion.parse(version)
    _require(
        manifest.get("game_version") == version,
        "catalog and manifest versions disagree",
    )
    if expected_version is not None:
        expected = GameVersion.parse(expected_version)
        _require(
            parsed_version == expected,
            f"catalog version mismatch: expected {expected}, got {parsed_version}",
        )

    outputs = manifest.get("outputs")
    _require(isinstance(outputs, dict), "manifest outputs are missing")
    assert isinstance(outputs, dict)
    parsed_files = {"catalog.json": catalog, "schema.json": schema}
    file_hashes: dict[str, str] = {}
    for filename, parsed in parsed_files.items():
        expected_output = outputs.get(filename)
        _require(
            isinstance(expected_output, dict),
            f"manifest output entry is missing for {filename}",
        )
        assert isinstance(expected_output, dict)
        file_hash = _file_hash(raw[filename])
        file_hashes[filename] = file_hash
        _require(
            expected_output.get("file_sha256") == file_hash,
            f"{filename} file hash mismatch",
            filename=filename,
        )
        _require(
            expected_output.get("sha256") == _content_hash(parsed),
            f"{filename} content hash mismatch",
            filename=filename,
        )

    schema_fingerprint = schema.get("fingerprint_sha256")
    _require(
        isinstance(schema_fingerprint, str) and len(schema_fingerprint) == 64,
        "schema fingerprint is missing",
    )
    assert isinstance(schema_fingerprint, str)
    schema_without_fingerprint = dict(schema)
    schema_without_fingerprint.pop("fingerprint_sha256", None)
    _require(
        _content_hash(schema_without_fingerprint) == schema_fingerprint,
        "schema fingerprint does not match schema content",
    )
    _require(
        catalog.get("source_schema_fingerprint_sha256") == schema_fingerprint,
        "catalog schema fingerprint does not match schema fingerprint",
    )

    source_assets = manifest.get("source_assets")
    _require(isinstance(source_assets, list), "manifest source assets are missing")
    assert isinstance(source_assets, list)
    typed_source_assets = cast(list[dict[str, Any]], source_assets)
    _require(
        catalog.get("source_assets") == source_assets,
        "catalog and manifest source asset records disagree",
    )
    _require(
        catalog.get("source_apk") == manifest.get("source_apk"),
        "catalog and manifest APK identity disagree",
    )
    _assert_unique(typed_source_assets, lambda row: row["path"], "source asset path")
    for source_asset in typed_source_assets:
        digest = source_asset.get("sha256")
        _require(
            isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
            "source asset has an invalid SHA-256",
        )
    _validate_normalized_catalog(catalog, schema)

    source_apk = manifest.get("source_apk", {})
    _require(isinstance(source_apk, dict), "manifest APK identity is malformed")
    assert isinstance(source_apk, dict)
    verification = CatalogVerification(
        version=version,
        valid=True,
        verified_files=("catalog.json", "schema.json"),
        source_asset_count=len(typed_source_assets),
        source_apk_sha256=str(source_apk.get("sha256", "")),
        schema_fingerprint_sha256=schema_fingerprint,
        catalog_file_sha256=file_hashes["catalog.json"],
        schema_file_sha256=file_hashes["schema.json"],
    )
    return verification, catalog, schema, manifest


def verify_catalog(version: str | GameVersion = "7.0.255c") -> CatalogVerification:
    """Verify packaged catalog hashes, schema fingerprint, counts, and relations."""

    requested = GameVersion.parse(version)
    verification, _catalog, _schema, _manifest = _verify_directory(
        _packaged_directory(requested), requested
    )
    return verification


def verify_catalog_directory(
    directory: str | Path,
    *,
    expected_version: str | GameVersion | None = None,
) -> CatalogVerification:
    """Verify an unpacked catalog directory, e.g. a freshly rebuilt catalog."""

    verification, _catalog, _schema, _manifest = _verify_directory(
        cast(_CatalogDirectory, Path(directory)), expected_version
    )
    return verification


def _optional_version(value: Any) -> GameVersion | None:
    if value in (None, ""):
        return None
    return GameVersion.parse(value)


def _parse_song(row: Mapping[str, Any]) -> Song:
    return Song(
        id=row["id"],
        index=int(row["index"]),
        title=LocalizedText.from_mapping(row.get("title_localized")),
        artist=row.get("artist"),
        pack_id=row.get("pack_id"),
        purchase_id=row.get("purchase_id"),
        version=_optional_version(row.get("version")),
        side=row.get("side"),
        release_date=row.get("release_date"),
        category=row.get("category"),
        world_unlock=bool(row.get("world_unlock", False)),
        remote_download=bool(row.get("remote_download", False)),
        streamable=not bool(row.get("no_stream", False)),
        deleted=bool(row.get("deleted", False)),
    )


def _parse_chart(row: Mapping[str, Any]) -> Chart:
    return Chart(
        song_id=row["song_id"],
        difficulty=Difficulty.from_code(row["difficulty"]),
        rating=int(row["rating"]),
        rating_plus=bool(row["rating_plus"]),
        chart_designer=row["chart_designer"],
        jacket_designer=row["jacket_designer"],
        version=_optional_version(row.get("version")),
        release_date=row.get("release_date"),
        hidden_until=row.get("hidden_until"),
        hidden_until_unlocked=bool(row.get("hidden_until_unlocked", False)),
        world_unlock=bool(row.get("world_unlock", False)),
    )


def _parse_pack(row: Mapping[str, Any]) -> SongPack:
    featured_partner = int(row["plus_partner_id"])
    return SongPack(
        id=row["id"],
        parent_id=row.get("parent_id"),
        name=LocalizedText.from_mapping(row["name_localized"]),
        description=LocalizedText.from_mapping(row["description_localized"]),
        section=row["section"],
        featured_partner_id=featured_partner if featured_partner >= 0 else None,
        extend_pack=bool(row.get("is_extend_pack", False)),
        active_extend_pack=bool(row.get("is_active_extend_pack", False)),
        coming_soon=bool(row.get("coming_soon", False)),
        limited_sale_end_time=row.get("limited_sale_end_time"),
    )


def _parse_partner(row: Mapping[str, Any]) -> Partner:
    stats = row["stats"]
    base = stats["base"]
    maximum = stats["max"]
    skill = row["skill"]
    return Partner(
        id=int(row["id"]),
        name=row["name"],
        search_terms=tuple(row["search_strings"]),
        pack_id=row.get("pack_id"),
        base_partner=bool(row["base_partner"]),
        base_partner_id=row.get("base_partner_id"),
        available=bool(row["available"]),
        previewable=bool(row["previewable"]),
        partner_type=int(row["type"]),
        base_stats=PartnerStats(float(base["frag"]), float(base["step"]), float(base["over"])),
        maximum_stats=PartnerStats(
            float(maximum["frag"]),
            float(maximum["step"]),
            float(maximum["over"]),
        ),
        skill=PartnerSkill(
            id=skill["id"],
            uncapped_id=skill.get("uncapped_id"),
            unlock_level=int(skill["unlock_level"]),
            requires_uncap=bool(skill.get("requires_uncap", False)),
            description=LocalizedText.from_mapping(skill.get("description")),
            uncapped_description=LocalizedText.from_mapping(skill.get("uncapped_description")),
        ),
        uncap_cores=tuple(
            CoreRequirement(core["core_type"], int(core["amount"])) for core in row["uncap_cores"]
        ),
        version_from=GameVersion.parse(row["version_from"]),
        uncap_version_from=_optional_version(row.get("uncap_version_from")),
        display_name=LocalizedText.from_mapping(row.get("display_name")),
    )


def _parse_story_path(row: Mapping[str, Any]) -> StoryPath:
    return StoryPath(
        main_id=row["main_id"],
        story_type=int(row["story_type"]),
        act=int(row["act"]),
        partner_icon_id=row.get("partner_icon_id"),
        partner_icon_x_offset=row.get("partner_icon_x_offset"),
    )


def _parse_story_entry(row: Mapping[str, Any]) -> StoryEntry:
    requirement = row["requirements"]
    extra = row.get("extra", {})
    return StoryEntry(
        key=StoryEntryKey(row["main_id"], int(row["minor_id"])),
        route=row["route"],
        story_type=row["story_type"],
        requirement=StoryRequirement(
            previous_minor_id=requirement.get("previous_minor_id"),
            purchase_id=requirement.get("purchase_id") or None,
            alternate_purchase_id=requirement.get("alternate_purchase_id") or None,
            clear_song_id=requirement.get("clear_song_id") or None,
            clear_partner_id=requirement.get("clear_partner_id"),
            additional_entries=tuple(requirement.get("additional", ())),
            anomaly_id=requirement.get("anomaly_id"),
            block_until_previous_read=bool(requirement.get("block_until_previous_read", False)),
        ),
        unlocked_song_id=row.get("unlocked_song_id"),
        hidden_from_count=bool(row.get("hidden_from_count", False)),
        has_alternative=bool(row.get("has_alternative", False)),
        icon_id=extra.get("icon"),
        primary_partner_id=extra.get("charIcon1"),
        secondary_partner_id=extra.get("charIcon2"),
        narrative_asset_id=extra.get("storyData"),
        illustration_asset_id=extra.get("storyCgPath"),
    )


def _parse_mission_requirement(row: Mapping[str, Any]) -> MissionRequirement:
    source_kind = row["type"]
    if source_kind == "local_clear":
        return MissionRequirement(MissionRequirementKind.LOCAL_CLEAR)
    if source_kind == "clear_mission_id":
        return MissionRequirement(
            MissionRequirementKind.COMPLETE_MISSION,
            related_mission_id=row["id"],
        )
    if source_kind == "custom":
        return MissionRequirement(
            MissionRequirementKind.CUSTOM_CONDITION,
            condition_id=row["id"],
        )
    if source_kind == "potential":
        return MissionRequirement(
            MissionRequirementKind.MINIMUM_POTENTIAL,
            minimum_potential=int(row["value"]),
        )
    raise UnsupportedCatalogSchemaError(f"unknown mission requirement kind: {source_kind!r}")


def _parse_mission_reward(value: str) -> MissionReward:
    if match := re.fullmatch(r"fragment(\d+)", value):
        return MissionReward(MissionRewardKind.FRAGMENTS, int(match.group(1)))
    if match := re.fullmatch(r"core_generic_(\d+)", value):
        return MissionReward(MissionRewardKind.GENERIC_CORES, int(match.group(1)))
    if match := re.fullmatch(r"stamina(\d+)", value):
        return MissionReward(MissionRewardKind.STAMINA, int(match.group(1)))
    if value == "pick_ticket":
        return MissionReward(MissionRewardKind.PICK_TICKET)
    return MissionReward(MissionRewardKind.CONTENT_UNLOCK, content_id=value)


def _parse_mission(row: Mapping[str, Any]) -> Mission:
    return Mission(
        id=row["id"],
        tier=int(row["tier"]),
        requirements=tuple(
            _parse_mission_requirement(requirement) for requirement in row["requirements"]
        ),
        rewards=tuple(_parse_mission_reward(reward) for reward in row["reward_bundle_ids"]),
        missions_to_clear_tier=row.get("missions_to_clear_tier"),
    )


_REQUIREMENT_KINDS = {
    0: UnlockRequirementKind.CREDITS,
    1: UnlockRequirementKind.CLEAR_SONG,
    2: UnlockRequirementKind.PLAY_SONG,
    3: UnlockRequirementKind.PLAY_SONG_TIMES,
    4: UnlockRequirementKind.ANY_OF,
    5: UnlockRequirementKind.PLAYER_RATING,
    6: UnlockRequirementKind.CLEAR_SONGS_OF_LEVEL,
    7: UnlockRequirementKind.LEGACY_SONG_CONDITION,
    8: UnlockRequirementKind.SONG_LAMP,
    9: UnlockRequirementKind.CLEAR_SONGS_OF_DIFFICULTY,
    10: UnlockRequirementKind.READ_STORY,
    80: UnlockRequirementKind.HIDDEN_DIFFICULTY_PLAY,
    101: UnlockRequirementKind.SPECIAL_SEAL,
    102: UnlockRequirementKind.CHALLENGE,
    103: UnlockRequirementKind.SPECIAL_SEAL_PARTNER,
    104: UnlockRequirementKind.FINALE,
    105: UnlockRequirementKind.PARTNER_SKILL_TOGGLE,
    106: UnlockRequirementKind.SONG_PLAYABLE,
    107: UnlockRequirementKind.SPELL_MAGNOLIA,
    108: UnlockRequirementKind.ARGHENA_STORIES,
    109: UnlockRequirementKind.ARGHENA_PUZZLE,
    110: UnlockRequirementKind.ARGHENA_COURSE,
    112: UnlockRequirementKind.ALTER_EGO_PUZZLE,
    113: UnlockRequirementKind.CLEAR_STORY_ACT_SONGS,
    114: UnlockRequirementKind.KONZETSU_CHAIN,
    115: UnlockRequirementKind.KONZETSU_PRECHALLENGE,
}


def _optional_difficulty(value: Any) -> Difficulty | None:
    return Difficulty.from_code(value) if value is not None else None


def _optional_grade(value: Any) -> ScoreGrade | None:
    if value is None:
        return None
    try:
        return ScoreGrade(value)
    except ValueError as exc:
        raise UnsupportedCatalogSchemaError(f"unknown score grade: {value!r}") from exc


def _parse_unlock_requirement(row: Mapping[str, Any]) -> UnlockRequirement:
    requirement_type = int(row["type"])
    try:
        kind = _REQUIREMENT_KINDS[requirement_type]
    except KeyError as exc:
        raise UnsupportedCatalogSchemaError(
            f"unknown song unlock requirement type: {requirement_type}"
        ) from exc
    params = row.get("params", {})
    children = tuple(_parse_unlock_requirement(child) for child in row.get("conditions", ()))
    song_difficulty = params.get("song_difficulty")
    general_difficulty = params.get("difficulty")
    return UnlockRequirement(
        kind=kind,
        children=children,
        song_id=params.get("song_id"),
        difficulty=_optional_difficulty(
            song_difficulty if song_difficulty is not None else general_difficulty
        ),
        minimum_grade=_optional_grade(params.get("grade")),
        required_credits=params.get("credit"),
        play_count=params.get("times"),
        minimum_player_rating=params.get("rating") if requirement_type == 5 else None,
        song_level=params.get("rating") if requirement_type == 6 else None,
        song_level_plus=params.get("ratingPlus"),
        required_clear_count=params.get("count"),
        minimum_lamp=params.get("lamp"),
        story_entry=StoryEntryKey(params["major"], int(params["minor"]))
        if "major" in params and "minor" in params
        else None,
        range_minimum=params.get("min"),
        range_maximum=params.get("max"),
        partner_id=params.get("id", params.get("char_id")),
        requires_awakened_partner=params.get("awakened"),
        inverted=params.get("inverted"),
        puzzle_index=params.get("index"),
        story_act=params.get("act"),
    )


def _parse_song_unlock(row: Mapping[str, Any]) -> SongUnlock:
    return SongUnlock(
        song_id=row["song_id"],
        difficulty=Difficulty.from_code(row["difficulty"]),
        requirements=tuple(
            _parse_unlock_requirement(requirement) for requirement in row["conditions"]
        ),
    )


def _to_domain(catalog: dict[str, Any]) -> GameCatalog:
    entities = catalog["entities"]
    translations: dict[str, dict[str, str]] = {}
    for locale, translation in catalog["localization"].items():
        messages: dict[str, str] = {}
        for message in translation["messages"]:
            if "plural_index" not in message or message["plural_index"] == 0:
                messages[message["msgid"]] = message["msgstr"]
        translations[locale] = messages
    try:
        return GameCatalog(
            version=GameVersion.parse(catalog["game_version"]),
            songs=(_parse_song(row) for row in entities["songs"]),
            charts=(_parse_chart(row) for row in entities["charts"]),
            packs=(_parse_pack(row) for row in entities["packs"]),
            partners=(_parse_partner(row) for row in entities["partners"]),
            story_paths=(_parse_story_path(row) for row in entities["story_paths"]),
            story_entries=(_parse_story_entry(row) for row in entities["story_entries"]),
            missions=(_parse_mission(row) for row in entities["missions"]),
            song_unlocks=(_parse_song_unlock(row) for row in entities["unlocks"]),
            translations=translations,
        )
    except CatalogError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise UnsupportedCatalogSchemaError(
            f"catalog contains an unsupported game entity shape: {exc}"
        ) from exc


@cache
def _load_packaged(version: str) -> GameCatalog:
    directory = _packaged_directory(version)
    _verification, catalog, _schema, _manifest = _verify_directory(directory, version)
    return _to_domain(catalog)


def load_catalog(version: str | GameVersion = "7.0.255c") -> GameCatalog:
    """Load a verified packaged catalog as immutable game-domain objects."""

    requested = GameVersion.parse(version).value
    return _load_packaged(requested)


def load_catalog_from_directory(
    directory: str | Path,
    *,
    expected_version: str | GameVersion | None = None,
) -> GameCatalog:
    """Load a verified rebuilt catalog without depending on package resources."""

    _verification, catalog, _schema, _manifest = _verify_directory(
        cast(_CatalogDirectory, Path(directory)), expected_version
    )
    return _to_domain(catalog)
