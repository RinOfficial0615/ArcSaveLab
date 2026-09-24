"""Structured verification for Arcaea UserDefault integrity values."""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from .formats.plist_map import PlistMapDocument, parse_plist_map
from .formats.prefs_xml import Preference, SharedPrefsDocument, parse_shared_prefs
from .integrity import (
    DEVICE_HASH_SOURCES,
    DIRECT_HASH_SOURCES,
    INT_HASH_SOURCES,
    MAP_HASH_FILES,
    device_bound_value_hash,
    direct_value_hash,
    validity_hash_for_int,
    value_map_hash,
)


class VerificationStatus(StrEnum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    MISSING = "MISSING"
    SKIP = "SKIP"


@dataclass(frozen=True, slots=True)
class VerificationResult:
    key: str
    source: str
    algorithm: str
    current: str | None
    computed: str | None
    status: VerificationStatus
    note: str = ""
    reason_code: str | None = None

    @property
    def expected(self) -> str | None:
        return self.computed

    @property
    def ok(self) -> bool:
        return self.status is VerificationStatus.MATCH

    def to_dict(self) -> dict[str, Any]:
        public_status = (
            "unresolved" if self.status is VerificationStatus.SKIP else self.status.value.lower()
        )
        return {
            "key": self.key,
            "source": self.source,
            "algorithm": self.algorithm,
            "current": self.current,
            "expected": self.expected,
            "status": public_status,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True, slots=True)
class VerificationReport:
    results: tuple[VerificationResult, ...]
    user_id: int | None
    prefs_path: Path | None = None
    sources: Mapping[str, str | None] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(
            result.status not in {VerificationStatus.MISMATCH, VerificationStatus.MISSING}
            for result in self.results
        )

    @property
    def strict_ok(self) -> bool:
        return bool(self.results) and all(result.ok for result in self.results)

    @property
    def values(self) -> dict[str, str]:
        return {
            result.key: result.computed for result in self.results if result.computed is not None
        }

    @property
    def counts(self) -> dict[str, int]:
        counts = Counter(result.status.value for result in self.results)
        return dict(sorted(counts.items()))

    @property
    def summary(self) -> dict[str, int]:
        counts = Counter(
            "unresolved"
            if result.status is VerificationStatus.SKIP
            else result.status.value.lower()
            for result in self.results
        )
        return {
            "total": len(self.results),
            "match": counts["match"],
            "mismatch": counts["mismatch"],
            "missing": counts["missing"],
            "unresolved": counts["unresolved"],
        }

    def exit_code(self, *, strict: bool = False) -> int:
        return 0 if (self.strict_ok if strict else self.ok) else 1

    def for_key(self, key: str) -> VerificationResult | None:
        return next((result for result in self.results if result.key == key), None)

    def to_dict(self, *, strict: bool = False) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "sources": dict(self.sources),
            "resolved_user_id": self.user_id,
            "results": [result.to_dict() for result in self.results],
            "summary": self.summary,
            "ok": self.strict_ok if strict else self.ok,
        }


PrefsSource = str | os.PathLike[str] | bytes | bytearray | memoryview | SharedPrefsDocument
MapSource = str | os.PathLike[str] | bytes | bytearray | memoryview | PlistMapDocument


def _preference_text(preference: Preference | None) -> str | None:
    if preference is None:
        return None
    if isinstance(preference.value, bool):
        return "true" if preference.value else "false"
    return str(preference.value)


def _make_result(
    *,
    key: str,
    source: str,
    algorithm: str,
    current: str | None,
    computed: str | None,
    note: str = "",
    reason_code: str | None = None,
) -> VerificationResult:
    if computed is None:
        status = VerificationStatus.SKIP
        effective_reason = reason_code or "not_computable"
    elif current is None:
        status = VerificationStatus.MISSING
        effective_reason = reason_code or "integrity_value_missing"
    elif current.lower() == computed.lower():
        status = VerificationStatus.MATCH
        effective_reason = None
    else:
        status = VerificationStatus.MISMATCH
        effective_reason = reason_code or "digest_mismatch"
    return VerificationResult(
        key=key,
        source=source,
        algorithm=algorithm,
        current=current,
        computed=computed,
        status=status,
        note=note,
        reason_code=effective_reason,
    )


def _load_optional_map(
    source: MapSource | None,
    *,
    default_path: Path | None,
    logical_name: str,
) -> tuple[PlistMapDocument | None, str]:
    if isinstance(source, PlistMapDocument):
        return source, str(source.source_path or logical_name)
    if isinstance(source, (bytes, bytearray, memoryview)):
        return parse_plist_map(source), f"<{logical_name}:memory>"
    candidate = Path(source) if source is not None else default_path
    if candidate is None or not candidate.is_file():
        return None, str(candidate or logical_name)
    return PlistMapDocument.from_path(candidate), str(candidate)


