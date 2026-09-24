from __future__ import annotations

import json

import pytest

from arcsavelab.adapters.cli import classify_source, main
from arcsavelab.interface import SaveKind


def test_cli_classifies_all_four_synthetic_formats(synthetic_saves: object) -> None:
    assert classify_source(synthetic_saves.preferences) is SaveKind.PREFERENCES  # type: ignore[attr-defined]
    assert classify_source(synthetic_saves.unlocks) is SaveKind.UNLOCK_PROGRESS  # type: ignore[attr-defined]
    assert classify_source(synthetic_saves.missions) is SaveKind.MISSION_PROGRESS  # type: ignore[attr-defined]
    assert classify_source(synthetic_saves.scores) is SaveKind.SCORE_DATABASE  # type: ignore[attr-defined]


def test_default_cli_path_launches_tui_with_selected_sources(
    synthetic_saves: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[object] = []

    def fake_tui(request: object) -> int:
        requests.append(request)
        return 23

    monkeypatch.setattr("arcsavelab.adapters.tui.run_tui", fake_tui)
    assert main([]) == 23
    assert requests[-1].sources == ()  # type: ignore[attr-defined]

    assert (
        main(
            [
                "--preferences",
                str(synthetic_saves.preferences),  # type: ignore[attr-defined]
                "--unlocks",
                str(synthetic_saves.unlocks),  # type: ignore[attr-defined]
                "--language",
                "zh-Hans",
            ]
        )
        == 23
    )
    request = requests[-1]
    assert request.locale == "zh-Hans"  # type: ignore[attr-defined]
    assert {source.kind for source in request.sources} == {  # type: ignore[attr-defined]
        SaveKind.PREFERENCES,
        SaveKind.UNLOCK_PROGRESS,
    }


def test_explicit_edit_cli_identifies_positional_files(
    synthetic_saves: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[object] = []
    monkeypatch.setattr(
        "arcsavelab.adapters.tui.run_tui",
        lambda request: requests.append(request) or 0,
    )
    assert (
        main(
            [
                "edit",
                str(synthetic_saves.preferences),  # type: ignore[attr-defined]
                str(synthetic_saves.scores),  # type: ignore[attr-defined]
            ]
        )
        == 0
    )
    assert {source.kind for source in requests[0].sources} == {  # type: ignore[attr-defined]
        SaveKind.PREFERENCES,
        SaveKind.SCORE_DATABASE,
    }


def test_verify_cli_emits_schema_v1_through_root_dispatch(
    synthetic_saves: object, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [
            "verify",
            str(synthetic_saves.preferences),  # type: ignore[attr-defined]
            "--un",
            str(synthetic_saves.unlocks),  # type: ignore[attr-defined]
            "--ms",
            str(synthetic_saves.missions),  # type: ignore[attr-defined]
            "--device-id",
            "synthetic-device",
            "--user-id",
            "123",
            "--format",
            "json",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    assert payload["ok"] is True
    assert payload["summary"] == {
        "total": 13,
        "match": 13,
        "mismatch": 0,
        "missing": 0,
        "unresolved": 0,
    }


def test_catalog_cli_info_and_verify(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["catalog", "info", "--version", "7.0.255c"]) == 0
    info = capsys.readouterr().out
    assert "ArcSaveLab catalog 7.0.255c" in info
    assert "songs: 553" in info
    assert "partners: 100" in info

    assert main(["catalog", "verify", "--version", "7.0.255c"]) == 0
    verified = capsys.readouterr().out
    assert "valid=True" in verified
    assert "source_asset_count=36" in verified
