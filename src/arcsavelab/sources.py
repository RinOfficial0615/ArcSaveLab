"""Portable source discovery; standard filenames are hints, not installation paths."""

from pathlib import Path

from .interface import SaveKind

SOURCE_FILES = {
    SaveKind.PREFERENCES: "Cocos2dxPrefsFile.xml",
    SaveKind.SCORE_DATABASE: "st3",
    SaveKind.UNLOCK_PROGRESS: "un",
    SaveKind.MISSION_PROGRESS: "ms",
}


def classify_source(path: Path) -> SaveKind:
    with path.open("rb") as stream:
        prefix = stream.read(4096)
    if prefix.startswith(b"SQLite format 3\x00"):
        return SaveKind.SCORE_DATABASE
    text = prefix.decode("utf-8-sig", errors="ignore")
    if "<map" in text:
        return SaveKind.PREFERENCES
    if "<plist" in text or "<dict" in text:
        if path.name == "ms":
            return SaveKind.MISSION_PROGRESS
        if path.name == "un":
            return SaveKind.UNLOCK_PROGRESS
        whole = path.read_text(encoding="utf-8-sig")
        if "<string>claimed</string>" in whole or "mission_" in whole:
            return SaveKind.MISSION_PROGRESS
        return SaveKind.UNLOCK_PROGRESS
    raise ValueError(f"unrecognized save file: {path}")
