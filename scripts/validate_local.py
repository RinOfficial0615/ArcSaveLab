"""Opt-in private validation. Test on temporary copies and report hashes/counts only."""

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from arcsavelab.interface import (
    BrowseQuery,
    CommitRequest,
    OpenRequest,
    Section,
    SetField,
    SourceSpec,
    open_save,
)
from arcsavelab.sources import SOURCE_FILES
from arcsavelab.transaction import sha256_bytes


def validate(source: Path, output: Path) -> None:
    originals = {kind: source / name for kind, name in SOURCE_FILES.items()}
    fingerprints = {kind.value: sha256_bytes(path.read_bytes()) for kind, path in originals.items()}
    with tempfile.TemporaryDirectory(prefix="arcsavelab-local-") as raw:
        root = Path(raw) / "copies"
        root.mkdir()
        for path in originals.values():
            shutil.copyfile(path, root / path.name)
        request = OpenRequest(
            tuple(SourceSpec(kind, root / path.name) for kind, path in originals.items())
        )
        session = open_save(request)
        try:
            initial = session.inspect()
            noop = session.preview()
            assert not any(plan.changed for plan in noop.artifacts)
            assert session.preferences
            amount = int(session.preferences.get("fr_v"))
            session.perform(SetField("fragments", "amount", 1 if amount != 1 else 2))
            for section, field in (
                (Section.UNLOCKS, "progress"),
                (Section.MISSIONS, "claimed"),
                (Section.SCORES, "far"),
            ):
                item = session.inspect(BrowseQuery(section, limit=1)).sections[0].items[0]
                old = next(f.value for f in item.fields if f.id == field)
                value = (
                    not old
                    if isinstance(old, bool)
                    else (int(old) + 1 if field == "far" else int(not old))
                )
                session.perform(SetField(item.id, field, value))
            preview = session.preview()
            assert preview.can_commit
            assert all(plan.changed for plan in preview.artifacts)
            receipt = session.commit(
                CommitRequest(destination=Path(raw) / "export", preview_token=preview.token)
            )
            assert receipt.verified
            assert not session.dirty and not any(a.changed for a in session.preview().artifacts)
            report = {
                "game_catalog": initial.game_version,
                "components": len(initial.sources),
                "section_counts": {s.section.value: s.total for s in initial.sections},
                "initial_diagnostics": [d.code for d in initial.diagnostics],
                "noop_byte_identical": True,
                "edited_all_components": True,
                "export_reopen_verified": True,
                "original_sha256": fingerprints,
            }
        finally:
            session.close(discard=True)
    assert fingerprints == {
        kind.value: sha256_bytes(path.read_bytes()) for kind, path in originals.items()
    }
    report["originals_unchanged"] = True
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("PASS local reference round-trip: all four components; originals unchanged")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    validate(args.source, args.output)