def _resolve_user_id(document: SharedPrefsDocument, explicit: int | None) -> int | None:
    if explicit is not None:
        return int(explicit)
    for candidate in ("lu", "lastLocalSyncUserId"):
        preference = document.get_entry(candidate)
        if preference is not None and preference.kind in {"int", "long"}:
            return int(preference.value)
    return None


def verify_userdefaults(
    prefs: PrefsSource,
    *,
    un: MapSource | None = None,
    ms: MapSource | None = None,
    device_id: str | None = None,
    user_id: int | None = None,
) -> VerificationReport:
    """Compute and compare every recognized UserDefault integrity value.

    ``prefs`` may be a path, bytes or an already edited document.  ``un`` and
    ``ms`` accept the same path/document forms for plist maps.  When ``prefs``
    has a source path, omitted map files are resolved beside it.
    """

    document = parse_shared_prefs(prefs)
    prefs_path = document.source_path
    base = prefs_path.parent if prefs_path is not None else None
    maps: dict[str, tuple[PlistMapDocument | None, str]] = {
        "un": _load_optional_map(
            un, default_path=(base / "un") if base else None, logical_name="un"
        ),
        "ms": _load_optional_map(
            ms, default_path=(base / "ms") if base else None, logical_name="ms"
        ),
    }
    return _evaluate_integrity(document, maps, device_id=device_id, user_id=user_id)


def evaluate_integrity(
    document: SharedPrefsDocument,
    *,
    un: PlistMapDocument | None = None,
    ms: PlistMapDocument | None = None,
    device_id: str | None = None,
    user_id: int | None = None,
) -> VerificationReport:
    """Pure policy evaluation over explicitly supplied documents, with no path discovery."""
    maps = {
        name: (doc, str(doc.source_path or name) if doc is not None else name)
        for name, doc in (("un", un), ("ms", ms))
    }
    return _evaluate_integrity(document, maps, device_id=device_id, user_id=user_id)


def _evaluate_integrity(
    document: SharedPrefsDocument,
    maps: Mapping[str, tuple[PlistMapDocument | None, str]],
    *,
    device_id: str | None,
    user_id: int | None,
) -> VerificationReport:
    prefs_path = document.source_path
    resolved_user_id = _resolve_user_id(document, user_id)
    candidates: list[str] = []
    seen: set[str] = set()
    # Known checksums should be reported as MISSING when their source exists,
    # even if the checksum element itself was deleted.
    all_known = {
        **DIRECT_HASH_SOURCES,
        **DEVICE_HASH_SOURCES,
        **INT_HASH_SOURCES,
    }
    known_order = (
        tuple(MAP_HASH_FILES)
        + tuple(DIRECT_HASH_SOURCES)
        + tuple(INT_HASH_SOURCES)
        + tuple(DEVICE_HASH_SOURCES)
    )
    for key in known_order:
        source_key = all_known.get(key)
        map_source_exists = key in MAP_HASH_FILES and maps[MAP_HASH_FILES[key]][0] is not None
        has_source = source_key is not None and source_key in document
        if key in document or has_source or map_source_exists:
            candidates.append(key)
            seen.add(key)
    extra_candidates = sorted(
        (
            key
            for key in document
            if key.endswith("_k") and key not in seen and key not in INT_HASH_SOURCES.values()
        ),
        key=lambda value: value.encode("utf-8"),
    )
    candidates.extend(extra_candidates)

    results: list[VerificationResult] = []
    for key in candidates:
        current = _preference_text(document.get_entry(key))
        if key in MAP_HASH_FILES:
            file_name = MAP_HASH_FILES[key]
            source_document, source_label = maps[file_name]
            computed = value_map_hash(source_document) if source_document else None
            results.append(
                _make_result(
                    key=key,
                    source=source_label,
                    algorithm="value-map-md5",
                    current=current,
                    computed=computed,
                    note="source file not found" if source_document is None else "",
                    reason_code=("source_file_missing" if source_document is None else None),
                )
            )
            continue

        if key in DIRECT_HASH_SOURCES:
            source_key = DIRECT_HASH_SOURCES[key]
            source_value = _preference_text(document.get_entry(source_key))
            results.append(
                _make_result(
                    key=key,
                    source=source_key,
                    algorithm="direct-md5-empty-sentinel",
                    current=current,
                    computed=(
                        direct_value_hash(source_value) if source_value is not None else None
                    ),
                    note=f"missing {source_key}" if source_value is None else "",
                    reason_code=("source_value_missing" if source_value is None else None),
                )
            )
            continue

        if key in DEVICE_HASH_SOURCES:
            source_key = DEVICE_HASH_SOURCES[key]
            source_value = _preference_text(document.get_entry(source_key))
            missing: list[str] = []
            if source_value is None:
                missing.append(source_key)
            # The native empty sentinel is independent of account/device state.
            if source_value == "":
                computed = ""
            else:
                if device_id is None:
                    missing.append("device_id")
                if resolved_user_id is None:
                    missing.append("user_id")
                computed = None
                if not missing:
                    computed = device_bound_value_hash(
                        source_value,  # type: ignore[arg-type]
                        device_id,  # type: ignore[arg-type]
                        resolved_user_id,  # type: ignore[arg-type]
                    )
            results.append(
                _make_result(
                    key=key,
                    source=source_key,
                    algorithm="device-user-bound-md5",
                    current=current,
                    computed=computed,
                    note="missing " + ", ".join(missing) if missing else "",
                    reason_code=(
                        "source_value_missing"
                        if source_value is None
                        else "identity_context_missing"
                        if missing
                        else None
                    ),
                )
            )
            continue

        if key in INT_HASH_SOURCES:
            source_key = INT_HASH_SOURCES[key]
            source = document.get_entry(source_key)
            int_computed: str | None = None
            note = ""
            reason_code: str | None = None
            if source is None:
                note = f"missing {source_key}"
                reason_code = "source_value_missing"
            elif source.kind not in {"int", "long"}:
                note = f"{source_key} is not an integer"
                reason_code = "source_type_invalid"
            else:
                int_computed = validity_hash_for_int(int(source.value))
            results.append(
                _make_result(
                    key=key,
                    source=source_key,
                    algorithm="validity-hash-for-int",
                    current=current,
                    computed=int_computed,
                    note=note,
                    reason_code=reason_code,
                )
            )
            continue

        # Integer gameplay fields named *_k store their digest in *_h.
        source = document.get_entry(key)
        companion_key = key[:-2] + "_h"
        if source is not None and source.kind in {"int", "long"}:
            results.append(
                _make_result(
                    key=companion_key,
                    source=key,
                    algorithm="validity-hash-for-int",
                    current=_preference_text(document.get_entry(companion_key)),
                    computed=validity_hash_for_int(int(source.value)),
                    note=f"{key} is the protected data value",
                )
            )
            continue

        results.append(
            _make_result(
                key=key,
                source=key,
                algorithm="unknown",
                current=current,
                computed=None,
                note="unrecognized *_k layout",
                reason_code="algorithm_unknown",
            )
        )

    sources: dict[str, str | None] = {
        "preferences": str(prefs_path) if prefs_path is not None else "<memory>",
        "un": maps["un"][1] if maps["un"][0] is not None else None,
        "ms": maps["ms"][1] if maps["ms"][0] is not None else None,
    }
    return VerificationReport(tuple(results), resolved_user_id, prefs_path, sources)


