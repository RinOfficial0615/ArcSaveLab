from __future__ import annotations

import copy
from typing import Any

import pytest

from arcsavelab.catalog_builder import CATALOG_FORMAT, ENTITY_KEYS, compare_catalogs


def _catalog() -> dict[str, Any]:
    return {
        "format": CATALOG_FORMAT,
        "game_version": "synthetic",
        "entities": {name: [] for name in ENTITY_KEYS},
        "source_assets": [],
        "source_schema_fingerprint_sha256": "0" * 64,
        "localization": {},
    }


@pytest.mark.parametrize("localization", [{}, {"zh-Hans": {}}, {"zh-Hans": {"messages": []}}])
def test_compare_catalogs_without_game_messages(localization: dict[str, Any]) -> None:
    old = _catalog()
    old["localization"] = localization
    result = compare_catalogs(old, _catalog())
    assert not result["has_catalog_changes"]
    assert result["entities"]["zh_hans_messages"]["old_count"] == 0


def test_compare_catalogs_can_remove_legacy_messages() -> None:
    old = _catalog()
    old["localization"] = {"zh-Hans": {"messages": [{"msgid": "test", "msgstr": "old"}]}}
    new = copy.deepcopy(old)
    new["localization"] = {}
    result = compare_catalogs(old, new)
    assert result["has_catalog_changes"]
    assert result["entities"]["zh_hans_messages"]["removed"] == ["test\u0000-1"]
