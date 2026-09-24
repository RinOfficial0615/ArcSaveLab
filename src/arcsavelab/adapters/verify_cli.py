"""CLI adapter for the structured integrity verifier.

The project-level CLI owns command dispatch.  This module only configures and
executes the ``verify`` subcommand so the application interface remains unaware
of argparse and presentation details.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import TextIO

from ..formats.xml_spans import XmlSpanError
from ..verification import VerificationReport, verify_userdefaults


def configure_parser(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("prefs", type=Path, help="Cocos2dxPrefsFile.xml path")
    parser.add_argument("--un", type=Path, help="un ValueMap plist (default: beside prefs)")
    parser.add_argument("--ms", type=Path, help="ms ValueMap plist (default: beside prefs)")
    parser.add_argument(
        "--device-id",
        default=os.environ.get("ARCAEA_DEVICE_ID"),
        help="Java-provided device ID (or ARCAEA_DEVICE_ID)",
    )
    parser.add_argument(
        "--user-id",
        type=int,
        help="account user ID (default: lu/lastLocalSyncUserId in prefs)",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="output format (default: text)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="also fail when an integrity entry is skipped or unrecognized",
    )
    return parser


def add_verify_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("verify", help="verify UserDefault integrity values")
    configure_parser(parser)
    parser.set_defaults(command_handler=run_namespace)
    return parser


def build_parser() -> argparse.ArgumentParser:
    return configure_parser(
        argparse.ArgumentParser(
            prog="arcsavelab verify",
            description="Verify Arcaea UserDefault integrity values",
        )
    )


def _print_text(report: VerificationReport, stream: TextIO) -> None:
    if report.user_id is not None:
        print(f"user_id: {report.user_id}", file=stream)
    for result in report.results:
        value = result.computed if result.computed is not None else "-"
        detail = f" ({result.note})" if result.note else ""
        print(
            f"{result.status.value:8} {result.key:40} {value:32} "
            f"<- {result.source} [{result.algorithm}]{detail}",
            file=stream,
        )
    summary = ", ".join(f"{status}={count}" for status, count in report.counts.items())
    print(f"summary: {summary}", file=stream)


def run_namespace(args: argparse.Namespace, *, stdout: TextIO | None = None) -> int:
    stream = stdout or sys.stdout
    report = verify_userdefaults(
        args.prefs,
        un=args.un,
        ms=args.ms,
        device_id=args.device_id,
        user_id=args.user_id,
    )
    if args.format == "json":
        print(
            json.dumps(report.to_dict(strict=args.strict), ensure_ascii=False, indent=2),
            file=stream,
        )
    else:
        _print_text(report, stream)
    return report.exit_code(strict=args.strict)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        return run_namespace(parser.parse_args(argv))
    except (OSError, ValueError, XmlSpanError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "add_verify_parser",
    "build_parser",
    "configure_parser",
    "main",
    "run_namespace",
]
