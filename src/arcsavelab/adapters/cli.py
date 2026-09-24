from __future__ import annotations

import argparse
import locale
import sqlite3
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from arcsavelab.errors import ArcSaveError
from arcsavelab.interface import OpenRequest, SaveKind, SourceSpec
from arcsavelab.sources import SOURCE_FILES, classify_source


def _version() -> str:
    try:
        return version("arcsavelab")
    except PackageNotFoundError:
        return "0.2.0.dev0"


def _root_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arcsavelab",
        description="Arcaea 7.0.260c save editor and integrity verifier",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
    subparsers = parser.add_subparsers(dest="command")

    verify = subparsers.add_parser("verify", help="verify UserDefault integrity values")
    verify.add_argument("args", nargs=argparse.REMAINDER)

    catalog = subparsers.add_parser("catalog", help="inspect or rebuild a game catalog")
    catalog.add_argument(
        "action",
        choices=("verify", "info", "build", "compare"),
        nargs="?",
        default="info",
    )
    catalog.add_argument("--version", default="7.0.260c", dest="game_version")
    catalog.add_argument("--apk", type=Path)
    catalog.add_argument("--out", type=Path)
    catalog.add_argument("--old", type=Path)
    catalog.add_argument("--new", type=Path)

    tui = subparsers.add_parser("edit", help="launch the interactive save editor")
    _add_tui_arguments(tui)
    return parser


def _add_tui_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("files", nargs="*", type=Path, help="save files to identify by content")
    parser.add_argument("--preferences", "--prefs", type=Path)
    parser.add_argument("--unlocks", type=Path)
    parser.add_argument("--missions", type=Path)
    parser.add_argument("--scores", type=Path)
    parser.add_argument("--device-id")
    parser.add_argument("--user-id", type=int)
    language = "zh-Hans" if (locale.getlocale()[0] or "").lower().startswith("zh") else "en"
    parser.add_argument("--language", choices=("en", "zh-Hans"), default=language)
    parser.add_argument("--expert", action="store_true")
    parser.add_argument(
        "--force-profile",
        action="store_true",
        help="explicitly edit a structurally mismatched save with the selected version profile",
    )


def _request_from_args(args: argparse.Namespace) -> OpenRequest:
    sources: dict[SaveKind, Path] = {}
    explicit = {
        SaveKind.PREFERENCES: getattr(args, "preferences", None),
        SaveKind.UNLOCK_PROGRESS: getattr(args, "unlocks", None),
        SaveKind.MISSION_PROGRESS: getattr(args, "missions", None),
        SaveKind.SCORE_DATABASE: getattr(args, "scores", None),
    }
    for kind, path in explicit.items():
        if path is not None:
            sources[kind] = path
    for path in getattr(args, "files", ()):  # content-based identification
        if path.is_dir():
            found = [
                (kind, path / name)
                for kind, name in SOURCE_FILES.items()
                if (path / name).is_file()
            ]
            if not found:
                raise ValueError(f"no standard save files in: {path}")
            for kind, file in found:
                if kind in sources:
                    raise ValueError(f"more than one {kind.value} file was selected")
                sources[kind] = file
            continue
        kind = classify_source(path)
        if kind in sources:
            raise ValueError(f"more than one {kind.value} file was selected")
        sources[kind] = path
    return OpenRequest(
        sources=tuple(SourceSpec(kind, path) for kind, path in sources.items()),
        locale=getattr(args, "language", "en"),
        device_id=getattr(args, "device_id", None),
        user_id=getattr(args, "user_id", None),
        expert=getattr(args, "expert", False),
        allow_version_override=getattr(args, "force_profile", False),
    )


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except (ArcSaveError, ValueError, OSError, sqlite3.DatabaseError) as exc:
        print(f"arcsavelab: {exc}", file=sys.stderr)
        return 2


def _main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # No subcommand is the shortest path to the TUI.
    root_commands = {"verify", "catalog", "edit", "--version", "-h", "--help"}
    if not argv or argv[0] not in root_commands:
        parser = argparse.ArgumentParser(prog="arcsavelab")
        _add_tui_arguments(parser)
        args = parser.parse_args(argv)
        from .tui import run_tui

        return run_tui(_request_from_args(args))

    parser = _root_parser()
    args = parser.parse_args(argv)
    if args.command == "verify":
        from .verify_cli import main as verify_main

        return verify_main(args.args)
    if args.command == "catalog":
        from arcsavelab.catalog import load_catalog, verify_catalog

        if args.action == "build":
            if args.apk is None or args.out is None:
                parser.error("catalog build requires --apk and --out")
            from arcsavelab.catalog_builder import main as catalog_builder_main

            return catalog_builder_main(
                [
                    "build",
                    "--apk",
                    str(args.apk),
                    "--version",
                    args.game_version,
                    "--out",
                    str(args.out),
                ]
            )
        if args.action == "compare":
            if args.old is None or args.new is None or args.out is None:
                parser.error("catalog compare requires --old, --new and --out")
            from arcsavelab.catalog_builder import main as catalog_builder_main

            return catalog_builder_main(
                [
                    "compare",
                    "--old",
                    str(args.old),
                    "--new",
                    str(args.new),
                    "--out",
                    str(args.out),
                ]
            )
        if args.action == "verify":
            report = verify_catalog(args.game_version)
            print(report)
            return 0
        catalog = load_catalog(args.game_version)
        print(f"ArcSaveLab catalog {args.game_version}")
        print(f"songs: {len(catalog.songs)}")
        print(f"charts: {len(catalog.charts)}")
        print(f"packs: {len(catalog.packs)}")
        print(f"partners: {len(catalog.partners)}")
        print(f"story entries: {len(catalog.story_entries)}")
        print(f"missions: {len(catalog.missions)}")
        return 0
    if args.command == "edit":
        from .tui import run_tui

        return run_tui(_request_from_args(args))
    parser.print_help()
    return 0
