"""Reject accidental private references, binaries, and development state in releases."""

import argparse
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

BLOCKED_PARTS = {".examples", ".old_ver", ".local", ".venv", ".git", "__pycache__"}
BLOCKED_SUFFIXES = {".apk", ".so", ".i64", ".pyc", ".db", ".sqlite", ".sqlite3"}


def check(directory: Path) -> None:
    artifacts = sorted(directory.glob("*.whl")) + sorted(directory.glob("*.tar.gz"))
    if len(artifacts) < 2:
        raise SystemExit("build both wheel and source archive first")
    for artifact in artifacts:
        if artifact.suffix == ".whl":
            with zipfile.ZipFile(artifact) as archive:
                names = archive.namelist()
        else:
            with tarfile.open(artifact) as archive:
                names = archive.getnames()
        for name in names:
            path = PurePosixPath(name)
            if BLOCKED_PARTS.intersection(path.parts) or path.suffix in BLOCKED_SUFFIXES:
                raise SystemExit(f"unexpected private/build artifact: {artifact.name}: {name}")
        if artifact.suffix == ".whl":
            required = {
                "arcsavelab/ui/styles.tcss",
                "arcsavelab/resources/catalog/7.0.255c/catalog.json",
            }
            if not required.issubset(names):
                raise SystemExit("wheel is missing runtime resources")
        print(f"PASS {artifact.name}: {len(names)} entries; no private reference files")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    check(parser.parse_args().directory)
