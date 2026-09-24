from __future__ import annotations

import hashlib
import json
from pathlib import Path

from arcsavelab.adapters.verify_cli import main as verify_main
from arcsavelab.formats.prefs_xml import SharedPrefsDocument
from arcsavelab.verification import (
    VerificationStatus,
    apply_computed_integrity,
    verify_userdefaults,
)


def _md5(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def _write_save(directory: Path) -> tuple[Path, Path, Path]:
    un = directory / "un"
    ms = directory / "ms"
    un.write_bytes(b"<plist><dict><key>x</key><integer>1</integer></dict></plist>")
    ms.write_bytes(b"<plist><dict><key>m</key><string>v</string></dict></plist>")

    prefs = directory / "Cocos2dxPrefsFile.xml"
    prefs.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<map>
  <int name="lu" value="123" />
  <string name="st_v">abc</string>
  <string name="st_k">{st}</string>
  <string name="cs_v">clear-state</string>
  <string name="fs_v"></string>
  <string name="fs_k"></string>
  <string name="p_v">payload</string>
  <string name="p_k">{p}</string>
  <int name="fr_v" value="0" />
  <string name="fr_k">{fr}</string>
  <int name="casa_k" value="5" />
  <string name="casa_h">{casa}</string>
  <string name="un_k">{un}</string>
  <string name="ms_k">definitely-wrong</string>
  <string name="unknown_k">opaque</string>
</map>
""".format(
            st=_md5("abc"),
            p=_md5("device" + "123" + "payload" + "246"),
            fr=_md5("0ok0"),
            casa=_md5("5ok5"),
            un=_md5("x&1$"),
        ),
        encoding="utf-8",
        newline="",
    )
    return prefs, un, ms


def test_verify_paths_auto_resolve_maps_and_report_all_statuses(tmp_path: Path) -> None:
    prefs, _, _ = _write_save(tmp_path)
    report = verify_userdefaults(prefs, device_id="device")

    assert report.user_id == 123
    assert report.for_key("st_k").status is VerificationStatus.MATCH  # type: ignore[union-attr]
    assert report.for_key("p_k").status is VerificationStatus.MATCH  # type: ignore[union-attr]
    assert report.for_key("un_k").status is VerificationStatus.MATCH  # type: ignore[union-attr]
    assert report.for_key("ms_k").status is VerificationStatus.MISMATCH  # type: ignore[union-attr]
    assert report.for_key("cs_k").status is VerificationStatus.MISSING  # type: ignore[union-attr]
    assert report.for_key("casa_h").status is VerificationStatus.MATCH  # type: ignore[union-attr]
    assert report.for_key("unknown_k").status is VerificationStatus.SKIP  # type: ignore[union-attr]
    assert report.ok is False
    assert report.strict_ok is False
    assert report.exit_code() == 1


def test_apply_computed_integrity_repairs_document_without_writing(tmp_path: Path) -> None:
    prefs_path, un_path, ms_path = _write_save(tmp_path)
    document = SharedPrefsDocument.from_path(prefs_path)
    before_disk = prefs_path.read_bytes()
    report = verify_userdefaults(document, un=un_path, ms=ms_path, device_id="device")

    updated = apply_computed_integrity(document, report)
    assert set(updated) == {"ms_k", "cs_k"}
    assert prefs_path.read_bytes() == before_disk

    repaired = verify_userdefaults(document, un=un_path, ms=ms_path, device_id="device")
    assert repaired.ok is True
    assert repaired.strict_ok is False  # unknown_k remains explicitly visible
    assert repaired.for_key("ms_k").status is VerificationStatus.MATCH  # type: ignore[union-attr]
    assert repaired.for_key("cs_k").status is VerificationStatus.MATCH  # type: ignore[union-attr]


def test_empty_device_bound_value_verifies_without_identity() -> None:
    document = SharedPrefsDocument.from_bytes(
        b'<map><string name="p_v"></string><string name="p_k"></string></map>'
    )
    report = verify_userdefaults(document)
    result = report.for_key("p_k")
    assert result is not None
    assert result.status is VerificationStatus.MATCH
    assert result.computed == ""


def test_verify_cli_json_contract_and_exit_codes(tmp_path: Path, capsys: object) -> None:
    prefs, un, ms = _write_save(tmp_path)
    exit_code = verify_main(
        [
            str(prefs),
            "--un",
            str(un),
            "--ms",
            str(ms),
            "--device-id",
            "device",
            "--format",
            "json",
        ]
    )
    assert exit_code == 1
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = json.loads(captured.out)
    assert payload["schema_version"] == 1
    assert payload["resolved_user_id"] == 123
    assert payload["sources"]["preferences"] == str(prefs)
    assert payload["ok"] is False
    assert payload["summary"]["mismatch"] == 1
    assert payload["summary"]["unresolved"] == 1
    st_result = next(item for item in payload["results"] if item["key"] == "st_k")
    assert st_result["expected"] == _md5("abc")
    assert st_result["status"] == "match"
    assert st_result["reason_code"] is None
    ms_result = next(item for item in payload["results"] if item["key"] == "ms_k")
    assert ms_result["status"] == "mismatch"
    assert ms_result["reason_code"] == "digest_mismatch"
    unknown_result = next(item for item in payload["results"] if item["key"] == "unknown_k")
    assert unknown_result["status"] == "unresolved"
    assert unknown_result["reason_code"] == "algorithm_unknown"

    assert verify_main([str(tmp_path / "missing.xml")]) == 2
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert captured.err.startswith("error:")


def test_json_ok_reflects_selected_strictness() -> None:
    document = SharedPrefsDocument.from_bytes(
        b'<map><string name="p_v">owned</string><string name="p_k">opaque</string></map>'
    )
    report = verify_userdefaults(document)

    assert report.to_dict(strict=False)["ok"] is True
    assert report.to_dict(strict=True)["ok"] is False
    assert report.summary["unresolved"] == 1


def test_result_order_does_not_depend_on_xml_element_order(tmp_path: Path) -> None:
    prefs, un, ms = _write_save(tmp_path)
    first = verify_userdefaults(prefs, un=un, ms=ms, device_id="device")

    lines = prefs.read_text(encoding="utf-8").splitlines()
    prefs.write_text(
        "\n".join(lines[:2] + list(reversed(lines[2:-1])) + lines[-1:]) + "\n",
        encoding="utf-8",
        newline="",
    )
    second = verify_userdefaults(prefs, un=un, ms=ms, device_id="device")

    assert [item.key for item in first.results] == [item.key for item in second.results]
