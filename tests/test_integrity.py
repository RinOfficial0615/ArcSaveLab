from __future__ import annotations

from arcsavelab.formats.plist_map import PlistMapDocument
from arcsavelab.integrity import (
    device_bound_value_hash,
    direct_value_hash,
    int32,
    serialize_value_map,
    validity_hash_for_int,
    value_map_hash,
)


def test_direct_hash_and_empty_sentinel() -> None:
    assert direct_value_hash("") == ""
    assert direct_value_hash("abc") == "900150983cd24fb0d6963f7d28e17f72"


def test_validity_hash_normalizes_to_signed_int32() -> None:
    assert validity_hash_for_int(0) == "2fd8ce3eba4c37c73574be998a952a91"
    assert validity_hash_for_int(0xFFFFFFFF) == "34fed4d174c94b5b61482dc56dd261f3"
    assert validity_hash_for_int(-1) == validity_hash_for_int(0xFFFFFFFF)
    assert int32(0x80000000) == -2147483648


def test_device_hash_uses_wrapped_user_and_double() -> None:
    assert device_bound_value_hash("a|b", "device", 123) == "7aba9df70d5bae14b5a4453ff974a2cc"
    assert (
        device_bound_value_hash("a|b", "device", 2147483647) == "bd93f022af40c37ded8d5a9147da5f19"
    )
    assert device_bound_value_hash("", "ignored", 123) == ""


def test_value_map_hash_is_utf8_descending_and_duplicate_last_wins() -> None:
    raw = b"""<?xml version='1.0' encoding='UTF-8'?>
<!DOCTYPE plist PUBLIC '-//Apple//DTD PLIST 1.0//EN' 'http://www.apple.com/DTDs/PropertyList-1.0.dtd'>
<plist version='1.0'><dict>
  <key>a</key><string>first</string>
  <key>z</key><real>2.5</real>
  <key>\xc3\xa9</key><true/>
  <key>a</key><string>last</string>
</dict></plist>
"""
    document = PlistMapDocument.from_bytes(raw)
    assert serialize_value_map(document) == "\xe9&true$z&2.5$a&last$"
    assert value_map_hash(document) == "504167ffb027e891381611e115fabfcc"
