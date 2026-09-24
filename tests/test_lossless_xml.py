from __future__ import annotations

from arcsavelab.formats.plist_map import PlistMapDocument
from arcsavelab.formats.prefs_xml import SharedPrefsDocument

PREFS = (
    b'\xef\xbb\xbf<?xml version="1.0" encoding="utf-8"?>\r\n'
    b"<!-- keep-before-root -->\r\n"
    b'<map vendor="opaque">\r\n'
    b"  <string name='dup'>first</string>\r\n"
    b'  <mystery name="opaque" x="1"><child /></mystery>\r\n'
    b'  <int name="n" value=\'0042\' extra="keep" />\r\n'
    b"  <string name='dup'>last &amp; value</string>\r\n"
    b"</map>\r\n"
)


PLIST = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<!DOCTYPE plist [<!ENTITY untouched "opaque">]>\n'
    b'<plist version="1.0">\n'
    b"<dict>\n"
    b"  <!-- duplicate is intentionally last-wins -->\n"
    b"  <key>dup</key><string>first</string>\n"
    b"  <key>opaque</key><date>2026-01-02T03:04:05Z</date>\n"
    b"  <key>dup</key><integer>7</integer>\n"
    b"</dict>\n"
    b"</plist>\n"
)


def test_shared_prefs_noop_is_byte_exact_and_duplicate_is_last_wins() -> None:
    document = SharedPrefsDocument.from_bytes(PREFS)
    assert document.to_bytes() == PREFS
    assert document.get("dup") == "last & value"
    assert document.get("n") == 42
    assert document.kind("n") == "int"

    document.set("dup", "last & value")
    document.set("n", 42)
    assert document.to_bytes() == PREFS


def test_shared_prefs_changes_only_effective_value_token_and_preserves_type() -> None:
    document = SharedPrefsDocument.from_bytes(PREFS)
    document.set("dup", "changed <&>")
    expected = PREFS.replace(
        b"<string name='dup'>last &amp; value</string>",
        b"<string name='dup'>changed &lt;&amp;&gt;</string>",
    )
    assert document.to_bytes() == expected
    assert b"<string name='dup'>first</string>" in document.to_bytes()
    assert b'<mystery name="opaque" x="1"><child /></mystery>' in document.to_bytes()

    document.set("n", 43)
    assert b'<int name="n" value=\'43\' extra="keep" />' in document.to_bytes()
    assert document.kind("n") == "int"


def test_shared_prefs_insert_and_delete_keep_unknown_markup() -> None:
    document = SharedPrefsDocument.from_bytes(PREFS)
    document.set("new", "x&y")
    assert b'<string name="new">x&amp;y</string>' in document.to_bytes()
    assert b"<!-- keep-before-root -->" in document.to_bytes()
    assert b'vendor="opaque"' in document.to_bytes()

    assert document.delete("dup") is True
    assert "dup" not in document
    assert b"name='dup'" not in document.to_bytes()
    assert b'<mystery name="opaque" x="1"><child /></mystery>' in document.to_bytes()


def test_plist_noop_is_byte_exact_and_duplicate_is_last_wins() -> None:
    document = PlistMapDocument.from_bytes(PLIST)
    assert document.to_bytes() == PLIST
    assert document.get("dup") == 7
    assert document.kind("dup") == "integer"
    document.set("dup", 7)
    assert document.to_bytes() == PLIST


def test_plist_minimal_set_delete_and_unknown_type_preservation() -> None:
    document = PlistMapDocument.from_bytes(PLIST)
    document.set("dup", 8)
    expected = PLIST.replace(b"<integer>7</integer>", b"<integer>8</integer>")
    assert document.to_bytes() == expected
    assert b"<string>first</string>" in document.to_bytes()
    assert b"<date>2026-01-02T03:04:05Z</date>" in document.to_bytes()
    assert b'<!DOCTYPE plist [<!ENTITY untouched "opaque">]>' in document.to_bytes()

    document.set("new", True)
    assert b"<key>new</key>" in document.to_bytes()
    assert b"<true/>" in document.to_bytes()
    assert document.delete("dup") is True
    assert "dup" not in document
    assert document.to_bytes().count(b"<key>dup</key>") == 0
    assert b"<date>2026-01-02T03:04:05Z</date>" in document.to_bytes()


def test_utf16_noop_remains_byte_exact() -> None:
    text = '<?xml version="1.0" encoding="UTF-16"?><map><string name="x">值</string></map>'
    raw = b"\xff\xfe" + text.encode("utf-16-le")
    document = SharedPrefsDocument.from_bytes(raw)
    assert document.get("x") == "值"
    document.set("x", "值")
    assert document.to_bytes() == raw
