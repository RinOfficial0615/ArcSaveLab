#!/usr/bin/env python3
"""Build and compare deterministic ArcSaveLab catalogs from Arcaea APKs.

Only Python's standard library is required.  The catalog deliberately reads
metadata assets directly from the APK and does not extract media files.
"""

from __future__ import annotations

import argparse
import collections
import copy
import gettext
import hashlib
import io
import json
import re
import sys
import zipfile
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, cast

from arcsavelab._catalog_labels import PARTNER_NAME_KEYS, SKILL_MESSAGE_KEYS

CATALOG_FORMAT = "arcsavelab.catalog/v1"
DIFF_FORMAT = "arcsavelab.catalog-diff/v1"

ASSET_SONGLIST = "assets/songs/songlist"
ASSET_PACKLIST = "assets/songs/packlist"
ASSET_UNLOCKS = "assets/songs/unlocks"
ASSET_PARTNERS = "assets/char/characters.json"
ASSET_MISSIONS = "assets/startup/missions.json"
ASSET_STORY_PATHS = "assets/app-data/story/paths"
ASSET_ZH_HANS = "assets/tl/zh-Hans.mo"

REQUIRED_ASSETS = (
    ASSET_SONGLIST,
    ASSET_PACKLIST,
    ASSET_UNLOCKS,
    ASSET_PARTNERS,
    ASSET_MISSIONS,
    ASSET_STORY_PATHS,
    ASSET_ZH_HANS,
)

STORY_ENTRY_RE = re.compile(r"^assets/app-data/story/(?P<route>main|side)/entries_(?P<main>[^/]+)$")

DIFFICULTY_NAMES = {0: "PST", 1: "PRS", 2: "FTR", 3: "BYD", 4: "ETR"}

# Names are the reverse-engineered domain labels.  The numeric type remains in
# every record so unknown future types are losslessly represented.
CONDITION_NAMES = {
    0: "credit",
    1: "song_clear",
    2: "song_play",
    3: "play_song_times",
    4: "or",
    5: "player_rating",
    6: "clear_songs_of_level",
    7: "legacy_type_7",
    8: "song_specific_lamp",
    9: "clear_songs_of_difficulty",
    10: "read_story",
    80: "hide_difficulty_song_play",
    101: "special_seal",
    102: "challenge",
    103: "special_seal_character",
    104: "finale_unlock",
    105: "song_toggle_by_character_skill",
    106: "song_playable",
    107: "spell_magnolia",
    108: "arghena_stories",
    109: "arghena_puzzle",
    110: "arghena_course",
    112: "alter_ego_puzzle",
    113: "clear_songs_from_story_act",
    114: "special_seal_konzetsu_chain",
    115: "special_seal_konzetsu_prechallenge",
}


Json = dict[str, Any] | list[Any] | str | int | float | bool | None


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
    path.write_text(payload + "\n", encoding="utf-8", newline="\n")


def read_json_asset(apk: zipfile.ZipFile, path: str) -> Any:
    try:
        data = apk.read(path)
    except KeyError as exc:
        raise ValueError(f"required APK asset is missing: {path}") from exc
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid UTF-8 JSON asset: {path}: {exc}") from exc


