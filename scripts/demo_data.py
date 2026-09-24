"""Generate a small, entirely synthetic demo save (never read personal examples)."""

import sqlite3
from pathlib import Path
from xml.sax.saxutils import escape

from arcsavelab.formats.st3_sqlite import calculate_score
from arcsavelab.integrity import (
    device_bound_value_hash,
    direct_value_hash,
    validity_hash_for_int,
    value_map_hash,
)


def create_demo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "un").write_text(
        "<plist><dict><key>sayonarahatsukoi|2|0</key><integer>0</integer></dict></plist>",
        encoding="utf-8",
    )
    (root / "ms").write_text(
        "<plist><dict><key>mission_1_1_tutorial</key><string>pending</string></dict></plist>",
        encoding="utf-8",
    )
    values = {
        "lu": 123,
        "fr_v": 2468,
        "fr_k": validity_hash_for_int(2468),
        "highspeed_int": 50,
        "offset_int_2": 0,
        "bt_offset": 80,
        "sfx_int": 16,
        "language": "en",
        "songSort": "TITLE",
        "songOrder": "ASCENDING",
        "p_v": "",
        "p_k": "",
        "s_v": "",
        "s_k": "",
        "wu_v": "",
        "wu_k": "",
        "ac_v": "0,1",
        "ac_k": device_bound_value_hash("0,1", "demo-device", 123),
        "st_v": "0|1|0|0",
        "st_k": direct_value_hash("0|1|0|0"),
        "cs_v": "",
        "cs_k": "",
        "fc_v": "0",
        "fc_k": direct_value_hash("0"),
        "fs_v": "",
        "fs_k": "",
        "ch": 0,
        "un_k": value_map_hash(root / "un"),
        "ms_k": value_map_hash(root / "ms"),
    }
    lines = ["<?xml version='1.0' encoding='UTF-8'?>", "<!-- SYNTHETIC DEMO -->", "<map>"]
    for key, value in values.items():
        lines.append(
            f'  <int name="{key}" value="{value}" />'
            if isinstance(value, int)
            else f'  <string name="{key}">{escape(value)}</string>'
        )
    lines.append("</map>")
    (root / "Cocos2dxPrefsFile.xml").write_text("\n".join(lines), encoding="utf-8")
    with sqlite3.connect(root / "st3") as db:
        db.executescript("""
            CREATE TABLE scores(id INTEGER PRIMARY KEY, version TEXT, score INTEGER,
              shinyPerfectCount INTEGER, perfectCount INTEGER, nearCount INTEGER, missCount INTEGER,
              date INTEGER, songId TEXT, songDifficulty INTEGER,
              modifier INTEGER, health INTEGER, ct INTEGER);
            CREATE TABLE cleartypes(id INTEGER PRIMARY KEY, songId TEXT, songDifficulty INTEGER,
              clearType INTEGER, ct INTEGER);
            CREATE TABLE schemaversion(appliedVersion INTEGER);
            INSERT INTO schemaversion VALUES(4);
        """)
        for index, song in enumerate(
            (
                "sayonarahatsukoi",
                "grievouslady",
                "fractureray",
                "testify",
                "pragmatism",
                "etherstrike",
                "worldender",
                "tempestissimo",
                "arcahv",
            )
        ):
            pure, far, lost, shiny = 970 + index, 20 - index, 10, 900
            db.execute(
                "INSERT INTO scores VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    index,
                    "7.0.255c",
                    calculate_score(pure, far, lost, shiny),
                    shiny,
                    pure,
                    far,
                    lost,
                    1700000000000,
                    song,
                    2,
                    0,
                    100,
                    0,
                ),
            )
            db.execute("INSERT INTO cleartypes VALUES(?,?,?,?,?)", (index, song, 2, 1, 0))
