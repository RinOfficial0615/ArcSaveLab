from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from arcsavelab.formats.st3_sqlite import (
    ChartKey,
    St3Document,
    calculate_score,
    grade_for_score,
)


@pytest.mark.parametrize(
    ("pure", "far", "lost", "shiny", "expected"),
    [
        (0, 0, 0, 0, 0),
        (100, 0, 0, 10, 10_000_010),
        (90, 8, 2, 5, 9_400_005),
        (0, 1, 1, 0, 2_500_000),
        (1, 1, 1, 0, 5_000_000),
    ],
)
def test_score_formula_uses_integer_flooring(
    pure: int, far: int, lost: int, shiny: int, expected: int
) -> None:
    assert calculate_score(pure, far, lost, shiny) == expected


@pytest.mark.parametrize(
    ("score", "grade"),
    [
        (0, 0),
        (8_599_999, 0),
        (8_600_000, 1),
        (8_899_999, 1),
        (8_900_000, 2),
        (9_199_999, 2),
        (9_200_000, 3),
        (9_499_999, 3),
        (9_500_000, 4),
        (9_799_999, 4),
        (9_800_000, 5),
        (9_899_999, 5),
        (9_900_000, 6),
        (10_000_000, 6),
    ],
)
def test_grade_boundaries_match_native(score: int, grade: int) -> None:
    assert grade_for_score(score) == grade


def test_st3_noop_is_exact_and_control_type_is_part_of_key(
    synthetic_saves: object,
) -> None:
    path = synthetic_saves.scores  # type: ignore[attr-defined]
    original = path.read_bytes()
    document = St3Document.from_path(path)

    standard = document.get_score(ChartKey("sayonarahatsukoi", 2, 0))
    controller = document.get_score(ChartKey("sayonarahatsukoi", 2, 1))
    assert standard is not None and controller is not None
    assert standard.row_id != controller.row_id
    assert document.schema_version == 4
    assert document.render_bytes() == original
    assert not document.dirty


def test_st3_edit_delete_and_reopen_preserve_other_control_type(
    synthetic_saves: object, tmp_path: Path
) -> None:
    document = St3Document.from_path(synthetic_saves.scores)  # type: ignore[attr-defined]
    standard_key = ChartKey("sayonarahatsukoi", 2, 0)
    controller_key = ChartKey("sayonarahatsukoi", 2, 1)
    standard = document.get_score(standard_key)
    assert standard is not None
    updated = replace(
        standard,
        near_count=20,
        score=calculate_score(
            standard.perfect_count,
            20,
            standard.miss_count,
            standard.shiny_perfect_count,
        ),
    )
    document.set_score(updated)
    document.set_clear_type(standard_key, 5)
    document.delete(controller_key)

    rendered = document.render_bytes()
    target = tmp_path / "rendered-st3"
    target.write_bytes(rendered)
    reopened = St3Document.from_path(target)
    assert reopened.get_score(standard_key) == updated
    clear = reopened.get_clear(standard_key)
    assert clear is not None and clear.clear_type == 5
    assert reopened.get_score(controller_key) is None
    assert reopened.get_clear(controller_key) is None


def test_st3_rejects_inconsistent_records_and_clear_types(
    synthetic_saves: object,
) -> None:
    document = St3Document.from_path(synthetic_saves.scores)  # type: ignore[attr-defined]
    key = ChartKey("sayonarahatsukoi", 2, 0)
    record = document.get_score(key)
    assert record is not None

    with pytest.raises(ValueError, match="score must equal"):
        document.set_score(replace(record, score=record.score + 1))
    with pytest.raises(ValueError, match="shiny PURE"):
        document.set_score(
            replace(record, shiny_perfect_count=record.perfect_count + 1), expert=True
        )
    with pytest.raises(ValueError, match="clear type"):
        document.set_clear_type(key, 6)

    document.set_score(replace(record, score=record.score + 1), expert=True)
    document.set_clear_type(key, 99, expert=True)