def record_schema(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(records)
    fields: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    keysets: collections.Counter[tuple[str, ...]] = collections.Counter()
    for row in rows:
        keysets[tuple(sorted(row))] += 1
        for key, value in row.items():
            fields[key][type(value).__name__] += 1
    result = {
        "record_count": len(rows),
        "fields": {
            key: {
                "present": sum(types.values()),
                "types": dict(sorted(types.items())),
            }
            for key, types in sorted(fields.items())
        },
        "keysets": [
            {"count": count, "fields": list(keys)}
            for keys, count in sorted(keysets.items(), key=lambda item: item[0])
        ],
    }
    result["fingerprint_sha256"] = content_hash(result)
    return result


def unknown_fields(row: dict[str, Any], known: set[str]) -> dict[str, Any]:
    return {key: copy.deepcopy(value) for key, value in row.items() if key not in known}


def normalize_song(row: dict[str, Any]) -> dict[str, Any]:
    known = {
        "id",
        "idx",
        "deleted",
        "title_localized",
        "artist",
        "search_title",
        "search_artist",
        "set",
        "purchase",
        "version",
        "side",
        "date",
        "category",
        "world_unlock",
        "remote_dl",
        "no_stream",
        "difficulties",
    }
    result: dict[str, Any] = {
        "id": row["id"],
        "index": row["idx"],
        "deleted": bool(row.get("deleted", False)),
        "title_localized": copy.deepcopy(row.get("title_localized", {})),
        "artist": row.get("artist"),
        "search_title": copy.deepcopy(row.get("search_title", {})),
        "search_artist": copy.deepcopy(row.get("search_artist", {})),
        "pack_id": row.get("set"),
        "purchase_id": row.get("purchase"),
        "version": row.get("version"),
        "side": row.get("side"),
        "release_date": row.get("date"),
        "category": row.get("category"),
        "world_unlock": row.get("world_unlock", False),
        "remote_download": row.get("remote_dl", False),
        "no_stream": row.get("no_stream", False),
    }
    extra = unknown_fields(row, known)
    if extra:
        result["extra"] = extra
    return result


def normalize_chart(song: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    known = {
        "ratingClass",
        "ratingClassAlias",
        "rating",
        "ratingPlus",
        "chartDesigner",
        "jacketDesigner",
        "version",
        "date",
        "hidden_until",
        "hidden_until_unlocked",
        "world_unlock",
    }
    difficulty = row["ratingClass"]
    result: dict[str, Any] = {
        "song_id": song["id"],
        "difficulty": difficulty,
        "difficulty_name": DIFFICULTY_NAMES.get(difficulty, f"UNKNOWN_{difficulty}"),
        "rating": row["rating"],
        "rating_plus": bool(row.get("ratingPlus", False)),
        # 7.0 marks Inscribed charts with a rating-class alias inside the BYD slot.
        "rating_class_alias": row.get("ratingClassAlias"),
        "chart_designer": row.get("chartDesigner", ""),
        "jacket_designer": row.get("jacketDesigner", ""),
        "version": row.get("version", song.get("version")),
        "release_date": row.get("date", song.get("date")),
        "hidden_until": row.get("hidden_until"),
        "hidden_until_unlocked": bool(row.get("hidden_until_unlocked", False)),
        "world_unlock": row.get("world_unlock", song.get("world_unlock", False)),
    }
    extra = unknown_fields(row, known)
    if extra:
        result["extra"] = extra
    return result


def normalize_pack(row: dict[str, Any]) -> dict[str, Any]:
    known = {
        "id",
        "pack_parent",
        "name_localized",
        "description_localized",
        "section",
        "plus_character",
        "is_extend_pack",
        "is_active_extend_pack",
        "coming_soon",
        "limitedSaleEndTime",
    }
    result: dict[str, Any] = {
        "id": row["id"],
        "parent_id": row.get("pack_parent"),
        "name_localized": copy.deepcopy(row["name_localized"]),
        "description_localized": copy.deepcopy(row["description_localized"]),
        "section": row["section"],
        "plus_partner_id": row["plus_character"],
        "is_extend_pack": bool(row.get("is_extend_pack", False)),
        "is_active_extend_pack": bool(row.get("is_active_extend_pack", False)),
        "coming_soon": bool(row.get("coming_soon", False)),
        "limited_sale_end_time": row.get("limitedSaleEndTime"),
    }
    extra = unknown_fields(row, known)
    if extra:
        result["extra"] = extra
    return result


def normalize_partner(row: dict[str, Any]) -> dict[str, Any]:
    known = {
        "character_id",
        "name",
        "search_strings",
        "pack_id",
        "base_character",
        "base_character_id",
        "is_available",
        "is_previewable",
        "char_type",
        "base_frag",
        "base_prog",
        "base_over",
        "max_frag",
        "max_prog",
        "max_over",
        "skill_id",
        "skill_id_uncap",
        "skill_unlock_level",
        "skill_requires_uncap",
        "uncap_cores",
        "uncap_version_from",
        "version_from",
    }
    result: dict[str, Any] = {
        "id": row["character_id"],
        "name": row["name"],
        "search_strings": copy.deepcopy(row["search_strings"]),
        "pack_id": row.get("pack_id"),
        "base_partner": bool(row.get("base_character", False)),
        "base_partner_id": row.get("base_character_id"),
        "available": bool(row["is_available"]),
        "previewable": bool(row["is_previewable"]),
        "type": row["char_type"],
        "stats": {
            "base": {
                "frag": row["base_frag"],
                "step": row["base_prog"],
                "over": row["base_over"],
            },
            "max": {
                "frag": row["max_frag"],
                "step": row["max_prog"],
                "over": row["max_over"],
            },
        },
        "skill": {
            "id": row["skill_id"],
            "uncapped_id": row.get("skill_id_uncap"),
            "unlock_level": row["skill_unlock_level"],
            "requires_uncap": bool(row.get("skill_requires_uncap", False)),
        },
        "uncap_cores": copy.deepcopy(row["uncap_cores"]),
        "uncap_version_from": row.get("uncap_version_from"),
        "version_from": row["version_from"],
    }
    extra = unknown_fields(row, known)
    if extra:
        result["extra"] = extra
    return result


def localize_partners(partners: list[dict[str, Any]], translation: gettext.GNUTranslations) -> None:
    """Attach only entity display text, never the game's full message catalog."""
    for partner in partners:
        keys = PARTNER_NAME_KEYS.get(partner["name"])
        if keys is None:
            continue  # A future unknown identity remains lossless and visibly unlocalized.
        names: dict[str, str] = {}
        for locale in ("en", "zh-Hans"):
            parts = [translation.gettext(key) if locale == "zh-Hans" else key for key in keys]
            parts = [part.replace("[ANS]", "") for part in parts]
            names[locale] = parts[0] + (f" ({' · '.join(parts[1:])})" if len(parts) > 1 else "")
        partner["display_name"] = names
        for identity, target in (("id", "description"), ("uncapped_id", "uncapped_description")):
            messages = SKILL_MESSAGE_KEYS.get(partner["skill"].get(identity, ""), ())
            if messages:

                def descriptions(locale: str, messages: tuple[str, ...] = messages) -> str:
                    blocks = []
                    for index, message in enumerate(messages):
                        text = translation.gettext(message) if locale == "zh-Hans" else message
                        if len(messages) > 1:
                            prefix = (
                                f"状态 {index + 1}："
                                if locale == "zh-Hans"
                                else f"State {index + 1}: "
                            )
                            text = prefix + text
                        blocks.append(text)
                    return "\n\n".join(blocks)

                partner["skill"][target] = {
                    "en": descriptions("en"),
                    "zh-Hans": descriptions("zh-Hans"),
                }


def parse_main_id(value: str) -> int | str:
    try:
        return int(value, 10)
    except ValueError:
        return value


def normalize_story_path(row: dict[str, Any]) -> dict[str, Any]:
    known = {"main", "storyType", "act", "characterIcon", "characterIconXOffset"}
    result: dict[str, Any] = {
        "main_id": row["main"],
        "story_type": row["storyType"],
        "act": row["act"],
        "partner_icon_id": row.get("characterIcon"),
        "partner_icon_x_offset": row.get("characterIconXOffset"),
    }
    extra = unknown_fields(row, known)
    if extra:
        result["extra"] = extra
    return result


def normalize_story_entry(
    route: str, main_id: int | str, source_path: str, row: dict[str, Any]
) -> dict[str, Any]:
    known = {
        "minor",
        "storyType",
        "requiredMinor",
        "requiredPurchase",
        "requiredPurchaseAlternate",
        "clearSongId",
        "clearCharaId",
        "additionalRequires",
        "requirementAnomalyId",
        "unlockedSongId",
        "blockReadingUntilPrevRead",
        "hiddenFromCount",
        "hasAlternative",
    }
    result: dict[str, Any] = {
        "route": route,
        "main_id": main_id,
        "minor_id": row["minor"],
        "story_type": row["storyType"],
        "requirements": {
            "previous_minor_id": row.get("requiredMinor"),
            "purchase_id": row.get("requiredPurchase"),
            "alternate_purchase_id": row.get("requiredPurchaseAlternate"),
            "clear_song_id": row.get("clearSongId"),
            "clear_partner_id": row.get("clearCharaId"),
            "additional": copy.deepcopy(row.get("additionalRequires", [])),
            "anomaly_id": row.get("requirementAnomalyId"),
            "block_until_previous_read": bool(row.get("blockReadingUntilPrevRead", False)),
        },
        "unlocked_song_id": row.get("unlockedSongId"),
        "hidden_from_count": bool(row.get("hiddenFromCount", False)),
        "has_alternative": bool(row.get("hasAlternative", False)),
        "source_asset": source_path,
    }
    extra = unknown_fields(row, known)
    if extra:
        result["extra"] = extra
    return result


def normalize_condition(row: dict[str, Any]) -> dict[str, Any]:
    condition_type = row.get("type")
    params = {
        key: copy.deepcopy(value) for key, value in row.items() if key not in {"type", "conditions"}
    }
    condition_name = (
        CONDITION_NAMES.get(condition_type) if isinstance(condition_type, int) else None
    )
    result: dict[str, Any] = {
        "type": condition_type,
        "kind": condition_name or f"unknown_type_{condition_type}",
        "params": params,
    }
    if "conditions" in row:
        nested = row["conditions"]
        if not isinstance(nested, list) or not all(isinstance(x, dict) for x in nested):
            raise ValueError(f"unlock condition {condition_type} has invalid children")
        result["conditions"] = [normalize_condition(child) for child in nested]
    return result


def normalize_mo(data: bytes) -> tuple[dict[str, str], list[dict[str, Any]]]:
    translation = gettext.GNUTranslations(io.BytesIO(data))
    metadata = dict(sorted(translation.info().items()))
    messages: list[dict[str, Any]] = []
    parsed_catalog = cast(dict[Any, Any], vars(translation)["_catalog"])
    for key, text in parsed_catalog.items():
        if key == "":
            continue
        if isinstance(key, tuple):
            msgid, plural_index = key
            record: dict[str, Any] = {
                "msgid": msgid,
                "plural_index": plural_index,
                "msgstr": text,
            }
        else:
            record = {"msgid": key, "msgstr": text}
        messages.append(record)
    messages.sort(key=lambda row: (row["msgid"], row.get("plural_index", -1)))
    return metadata, messages


def walk_conditions(
    rows: Iterable[dict[str, Any]], depth: int = 0
) -> Iterator[tuple[dict[str, Any], int]]:
    for row in rows:
        yield row, depth
        nested = row.get("conditions", [])
        if isinstance(nested, list):
            yield from walk_conditions(nested, depth + 1)


def duplicate_values(values: Iterable[Any]) -> list[Any]:
    counter = collections.Counter(values)
    return sorted((value for value, count in counter.items() if count > 1), key=str)


def build_integrity(
    songs: list[dict[str, Any]],
    charts: list[dict[str, Any]],
    packs: list[dict[str, Any]],
    partners: list[dict[str, Any]],
    paths: list[dict[str, Any]],
    entries: list[dict[str, Any]],
    missions: list[dict[str, Any]],
    unlocks: list[dict[str, Any]],
) -> dict[str, Any]:
    song_ids = {row["id"] for row in songs if not row["deleted"]}
    chart_keys = {(row["song_id"], row["difficulty"]) for row in charts}
    pack_ids = {row["id"] for row in packs}
    partner_ids = {row["id"] for row in partners}
    mission_ids = {row["id"] for row in missions}
    story_path_ids = {row["main_id"] for row in paths}
    story_entry_main_ids = {row["main_id"] for row in entries}
    return {
        "duplicates": {
            "song_ids": duplicate_values(row["id"] for row in songs),
            "song_indices": duplicate_values(row["index"] for row in songs),
            "chart_keys": duplicate_values(
                f"{row['song_id']}:{row['difficulty']}" for row in charts
            ),
            "pack_ids": duplicate_values(row["id"] for row in packs),
            "partner_ids": duplicate_values(row["id"] for row in partners),
            "story_path_ids": duplicate_values(row["main_id"] for row in paths),
            "story_entry_keys": duplicate_values(
                f"{row['main_id']}:{row['minor_id']}" for row in entries
            ),
            "mission_ids": duplicate_values(row["id"] for row in missions),
            "unlock_keys": duplicate_values(
                f"{row['song_id']}:{row['difficulty']}" for row in unlocks
            ),
        },
        "missing_references": {
            # "single" is the source format's loose-song pseudo-pack.
            "song_pack_ids": sorted(
                {
                    row["pack_id"]
                    for row in songs
                    if not row["deleted"]
                    and row["pack_id"] not in pack_ids
                    and row["pack_id"] != "single"
                }
            ),
            "pack_parent_ids": sorted(
                {
                    row["parent_id"]
                    for row in packs
                    if row["parent_id"] is not None and row["parent_id"] not in pack_ids
                }
            ),
            "partner_pack_ids": sorted(
                {
                    row["pack_id"]
                    for row in partners
                    if row["pack_id"] is not None and row["pack_id"] not in pack_ids
                }
            ),
            "unlock_song_ids": sorted(
                {row["song_id"] for row in unlocks if row["song_id"] not in song_ids}
            ),
            "unlock_chart_keys": sorted(
                f"{row['song_id']}:{row['difficulty']}"
                for row in unlocks
                if (row["song_id"], row["difficulty"]) not in chart_keys
            ),
            "story_path_without_entries": sorted(story_path_ids - story_entry_main_ids, key=str),
            "story_entries_without_path": sorted(story_entry_main_ids - story_path_ids, key=str),
            "mission_requirement_ids": sorted(
                {
                    requirement["id"]
                    for mission in missions
                    for requirement in mission["requirements"]
                    if requirement.get("type") == "clear_mission_id"
                    and requirement.get("id") not in mission_ids
                }
            ),
        },
        "known_domains": {
            "song_ids": len(song_ids),
            "chart_keys": len(chart_keys),
            "pack_ids": len(pack_ids),
            "partner_ids": len(partner_ids),
        },
    }


def build_catalog(
    apk_path: Path, game_version: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not apk_path.is_file():
        raise ValueError(f"APK does not exist: {apk_path}")
    with zipfile.ZipFile(apk_path) as apk:
        names = set(apk.namelist())
        missing = sorted(set(REQUIRED_ASSETS) - names)
        if missing:
            raise ValueError(f"APK is missing required assets: {missing}")
        story_assets = sorted(name for name in names if STORY_ENTRY_RE.fullmatch(name))
        if not story_assets:
            raise ValueError("APK contains no story entry assets")
        selected_assets = sorted(set(REQUIRED_ASSETS) | set(story_assets))

        source_assets: list[dict[str, Any]] = []
        for name in selected_assets:
            payload = apk.read(name)
            info = apk.getinfo(name)
            source_assets.append(
                {
                    "path": name,
                    "size": len(payload),
                    "compressed_size": info.compress_size,
                    "crc32": f"{info.CRC:08x}",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )

        raw_songs = read_json_asset(apk, ASSET_SONGLIST)["songs"]
        raw_packs = read_json_asset(apk, ASSET_PACKLIST)["packs"]
        raw_unlocks = read_json_asset(apk, ASSET_UNLOCKS)["unlocks"]
        raw_partners = read_json_asset(apk, ASSET_PARTNERS)
        raw_missions = read_json_asset(apk, ASSET_MISSIONS)
        raw_story_paths = read_json_asset(apk, ASSET_STORY_PATHS)["paths"]

        raw_story_entries: list[dict[str, Any]] = []
        story_entries: list[dict[str, Any]] = []
        for name in story_assets:
            match = STORY_ENTRY_RE.fullmatch(name)
            assert match is not None
            route = match.group("route")
            main_id = parse_main_id(match.group("main"))
            rows = read_json_asset(apk, name)["entries"]
            raw_story_entries.extend(rows)
            story_entries.extend(normalize_story_entry(route, main_id, name, row) for row in rows)

        # Only entity display text is retained; no generic game message catalog is shipped.
        partner_translation = gettext.GNUTranslations(io.BytesIO(apk.read(ASSET_ZH_HANS)))
        mo_messages: list[dict[str, Any]] = []

    songs = [normalize_song(row) for row in raw_songs]
    charts = [
        normalize_chart(song, chart) for song in raw_songs for chart in song.get("difficulties", [])
    ]
    packs = [normalize_pack(row) for row in raw_packs]
    partners = [normalize_partner(row) for row in raw_partners]
    localize_partners(partners, partner_translation)
    story_paths = [normalize_story_path(row) for row in raw_story_paths]
    missions: list[dict[str, Any]] = []
    for row in raw_missions:
        mission = {
            "id": row["id"],
            "tier": row["tier"],
            "requirements": copy.deepcopy(row["requirements"]),
            "reward_bundle_ids": copy.deepcopy(row["reward_bundles"]),
            "missions_to_clear_tier": row.get("missionsToClearTier"),
        }
        extra = unknown_fields(
            row,
            {"id", "tier", "requirements", "reward_bundles", "missionsToClearTier"},
        )
        if extra:
            mission["extra"] = extra
        missions.append(mission)

    unlocks: list[dict[str, Any]] = []
    for row in raw_unlocks:
        unlock = {
            "song_id": row["songId"],
            "difficulty": row["ratingClass"],
            "difficulty_name": DIFFICULTY_NAMES.get(
                row["ratingClass"], f"UNKNOWN_{row['ratingClass']}"
            ),
            "conditions": [normalize_condition(cond) for cond in row["conditions"]],
        }
        extra = unknown_fields(row, {"songId", "ratingClass", "conditions"})
        if extra:
            unlock["extra"] = extra
        unlocks.append(unlock)

    songs.sort(key=lambda row: (row["index"], row["id"]))
    charts.sort(key=lambda row: (row["song_id"], row["difficulty"]))
    packs.sort(key=lambda row: row["id"])
    partners.sort(key=lambda row: row["id"])
    story_paths.sort(key=lambda row: str(row["main_id"]))
    story_entries.sort(key=lambda row: (str(row["main_id"]), row["minor_id"]))
    missions.sort(key=lambda row: row["id"])
    unlocks.sort(key=lambda row: (row["song_id"], row["difficulty"]))

    raw_conditions = list(
        walk_conditions(cond for unlock in raw_unlocks for cond in unlock["conditions"])
    )
    raw_condition_rows = [row for row, _depth in raw_conditions]
    direct_condition_count = sum(len(row["conditions"]) for row in raw_unlocks)
    condition_types: collections.Counter[int] = collections.Counter(
        int(row["type"]) for row in raw_condition_rows if isinstance(row.get("type"), int)
    )

    schemas: dict[str, Any] = {
        "song": record_schema(raw_songs),
        "chart": record_schema(
            chart for song in raw_songs for chart in song.get("difficulties", [])
        ),
        "pack": record_schema(raw_packs),
        "partner": record_schema(raw_partners),
        "story_path": record_schema(raw_story_paths),
        "story_entry": record_schema(raw_story_entries),
        "mission": record_schema(raw_missions),
        "mission_requirement": record_schema(
            requirement for mission in raw_missions for requirement in mission["requirements"]
        ),
        "unlock": record_schema(raw_unlocks),
        "unlock_condition": record_schema(raw_condition_rows),
        "unlock_condition_by_type": {
            str(condition_type): record_schema(
                row for row in raw_condition_rows if row.get("type") == condition_type
            )
            for condition_type in sorted(condition_types)
        },
        "zh_hans_message": record_schema(mo_messages),
    }
    schemas["fingerprint_sha256"] = content_hash(schemas)

    integrity = build_integrity(
        songs,
        charts,
        packs,
        partners,
        story_paths,
        story_entries,
        missions,
        unlocks,
    )
    counts = {
        "songs_total": len(songs),
        "songs_live": sum(not row["deleted"] for row in songs),
        "songs_deleted": sum(row["deleted"] for row in songs),
        "charts": len(charts),
        "charts_by_difficulty": dict(
            sorted(collections.Counter(str(row["difficulty"]) for row in charts).items())
        ),
        "packs": len(packs),
        "partners": len(partners),
        "story_paths": len(story_paths),
        "story_entries": len(story_entries),
        "missions": len(missions),
        "unlocks": len(unlocks),
        "unlock_conditions_direct": direct_condition_count,
        "unlock_conditions_recursive": len(raw_condition_rows),
        "unlock_condition_max_depth": max(depth for _row, depth in raw_conditions),
        "unlock_condition_types_recursive": {
            str(key): value for key, value in sorted(condition_types.items())
        },
        "zh_hans_messages": len(mo_messages),
    }

    apk_stat = apk_path.stat()
    apk_source = {
        "filename": apk_path.name,
        "size": apk_stat.st_size,
        "sha256": sha256_file(apk_path),
    }
    catalog = {
        "format": CATALOG_FORMAT,
        "game_version": game_version,
        "source_apk": apk_source,
        "source_assets": source_assets,
        "counts": counts,
        "integrity": integrity,
        "entities": {
            "songs": songs,
            "charts": charts,
            "packs": packs,
            "partners": partners,
            "story_paths": story_paths,
            "story_entries": story_entries,
            "missions": missions,
            "unlocks": unlocks,
        },
        "localization": {},
        "source_schema_fingerprint_sha256": schemas["fingerprint_sha256"],
    }
    manifest = {
        "format": "arcsavelab.catalog-manifest/v1",
        "game_version": game_version,
        "source_apk": apk_source,
        "source_assets": source_assets,
        "outputs": {
            "catalog.json": {"sha256": content_hash(catalog)},
            "schema.json": {"sha256": content_hash(schemas)},
        },
    }
    return catalog, manifest, schemas


def entity_index(
    rows: list[dict[str, Any]], key: Callable[[dict[str, Any]], str]
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        item_key = key(row)
        if item_key in result:
            raise ValueError(f"duplicate normalized entity key: {item_key}")
        result[item_key] = row
    return result


ENTITY_KEYS: dict[str, Callable[[dict[str, Any]], str]] = {
    "songs": lambda row: str(row["id"]),
    "charts": lambda row: f"{row['song_id']}:{row['difficulty']}",
    "packs": lambda row: str(row["id"]),
    "partners": lambda row: str(row["id"]),
    "story_paths": lambda row: str(row["main_id"]),
    "story_entries": lambda row: f"{row['main_id']}:{row['minor_id']}",
    "missions": lambda row: str(row["id"]),
    "unlocks": lambda row: f"{row['song_id']}:{row['difficulty']}",
}


def compare_catalogs(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    if old.get("format") != CATALOG_FORMAT or new.get("format") != CATALOG_FORMAT:
        raise ValueError("both inputs must be arcsavelab.catalog/v1 catalogs")
    entity_diff: dict[str, Any] = {}
    for name, key in ENTITY_KEYS.items():
        old_index = entity_index(old["entities"][name], key)
        new_index = entity_index(new["entities"][name], key)
        old_keys = set(old_index)
        new_keys = set(new_index)
        entity_diff[name] = {
            "old_count": len(old_index),
            "new_count": len(new_index),
            "added": sorted(new_keys - old_keys),
            "removed": sorted(old_keys - new_keys),
            "changed": sorted(
                item_key
                for item_key in old_keys & new_keys
                if canonical_bytes(old_index[item_key]) != canonical_bytes(new_index[item_key])
            ),
        }

    def mo_index(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
        rows = cast(
            list[dict[str, Any]],
            catalog.get("localization", {}).get("zh-Hans", {}).get("messages", []),
        )
        return entity_index(
            rows,
            lambda row: f"{row['msgid']}\u0000{row.get('plural_index', -1)}",
        )

    old_mo, new_mo = mo_index(old), mo_index(new)
    old_mo_keys, new_mo_keys = set(old_mo), set(new_mo)
    entity_diff["zh_hans_messages"] = {
        "old_count": len(old_mo),
        "new_count": len(new_mo),
        "added": sorted(new_mo_keys - old_mo_keys),
        "removed": sorted(old_mo_keys - new_mo_keys),
        "changed": sorted(
            key
            for key in old_mo_keys & new_mo_keys
            if canonical_bytes(old_mo[key]) != canonical_bytes(new_mo[key])
        ),
    }

    old_assets = {row["path"]: row for row in old["source_assets"]}
    new_assets = {row["path"]: row for row in new["source_assets"]}
    old_asset_keys, new_asset_keys = set(old_assets), set(new_assets)
    asset_diff = {
        "added": sorted(new_asset_keys - old_asset_keys),
        "removed": sorted(old_asset_keys - new_asset_keys),
        "changed": sorted(
            key
            for key in old_asset_keys & new_asset_keys
            if old_assets[key]["sha256"] != new_assets[key]["sha256"]
        ),
        "unchanged_count": sum(
            old_assets[key]["sha256"] == new_assets[key]["sha256"]
            for key in old_asset_keys & new_asset_keys
        ),
    }
    schema_changed = (
        old["source_schema_fingerprint_sha256"] != new["source_schema_fingerprint_sha256"]
    )
    return {
        "format": DIFF_FORMAT,
        "old_version": old["game_version"],
        "new_version": new["game_version"],
        "source_assets": asset_diff,
        "schema": {
            "old_fingerprint_sha256": old["source_schema_fingerprint_sha256"],
            "new_fingerprint_sha256": new["source_schema_fingerprint_sha256"],
            "changed": schema_changed,
        },
        "entities": entity_diff,
        "has_catalog_changes": schema_changed
        or bool(asset_diff["added"] or asset_diff["removed"] or asset_diff["changed"])
        or any(
            value["added"] or value["removed"] or value["changed"] for value in entity_diff.values()
        ),
    }


def command_build(args: argparse.Namespace) -> int:
    catalog, manifest, schemas = build_catalog(args.apk.resolve(), args.version)
    out = args.out.resolve()
    write_json(out / "catalog.json", catalog)
    write_json(out / "schema.json", schemas)
    # Recompute byte hashes after writing so the manifest verifies exact files,
    # not merely their canonical compact representation.
    manifest["outputs"]["catalog.json"]["file_sha256"] = sha256_file(out / "catalog.json")
    manifest["outputs"]["schema.json"]["file_sha256"] = sha256_file(out / "schema.json")
    write_json(out / "manifest.json", manifest)
    print(
        json.dumps(
            {
                "output": str(out),
                "game_version": args.version,
                "counts": catalog["counts"],
                "integrity": catalog["integrity"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def command_compare(args: argparse.Namespace) -> int:
    old = json.loads(args.old.read_text(encoding="utf-8"))
    new = json.loads(args.new.read_text(encoding="utf-8"))
    result = compare_catalogs(old, new)
    write_json(args.out.resolve(), result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="build one catalog from an APK")
    build.add_argument("--apk", type=Path, required=True, help="input APK")
    build.add_argument("--version", required=True, help="exact game version label")
    build.add_argument("--out", type=Path, required=True, help="output directory")
    build.set_defaults(function=command_build)

    compare = subparsers.add_parser("compare", help="compare two catalog.json files")
    compare.add_argument("--old", type=Path, required=True, help="old catalog.json")
    compare.add_argument("--new", type=Path, required=True, help="new catalog.json")
    compare.add_argument("--out", type=Path, required=True, help="output diff JSON")
    compare.set_defaults(function=command_compare)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        function = cast(Callable[[argparse.Namespace], int], args.function)
        return function(args)
    except (ValueError, OSError, zipfile.BadZipFile, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
