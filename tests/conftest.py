from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from arcsavelab.formats.st3_sqlite import calculate_score, grade_for_score
from arcsavelab.integrity import (
    device_bound_value_hash,
    direct_value_hash,
    validity_hash_for_int,
    value_map_hash,
)
from arcsavelab.interface import OpenRequest, SaveKind, SourceSpec

DEVICE_ID = "synthetic-device"
USER_ID = 123
SONG_ID = "sayonarahatsukoi"
MISSION_ID = "mission_1_1_tutorial"


@dataclass(frozen=True, slots=True)
class SyntheticSaveSet:
    root: Path
    preferences: Path
    unlocks: Path
    missions: Path
    scores: Path

    @property
    def paths(self) -> dict[SaveKind, Path]:
        return {
            SaveKind.PREFERENCES: self.preferences,
            SaveKind.UNLOCK_PROGRESS: self.unlocks,
            SaveKind.MISSION_PROGRESS: self.missions,
            SaveKind.SCORE_DATABASE: self.scores,
        }

    def request(
        self,
        *kinds: SaveKind,
        device_id: str | None = DEVICE_ID,
        user_id: int | None = USER_ID,
        expert: bool = False,
    ) -> OpenRequest:
        return OpenRequest(
            sources=tuple(SourceSpec(kind, self.paths[kind]) for kind in kinds),
            device_id=device_id,
            user_id=user_id,
            expert=expert,
        )


def _create_st3(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE scores(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version TEXT NOT NULL,
                score INTEGER NOT NULL,
                shinyPerfectCount INTEGER NOT NULL,
                perfectCount INTEGER NOT NULL,
                nearCount INTEGER NOT NULL,
                missCount INTEGER NOT NULL,
                date INTEGER NOT NULL,
                songId TEXT NOT NULL,
                songDifficulty INTEGER NOT NULL,
                modifier INTEGER NOT NULL,
                health INTEGER NOT NULL,
                ct INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE cleartypes(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                songId TEXT NOT NULL,
                songDifficulty INTEGER NOT NULL,
                clearType INTEGER NOT NULL,
                ct INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE schemaversion(appliedVersion INTEGER NOT NULL);
            INSERT INTO schemaversion(appliedVersion) VALUES(4);
            """
        )
        rows = (
            (0, 90, 8, 2, 5, 1),
            (1, 100, 0, 0, 10, 3),
        )
        for control_type, pure, far, lost, shiny, clear_type in rows:
            score = calculate_score(pure, far, lost, shiny)
            connection.execute(
                "INSERT INTO scores(version,score,shinyPerfectCount,perfectCount,"
                "nearCount,missCount,date,songId,songDifficulty,modifier,health,ct) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "7.0.255c",
                    score,
                    shiny,
                    pure,
                    far,
                    lost,
                    1_700_000_000_000 + control_type,
                    SONG_ID,
                    2,
                    0,
                    100,
                    control_type,
                ),
            )
            connection.execute(
                "INSERT INTO cleartypes(songId,songDifficulty,clearType,ct) VALUES(?,?,?,?)",
                (SONG_ID, 2, clear_type, control_type),
            )
        connection.commit()
    finally:
        connection.close()


@pytest.fixture
def synthetic_saves(tmp_path: Path) -> SyntheticSaveSet:
    unlocks = tmp_path / "un"
    unlocks.write_bytes(
        b"<?xml version='1.0' encoding='UTF-8'?>\n"
        b"<!DOCTYPE plist SYSTEM 'synthetic.dtd'>\n"
        b"<plist><dict>\n"
        b"  <key>sayonarahatsukoi|2|0</key><integer>0</integer>\n"
        b"</dict></plist>\n"
    )
    missions = tmp_path / "ms"
    missions.write_bytes(
        b"<?xml version='1.0' encoding='UTF-8'?>\n"
        b"<plist><dict>\n"
        b"  <key>mission_1_1_tutorial</key><string>pending</string>\n"
        b"</dict></plist>\n"
    )

    pure, far, lost, shiny = 90, 8, 2, 5
    score = calculate_score(pure, far, lost, shiny)
    grade = grade_for_score(score)
    values = {
        "fr_v": 100,
        "fr_k": validity_hash_for_int(100),
        "p_v": "",
        "p_k": "",
        "s_v": "",
        "s_k": "",
        "wu_v": "",
        "wu_k": "",
        "ac_v": "0",
        "ac_k": device_bound_value_hash("0", DEVICE_ID, USER_ID),
        "fc_v": "0",
        "fc_k": direct_value_hash("0"),
        "fs_v": "",
        "fs_k": "",
        "st_v": "0|1|0|0",
        "st_k": direct_value_hash("0|1|0|0"),
        "cs_v": f"{SONG_ID}|2|{grade}",
        "cs_k": direct_value_hash(f"{SONG_ID}|2|{grade}"),
        "fin_v": "0|1|1337",
        "fin_k": direct_value_hash("0|1|1337"),
        "casa_k": 5,
        "casa_h": validity_hash_for_int(5),
        "un_k": value_map_hash(unlocks),
        "ms_k": value_map_hash(missions),
    }
    preferences = tmp_path / "Cocos2dxPrefsFile.xml"
    preferences.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<!-- synthetic fixture: no personal save data -->
<map>
  <int name="lu" value="{USER_ID}" />
  <int name="highspeed_int" value="30" />
  <int name="fr_v" value="{values["fr_v"]}" />
  <string name="fr_k">{values["fr_k"]}</string>
  <string name="p_v">{values["p_v"]}</string>
  <string name="p_k">{values["p_k"]}</string>
  <string name="s_v">{values["s_v"]}</string>
  <string name="s_k">{values["s_k"]}</string>
  <string name="wu_v">{values["wu_v"]}</string>
  <string name="wu_k">{values["wu_k"]}</string>
  <string name="ac_v">{values["ac_v"]}</string>
  <string name="ac_k">{values["ac_k"]}</string>
  <string name="fc_v">{values["fc_v"]}</string>
  <string name="fc_k">{values["fc_k"]}</string>
  <string name="fs_v">{values["fs_v"]}</string>
  <string name="fs_k">{values["fs_k"]}</string>
  <string name="st_v">{values["st_v"]}</string>
  <string name="st_k">{values["st_k"]}</string>
  <string name="cs_v">{values["cs_v"]}</string>
  <string name="cs_k">{values["cs_k"]}</string>
  <string name="fin_v">{values["fin_v"]}</string>
  <string name="fin_k">{values["fin_k"]}</string>
  <int name="casa_k" value="{values["casa_k"]}" />
  <string name="casa_h">{values["casa_h"]}</string>
  <int name="ch" value="0" />
  <string name="un_k">{values["un_k"]}</string>
  <string name="ms_k">{values["ms_k"]}</string>
</map>
""",
        encoding="utf-8",
        newline="\n",
    )

    scores = tmp_path / "st3"
    _create_st3(scores)
    return SyntheticSaveSet(tmp_path, preferences, unlocks, missions, scores)
