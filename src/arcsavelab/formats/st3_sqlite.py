from __future__ import annotations

import hashlib
import sqlite3
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class ChartKey:
    song_id: str
    difficulty: int
    control_type: int = 0


@dataclass(frozen=True)
class ScoreRecord:
    key: ChartKey
    row_id: int | None
    version: str
    score: int
    shiny_perfect_count: int
    perfect_count: int
    near_count: int
    miss_count: int
    date: int
    modifier: int
    health: int


@dataclass(frozen=True)
class ClearRecord:
    key: ChartKey
    row_id: int | None
    clear_type: int


def calculate_score(
    perfect_count: int,
    near_count: int,
    miss_count: int,
    shiny_perfect_count: int,
) -> int:
    total = perfect_count + near_count + miss_count
    if total == 0:
        return 0
    return 10_000_000 * (2 * perfect_count + near_count) // (2 * total) + shiny_perfect_count


def grade_for_score(score: int) -> int:
    if score < 8_600_000:
        return 0
    if score < 8_900_000:
        return 1
    if score < 9_200_000:
        return 2
    if score < 9_500_000:
        return 3
    if score < 9_800_000:
        return 4
    if score < 9_900_000:
        return 5
    return 6


class St3Document:
    """Schema-aware editable snapshot of an Arcaea st3 database."""

    REQUIRED_SCORE_COLUMNS = {
        "id",
        "version",
        "score",
        "shinyPerfectCount",
        "perfectCount",
        "nearCount",
        "missCount",
        "date",
        "songId",
        "songDifficulty",
        "modifier",
        "health",
        "ct",
    }
    REQUIRED_CLEAR_COLUMNS = {
        "id",
        "songId",
        "songDifficulty",
        "clearType",
        "ct",
    }

    def __init__(
        self,
        source_path: Path,
        original_bytes: bytes,
        scores: dict[ChartKey, ScoreRecord],
        clears: dict[ChartKey, ClearRecord],
        schema_version: int,
    ) -> None:
        self.source_path = source_path
        self._original_bytes = original_bytes
        self._scores = scores
        self._clears = clears
        self.schema_version = schema_version
        self._original_scores = dict(scores)
        self._original_clears = dict(clears)

    @classmethod
    def from_path(cls, path: str | Path) -> St3Document:
        source = Path(path)
        for suffix in ("-wal", "-journal"):
            sidecar = Path(str(source) + suffix)
            if sidecar.exists() and sidecar.stat().st_size:
                raise ValueError("st3 has an active SQLite journal; close/checkpoint it first")
        original = source.read_bytes()
        uri = source.resolve().as_uri() + "?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        try:
            quick = connection.execute("PRAGMA quick_check").fetchone()[0]
            if quick != "ok":
                raise ValueError(f"SQLite quick_check failed: {quick}")
            score_columns = {row[1] for row in connection.execute("PRAGMA table_info(scores)")}
            clear_columns = {row[1] for row in connection.execute("PRAGMA table_info(cleartypes)")}
            if not cls.REQUIRED_SCORE_COLUMNS <= score_columns:
                missing = cls.REQUIRED_SCORE_COLUMNS - score_columns
                raise ValueError(f"st3 scores schema is missing: {sorted(missing)}")
            if not cls.REQUIRED_CLEAR_COLUMNS <= clear_columns:
                missing = cls.REQUIRED_CLEAR_COLUMNS - clear_columns
                raise ValueError(f"st3 cleartypes schema is missing: {sorted(missing)}")

            scores: dict[ChartKey, ScoreRecord] = {}
            for row in connection.execute(
                "SELECT id,version,score,shinyPerfectCount,perfectCount,nearCount,"
                "missCount,date,songId,songDifficulty,modifier,health,ct FROM scores"
            ):
                key = ChartKey(str(row["songId"]), int(row["songDifficulty"]), int(row["ct"]))
                if key in scores:
                    raise ValueError(f"st3 has duplicate score keys: {key}")
                scores[key] = ScoreRecord(
                    key=key,
                    row_id=int(row["id"]),
                    version=str(row["version"]),
                    score=int(row["score"]),
                    shiny_perfect_count=int(row["shinyPerfectCount"]),
                    perfect_count=int(row["perfectCount"]),
                    near_count=int(row["nearCount"]),
                    miss_count=int(row["missCount"]),
                    date=int(row["date"]),
                    modifier=int(row["modifier"]),
                    health=int(row["health"]),
                )

            clears: dict[ChartKey, ClearRecord] = {}
            for row in connection.execute(
                "SELECT id,songId,songDifficulty,clearType,ct FROM cleartypes"
            ):
                key = ChartKey(str(row["songId"]), int(row["songDifficulty"]), int(row["ct"]))
                if key in clears:
                    raise ValueError(f"st3 has duplicate clear keys: {key}")
                clears[key] = ClearRecord(key, int(row["id"]), int(row["clearType"]))

            schema_row = connection.execute(
                "SELECT MAX(appliedVersion) FROM schemaversion"
            ).fetchone()
            schema_version = int(schema_row[0]) if schema_row and schema_row[0] else 0
        finally:
            connection.close()
        return cls(source, original, scores, clears, schema_version)

    @property
    def original_bytes(self) -> bytes:
        return self._original_bytes

    @property
    def dirty(self) -> bool:
        return self._scores != self._original_scores or self._clears != self._original_clears

    @property
    def scores(self) -> tuple[ScoreRecord, ...]:
        return tuple(
            self._scores[key]
            for key in sorted(self._scores, key=lambda k: (k.song_id, k.difficulty, k.control_type))
        )

    @property
    def clears(self) -> tuple[ClearRecord, ...]:
        return tuple(
            self._clears[key]
            for key in sorted(self._clears, key=lambda k: (k.song_id, k.difficulty, k.control_type))
        )

    def get_score(self, key: ChartKey) -> ScoreRecord | None:
        return self._scores.get(key)

    def get_clear(self, key: ChartKey) -> ClearRecord | None:
        return self._clears.get(key)

    def set_score(self, record: ScoreRecord, *, expert: bool = False) -> None:
        self.validate_score(record, expert=expert)
        existing = self._scores.get(record.key)
        if existing is not None and record.row_id is None:
            record = replace(record, row_id=existing.row_id)
        self._scores[record.key] = record

    def set_clear_type(self, key: ChartKey, clear_type: int, *, expert: bool = False) -> None:
        if not expert and clear_type not in range(6):
            raise ValueError("clear type must be between 0 and 5")
        existing = self._clears.get(key)
        self._clears[key] = ClearRecord(key, existing.row_id if existing else None, int(clear_type))

    def delete(self, key: ChartKey) -> None:
        self._scores.pop(key, None)
        self._clears.pop(key, None)

    @staticmethod
    def validate_score(record: ScoreRecord, *, expert: bool = False) -> None:
        counts = (
            record.shiny_perfect_count,
            record.perfect_count,
            record.near_count,
            record.miss_count,
        )
        if any(value < 0 for value in counts):
            raise ValueError("judgement counts cannot be negative")
        if record.shiny_perfect_count > record.perfect_count:
            raise ValueError("shiny PURE count cannot exceed PURE count")
        if record.key.difficulty not in range(5) and not expert:
            raise ValueError("difficulty must be between 0 and 4")
        expected = calculate_score(
            record.perfect_count,
            record.near_count,
            record.miss_count,
            record.shiny_perfect_count,
        )
        if record.score != expected and not expert:
            raise ValueError(f"score must equal the judgement formula result ({expected})")

    def restore(self, scores: Iterable[ScoreRecord], clears: Iterable[ClearRecord]) -> None:
        self._scores = {record.key: record for record in scores}
        self._clears = {record.key: record for record in clears}

    def render_bytes(self) -> bytes:
        if not self.dirty:
            return self._original_bytes
        with tempfile.TemporaryDirectory(prefix="arcsavelab-st3-") as directory:
            path = Path(directory) / "st3"
            path.write_bytes(self._original_bytes)
            self._apply_to_path(path)
            return path.read_bytes()

    def _apply_to_path(self, path: Path) -> None:
        connection = sqlite3.connect(path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            original_keys = set(self._original_scores)
            current_keys = set(self._scores)
            for key in original_keys - current_keys:
                connection.execute(
                    "DELETE FROM scores WHERE songId=? AND songDifficulty=? AND ct=?",
                    (key.song_id, key.difficulty, key.control_type),
                )
            original_clear_keys = set(self._original_clears)
            current_clear_keys = set(self._clears)
            for key in original_clear_keys - current_clear_keys:
                connection.execute(
                    "DELETE FROM cleartypes WHERE songId=? AND songDifficulty=? AND ct=?",
                    (key.song_id, key.difficulty, key.control_type),
                )

            for key, record in self._scores.items():
                if self._original_scores.get(key) == record:
                    continue
                values = (
                    record.version,
                    record.score,
                    record.shiny_perfect_count,
                    record.perfect_count,
                    record.near_count,
                    record.miss_count,
                    record.date,
                    key.song_id,
                    key.difficulty,
                    record.modifier,
                    record.health,
                    key.control_type,
                )
                cursor = connection.execute(
                    "UPDATE scores SET version=?,score=?,shinyPerfectCount=?,perfectCount=?,"
                    "nearCount=?,missCount=?,date=?,songId=?,songDifficulty=?,modifier=?,"
                    "health=?,ct=? "
                    "WHERE songId=? AND songDifficulty=? AND ct=?",
                    values + (key.song_id, key.difficulty, key.control_type),
                )
                if cursor.rowcount == 0:
                    connection.execute(
                        "INSERT INTO scores(version,score,shinyPerfectCount,perfectCount,nearCount,"
                        "missCount,date,songId,songDifficulty,modifier,health,ct) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        values,
                    )

            for key, clear_record in self._clears.items():
                if self._original_clears.get(key) == clear_record:
                    continue
                cursor = connection.execute(
                    "UPDATE cleartypes SET clearType=? "
                    "WHERE songId=? AND songDifficulty=? AND ct=?",
                    (clear_record.clear_type, key.song_id, key.difficulty, key.control_type),
                )
                if cursor.rowcount == 0:
                    connection.execute(
                        "INSERT INTO cleartypes(songId,songDifficulty,clearType,ct) "
                        "VALUES(?,?,?,?)",
                        (key.song_id, key.difficulty, clear_record.clear_type, key.control_type),
                    )
            connection.commit()
            quick = connection.execute("PRAGMA quick_check").fetchone()[0]
            if quick != "ok":
                raise ValueError(f"staged SQLite quick_check failed: {quick}")
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def fingerprint(self) -> str:
        return hashlib.sha256(self.render_bytes()).hexdigest()
