from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from arcsavelab.catalog import (
    CatalogIntegrityError,
    CatalogNotFoundError,
    list_catalog_versions,
    load_catalog,
    load_catalog_from_directory,
    verify_catalog,
)
from arcsavelab.domain import Difficulty, UnlockRequirementKind


def test_packaged_current_catalog_is_verified_and_loadable() -> None:
    assert "7.0.255c" in list_catalog_versions()

    verification = verify_catalog("7.0.255c")
    catalog = load_catalog("7.0.255c")

    assert verification.valid
    assert verification.version == "7.0.255c"
    assert verification.verified_files == ("catalog.json", "schema.json")
    assert verification.source_asset_count == 36
    assert catalog.version.value == "7.0.255c"
    assert catalog.summary.song_count == 553
    assert catalog.summary.live_song_count == 552
    assert catalog.summary.chart_count == 1833
    assert catalog.summary.pack_count == 62
    assert catalog.summary.partner_count == 100
    assert catalog.summary.story_entry_count == 196
    assert catalog.summary.mission_count == 28
    assert catalog.summary.song_unlock_count == 471
    assert catalog.summary.simplified_chinese_message_count == 0


def test_catalog_returns_domain_entities_and_relations() -> None:
    catalog = load_catalog("7.0.255c")

    song = catalog.song("sayonarahatsukoi")
    eternal = catalog.chart(song.id, Difficulty.ETERNAL)
    unlock = catalog.song_unlock("lostcivilization", Difficulty.PRESENT)

    assert song.title.resolve("en") == "Sayonara Hatsukoi"
    assert eternal.display_rating == "8"
    assert catalog.charts_for_song(song.id)[-1].difficulty is Difficulty.ETERNAL
    assert unlock.requirements[0].kind is UnlockRequirementKind.CLEAR_SONG
    assert catalog.translate("Hikari", locale="zh-Hans") == "Hikari"

    assert not hasattr(song, "idx")
    assert not hasattr(eternal, "ratingClass")
    assert not hasattr(unlock, "songId")


def test_catalog_rejects_unknown_packaged_version() -> None:
    with pytest.raises(CatalogNotFoundError):
        load_catalog("9.9.9c")


@pytest.mark.parametrize("filename", ["catalog.json", "schema.json"])
def test_directory_loader_detects_hash_tampering(tmp_path: Path, filename: str) -> None:
    source = Path(__file__).parents[1] / "src" / "arcsavelab" / "resources" / "catalog" / "7.0.255c"
    target = tmp_path / "catalog"
    shutil.copytree(source, target)
    with (target / filename).open("ab") as stream:
        stream.write(b"\n")

    with pytest.raises(CatalogIntegrityError, match=filename):
        load_catalog_from_directory(target, expected_version="7.0.255c")


def test_directory_loader_detects_schema_fingerprint_mismatch(tmp_path: Path) -> None:
    source = Path(__file__).parents[1] / "src" / "arcsavelab" / "resources" / "catalog" / "7.0.255c"
    target = tmp_path / "catalog"
    shutil.copytree(source, target)

    catalog_path = target / "catalog.json"
    manifest_path = target / "manifest.json"
    catalog_data = json.loads(catalog_path.read_text(encoding="utf-8"))
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    catalog_data["source_schema_fingerprint_sha256"] = "0" * 64
    catalog_path.write_text(
        json.dumps(catalog_data, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    # Update only the file hash so verification reaches the independent schema
    # fingerprint check instead of stopping at the first integrity layer.
    import hashlib

    manifest_data["outputs"]["catalog.json"]["file_sha256"] = hashlib.sha256(
        catalog_path.read_bytes()
    ).hexdigest()
    manifest_data["outputs"]["catalog.json"]["sha256"] = hashlib.sha256(
        json.dumps(
            catalog_data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest_data, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(CatalogIntegrityError, match="schema fingerprint"):
        load_catalog_from_directory(target, expected_version="7.0.255c")