def apply_computed_integrity(
    prefs: SharedPrefsDocument,
    report: VerificationReport,
    *,
    statuses: Iterable[VerificationStatus] = (
        VerificationStatus.MATCH,
        VerificationStatus.MISMATCH,
        VerificationStatus.MISSING,
    ),
) -> tuple[str, ...]:
    """Apply computable report values to a document without writing it."""

    allowed = set(statuses)
    updated: list[str] = []
    for result in report.results:
        if result.computed is None or result.status not in allowed:
            continue
        before = prefs.get(result.key, object())
        prefs.set(result.key, result.computed, kind="string")
        if before != result.computed:
            updated.append(result.key)
    return tuple(updated)


# Concise semantic alias for transaction code that treats verification as the
# computation phase before applying report.values.
compute_userdefault_integrity = verify_userdefaults


__all__ = [
    "MapSource",
    "PrefsSource",
    "VerificationReport",
    "VerificationResult",
    "VerificationStatus",
    "apply_computed_integrity",
    "compute_userdefault_integrity",
    "verify_userdefaults",
]


@dataclass(frozen=True)
class IntegrityPlan:
    updates: tuple[VerificationResult, ...]
    unresolved: tuple[VerificationResult, ...]


def plan_integrity(
    report: VerificationReport,
    *,
    changed_preferences: set[str],
    changed_maps: set[str],
    repair: bool,
) -> IntegrityPlan:
    """Select repair work using the same discovery, empty and identity policy as verification.

    A no-op retains original digest bytes, including valid uppercase spelling. Unknown
    layouts and absent, unselected sources are never guessed or written.
    """
    updates, unresolved = [], []
    for result in report.results:
        if result.algorithm == "unknown" or result.reason_code in {
            "source_file_missing",
            "source_value_missing",
        }:
            continue
        map_name = MAP_HASH_FILES.get(result.key)
        changed = map_name in changed_maps if map_name else result.source in changed_preferences
        if not repair and not changed:
            continue
        if result.computed is None:
            unresolved.append(result)
        else:
            updates.append(result)
    return IntegrityPlan(tuple(updates), tuple(unresolved))
