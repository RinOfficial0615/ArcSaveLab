"""Arcaea save-integrity algorithms.

The implementations mirror the four native algorithm families used by current
Arcaea saves.  Every digest input is UTF-8 and every digest is lowercase MD5.
"""

from __future__ import annotations

import hashlib
import os
from typing import Final

from .formats.plist_map import PlistMapDocument, parse_plist_map

DIRECT_HASH_SOURCES: Final[dict[str, str]] = {
    "st_k": "st_v",
    "cs_k": "cs_v",
    "fin_k": "fin_v",
    "fc_k": "fc_v",
    "fs_k": "fs_v",
}

DEVICE_HASH_SOURCES: Final[dict[str, str]] = {
    "p_k": "p_v",
    "s_k": "s_v",
    "wu_k": "wu_v",
    "ac_k": "ac_v",
}

INT_HASH_SOURCES: Final[dict[str, str]] = {
    "fr_k": "fr_v",
    "ca_wacca_luin_h": "ca_wacca_luin_k",
    "ca_wacca_luin_awakened_h": "ca_wacca_luin_awakened_k",
    "ca_wacca_luin_awakened_lifebar_h": "ca_wacca_luin_awakened_lifebar_k",
    "ca_wacca_lily_h": "ca_wacca_lily_k",
    "ca_wacca_elizabeth_h": "ca_wacca_elizabeth_k",
    "ca_nonoka_rank_h": "ca_nonoka_rank_k",
    "casa_h": "casa_k",
}

MAP_HASH_FILES: Final[dict[str, str]] = {
    "un_k": "un",
    "ms_k": "ms",
}


def md5_text(value: str) -> str:
    """Return lowercase MD5 for a UTF-8 string."""

    return hashlib.md5(value.encode("utf-8")).hexdigest()


def int32(value: int) -> int:
    """Apply the native signed 32-bit wrap used by account and integer hashes."""

    wrapped = int(value) & 0xFFFFFFFF
    return wrapped - 0x100000000 if wrapped & 0x80000000 else wrapped


def direct_value_hash(value: str) -> str:
    """Hash a serialized ``*_v`` while retaining the empty-string sentinel."""

    if not isinstance(value, str):
        raise TypeError("direct hash source must be str")
    return "" if value == "" else md5_text(value)


def validity_hash_for_int(value: int) -> str:
    """Replicate native ``validityHashForInt(int32_t)``."""

    normalized = int32(value)
    text = str(normalized)
    return md5_text(text + "ok" + text)


def device_bound_value_hash(value: str, device_id: str, user_id: int) -> str:
    """Hash p/s/wu/ac data using the Java-provided device ID and account ID."""

    if not isinstance(value, str):
        raise TypeError("device-bound hash source must be str")
    if not isinstance(device_id, str):
        raise TypeError("device_id must be str")
    if value == "":
        return ""
    normalized_user = int32(user_id)
    doubled_user = int32(normalized_user * 2)
    return md5_text(device_id + str(normalized_user) + value + str(doubled_user))


PlistSource = str | os.PathLike[str] | bytes | bytearray | memoryview | PlistMapDocument


def serialize_value_map(source: PlistSource) -> str:
    """Return native ``hashForValueMap``'s exact preimage.

    Duplicate plist keys are resolved last-wins before sorting, matching the
    dictionary constructed by Cocos.  Sorting compares raw UTF-8 key bytes in
    descending order.
    """

    document = parse_plist_map(source)
    return document.serialize_value_map()


def value_map_hash(source: PlistSource) -> str:
    """Generate the digest stored as ``un_k`` or ``ms_k``."""

    return md5_text(serialize_value_map(source))


# Native-name aliases make reverse-engineering notes and Python call sites line
# up without preserving the retired command-line wrapper.
hash_for_value_map = value_map_hash
validity_hash_for_value_map = value_map_hash


__all__ = [
    "DEVICE_HASH_SOURCES",
    "DIRECT_HASH_SOURCES",
    "INT_HASH_SOURCES",
    "MAP_HASH_FILES",
    "PlistSource",
    "device_bound_value_hash",
    "direct_value_hash",
    "hash_for_value_map",
    "int32",
    "md5_text",
    "serialize_value_map",
    "validity_hash_for_int",
    "validity_hash_for_value_map",
    "value_map_hash",
]
