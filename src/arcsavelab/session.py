from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from types import TracebackType
from typing import Any

from .errors import (
    CapabilityUnavailable,
    ClosedSession,
    CommitBlocked,
    Diagnostic,
    InvalidEdit,
    OpenFailed,
    ReloadRequired,
    StaleRevision,
    UncommittedChanges,
)
from .formats.plist_map import PlistMapDocument
from .formats.prefs_xml import SharedPrefsDocument
from .formats.st3_sqlite import (
    ChartKey,
    ClearRecord,
    ScoreRecord,
    St3Document,
    calculate_score,
    grade_for_score,
)
from .integrity import (
    INT_HASH_SOURCES,
)
from .interface import (
    ArtifactPlan,
    Availability,
    BatchSet,
    BrowseQuery,
    CommitPreview,
    CommitReceipt,
    CommitRequest,
    EditorSpec,
    FieldView,
    ItemView,
    OpenRequest,
    Operation,
    OperationReport,
    Redo,
    RepairIntegrity,
    SaveKind,
    Section,
    SectionView,
    SemanticChange,
    SetDeviceIdentity,
    SetField,
    SourceSpec,
    Undo,
    WorkspaceView,
)
from .transaction import ArtifactPayload, TransactionManager, sha256_bytes
from .verification import VerificationStatus, evaluate_integrity, plan_integrity

INT_COMPANIONS = {source: digest for digest, source in INT_HASH_SOURCES.items()}


SETTING_FIELDS: dict[str, tuple[str, EditorSpec]] = {
    "highspeed_int": ("field.note_speed", EditorSpec("integer", minimum=10, maximum=65)),
    "offset_int_2": ("field.audio_offset", EditorSpec("integer", minimum=-999, maximum=999)),
    "bt_offset": ("field.bluetooth_offset", EditorSpec("integer", minimum=-999, maximum=999)),
    "sfx_int": ("field.sfx_volume", EditorSpec("integer", minimum=0, maximum=20)),
    "colorblindfriendly": ("field.colorblind", EditorSpec("boolean")),
    "performancemode_v2": ("field.performance", EditorSpec("boolean")),
    "language": (
        "field.game_language",
        EditorSpec(
            "choice",
            choices=(
                ("en", "English"),
                ("ja", "日本語"),
                ("ko", "한국어"),
                ("zh-Hans", "简体中文"),
                ("zh-Hant", "繁體中文"),
            ),
        ),
    ),
    "songSort": (
        "field.song_sort",
        EditorSpec(
            "choice",
            choices=tuple(
                (value, value.title())
                for value in ("TITLE", "DIFFICULTY", "GRADE", "DATE", "CLEARTYPE")
            ),
        ),
    ),
    "songOrder": (
        "field.song_order",
        EditorSpec("choice", choices=(("ASCENDING", "Ascending"), ("DESCENDING", "Descending"))),
    ),
}


SKILL_FIELDS: dict[str, tuple[str, int, int]] = {
    "ca_wacca_luin_k": ("Luin fragment-skill progress", 0, 10),
    "ca_wacca_luin_awakened_k": ("Awakened Luin fragment-skill progress", 0, 15),
    "ca_wacca_luin_awakened_lifebar_k": ("Awakened Luin stored lifebar", 0, 100),
    "ca_wacca_lily_k": ("Lily fragment-skill progress", 0, 20),
    "ca_wacca_elizabeth_k": ("Elizabeth fragment-skill progress", 0, 10),
    "ca_nonoka_rank_k": ("Nonoka clear-streak fragment rank", 0, 4),
    "casa_k": ("Fragments earned toward the next doubling", 0, 99),
}


MISSION_LABELS = {
    "tutorial": ("Complete the tutorial", "完成教程"),
    "clearsong": ("Clear a song", "通关歌曲"),
    "settings": ("Open Settings", "打开设置"),
    "allsongsview": ("Open All Songs", "查看全部歌曲"),
    "fragunlock": ("Unlock with Fragments", "使用残片解锁"),
    "account": ("Use an account", "登录或创建账号"),
    "profile": ("Open Profile", "查看个人资料"),
    "partner": ("Use a Partner", "使用搭档"),
    "usestamina": ("Use stamina", "使用体力"),
    "prologuestart": ("Start the Prologue", "开始序章"),
    "prologuefinish": ("Finish the Prologue", "完成序章"),
    "prsclear": ("Clear a Present chart", "通关 PRESENT 难度"),
    "etherdrop": ("Obtain an Ether Drop", "获得以太之滴"),
    "step50": ("Reach STEP 50", "STEP 达到 50"),
    "frag60": ("Reach FRAG 60", "FRAG 达到 60"),
    "exgrade": ("Achieve EX grade", "达到 EX 评级"),
    "potential350": ("Reach Potential 3.50", "潜力值达到 3.50"),
    "twomaps": ("Play two World maps", "游玩两个世界模式地图"),
    "worldsongunlock": ("Unlock a World Mode song", "解锁世界模式歌曲"),
    "songgrouping": ("Use song grouping", "使用歌曲分组"),
    "partnerlv12": ("Raise a Partner to level 12", "搭档达到 12 级"),
    "cores": ("Obtain Awakening Cores", "获得觉醒核心"),
    "courseclear": ("Clear a Course", "通关课程"),
    "end": ("Complete the mission tier", "完成任务阶层"),
}


UNLOCK_TYPE_LABELS = {
    0: ("Fragment unlock", "残片解锁"),
    3: ("Repeated play progress", "重复游玩进度"),
    101: ("Special seal progress", "特殊封印进度"),
    102: ("Challenge progress", "挑战进度"),
    103: ("Partner seal", "搭档封印"),
    107: ("Magnolia spell progress", "Magnolia 法术进度"),
    109: ("Arghena puzzle", "Arghena 拼图"),
    110: ("Arghena course", "Arghena 课程"),
    112: ("Alter Ego puzzle", "Alter Ego 拼图"),
    114: ("Konzetsu chain", "绝灭链进度"),
    115: ("Konzetsu pre-challenge", "绝灭前置挑战"),
}


CLEAR_TYPES = (
    (0, "Track Lost"),
    (1, "Normal Clear"),
    (2, "Full Recall"),
    (3, "Pure Memory"),
    (4, "Easy Clear"),
    (5, "Hard Clear"),
)


@dataclass(frozen=True)
class _Snapshot:
    preferences: bytes | None
    unlocks: bytes | None
    missions: bytes | None
    scores: tuple[ScoreRecord, ...] | None
    clears: tuple[ClearRecord, ...] | None
    repair_requested: bool


@dataclass(frozen=True)
class _HistoryEntry:
    before: _Snapshot
    after: _Snapshot
    changes: tuple[SemanticChange, ...]


class SaveSession:
    """Deep save workspace used by the TUI, CLI and interface tests."""

    def __init__(self, request: OpenRequest) -> None:
        self.request = request
        self.sources = {source.kind: Path(source.path).resolve() for source in request.sources}
        if not self.sources:
            raise OpenFailed("at least one save file is required")
        if len(self.sources) != len(request.sources):
            raise OpenFailed("a save kind was selected more than once")
        if len(set(self.sources.values())) != len(self.sources):
            raise OpenFailed("the same file was selected for multiple save kinds")
        TransactionManager.recover_for_sources(self.sources)
        for kind, path in self.sources.items():
            if not path.is_file():
                raise OpenFailed(f"{kind.value}: file not found: {path}")

        from .catalog import load_catalog

        self.catalog = load_catalog(request.game_version)
        self.preferences = (
            SharedPrefsDocument.from_path(self.sources[SaveKind.PREFERENCES])
            if SaveKind.PREFERENCES in self.sources
            else None
        )
        self.unlocks = (
            PlistMapDocument.from_path(self.sources[SaveKind.UNLOCK_PROGRESS])
            if SaveKind.UNLOCK_PROGRESS in self.sources
            else None
        )
        self.missions = (
            PlistMapDocument.from_path(self.sources[SaveKind.MISSION_PROGRESS])
            if SaveKind.MISSION_PROGRESS in self.sources
            else None
        )
        self.score_database = (
            St3Document.from_path(self.sources[SaveKind.SCORE_DATABASE])
            if SaveKind.SCORE_DATABASE in self.sources
            else None
        )
        self._profile_mismatch = False
        self._profile_diagnostics: list[Diagnostic] = []
        self._detect_version_profile()

        self.device_id = request.device_id
        self.user_id = request.user_id if request.user_id is not None else self._resolve_user_id()
        self.revision = 0
        self._history: list[_HistoryEntry] = []
        self._redo: list[_HistoryEntry] = []
        self._closed = False
        self._reload_error: ReloadRequired | None = None
        self._diagnostics: list[Diagnostic] = []
        self._unlock_handles: dict[str, str] = {}
        self._integrity_blocked = False
        self._repair_requested = False
        self._baseline = self._snapshot()
        self._baseline_raw = self._source_snapshot()
        self._validate_open_state()
        self._diagnostics[:0] = self._profile_diagnostics

    @classmethod
    def open(cls, request: OpenRequest) -> SaveSession:
        return cls(request)

    def _source_snapshot(self) -> dict[SaveKind, bytes]:
        raw: dict[SaveKind, bytes] = {}
        for kind, document in (
            (SaveKind.PREFERENCES, self.preferences),
            (SaveKind.UNLOCK_PROGRESS, self.unlocks),
            (SaveKind.MISSION_PROGRESS, self.missions),
        ):
            if document is not None:
                raw[kind] = document.to_bytes()
        if self.score_database is not None:
            raw[SaveKind.SCORE_DATABASE] = self.score_database.original_bytes
        for kind, data in raw.items():
            if self.sources[kind].read_bytes() != data:
                raise OpenFailed("save changed while opening; close the writer and reopen")
        return raw

    def __enter__(self) -> SaveSession:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close(discard=exc is not None)

    @property
    def dirty(self) -> bool:
        return self._snapshot() != self._baseline

    def _ensure_open(self) -> None:
        if self._closed:
            raise ClosedSession()
        if self._reload_error is not None:
            raise self._reload_error

    def suspend(self, error: ReloadRequired) -> None:
        """Retain the draft while rejecting operations through stale borrowed references."""
        self._reload_error = error

    def _resolve_user_id(self) -> int | None:
        if self.preferences is None:
            return None
        for key in ("lu", "lastLocalSyncUserId"):
            value = self.preferences.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        return None

    def _validate_open_state(self) -> None:
        if self.preferences is None:
            return
        report = evaluate_integrity(
            self.preferences,
            un=self.unlocks,
            ms=self.missions,
            device_id=self.device_id,
            user_id=self.user_id,
        )
        codes = {
            "validity-hash-for-int": "integrity.integer_mismatch",
            "direct-md5-empty-sentinel": "integrity.direct_mismatch",
            "device-user-bound-md5": "integrity.identity_mismatch",
        }
        for result in report.results:
            if result.status not in {VerificationStatus.MISSING, VerificationStatus.MISMATCH}:
                continue
            self._integrity_blocked = True
            code = (
                "integrity.digest_missing"
                if result.status is VerificationStatus.MISSING
                else {
                    "un_k": "integrity.unlock_mismatch",
                    "ms_k": "integrity.mission_mismatch",
                }.get(result.key, codes.get(result.algorithm, "integrity.mismatch"))
            )
            self._diagnostics.append(
                Diagnostic(
                    code,
                    "warning",
                    "error.open_failed",
                    {"key": result.key, "area": self._friendly_integrity_area(result.source)},
                )
            )

    def _detect_version_profile(self) -> None:
        reasons: list[str] = []
        if self.preferences is not None:
            # Missing digests are repairable integrity failures, not a different format.
            required = {"fr_v", "st_v", "cs_v", "p_v", "s_v", "wu_v", "ac_v"}
            missing = sorted(required - set(self.preferences.keys()))
            if missing:
                reasons.append(f"preferences signature is missing {len(missing)} fields")
        if self.missions is not None:
            expected = {mission.id for mission in self.catalog.missions}
            actual = set(self.missions.keys())
            if actual - expected:
                reasons.append("mission catalog contains entries unknown to 7.0.255c")
        if self.unlocks is not None:
            known_persisted_types = {0, 3, 101, 102, 103, 107, 109, 110, 112, 114, 115}
            for key in self.unlocks.keys():
                parts = key.split("|")
                if len(parts) < 3:
                    reasons.append("unlock progress contains an unknown key layout")
                    break
                try:
                    condition_type = int(parts[2])
                except ValueError:
                    reasons.append("unlock progress contains a non-numeric condition type")
                    break
                if condition_type not in known_persisted_types:
                    reasons.append("unlock progress contains a condition type unknown to 7.0.255c")
                    break
        if self.score_database is not None and self.score_database.schema_version != 4:
            reasons.append(
                f"score database schema is v{self.score_database.schema_version}, expected v4"
            )
        if reasons:
            self._profile_mismatch = True
            severity = "warning" if self.request.allow_version_override else "error"
            self._profile_diagnostics.extend(
                Diagnostic(
                    "version.profile_mismatch",
                    severity,
                    "error.unsupported_format",
                    {"reason": reason, "profile": self.request.game_version},
                )
                for reason in reasons
            )

    @staticmethod
    def _friendly_integrity_area(source: str) -> str:
        return {
            "fr_v": "Fragments",
            "st_v": "Story",
            "cs_v": "Score grades",
            "fin_v": "Axiom of the End",
            "fc_v": "Favorite Partners",
            "fs_v": "Favorite songs",
        }.get(source, "Partner skill progress")

    def _snapshot(self) -> _Snapshot:
        return _Snapshot(
            self.preferences.to_bytes() if self.preferences else None,
            self.unlocks.to_bytes() if self.unlocks else None,
            self.missions.to_bytes() if self.missions else None,
            self.score_database.scores if self.score_database else None,
            self.score_database.clears if self.score_database else None,
            self._repair_requested,
        )

    def _restore(self, snapshot: _Snapshot) -> None:
        if snapshot.preferences is not None:
            self.preferences = SharedPrefsDocument.from_bytes(
                snapshot.preferences,
                source_path=self.sources.get(SaveKind.PREFERENCES),
            )
        if snapshot.unlocks is not None:
            self.unlocks = PlistMapDocument.from_bytes(
                snapshot.unlocks,
                source_path=self.sources.get(SaveKind.UNLOCK_PROGRESS),
            )
        if snapshot.missions is not None:
            self.missions = PlistMapDocument.from_bytes(
                snapshot.missions,
                source_path=self.sources.get(SaveKind.MISSION_PROGRESS),
            )
        if (
            self.score_database is not None
            and snapshot.scores is not None
            and snapshot.clears is not None
        ):
            self.score_database.restore(snapshot.scores, snapshot.clears)
        self._repair_requested = snapshot.repair_requested

    def inspect(self, query: BrowseQuery | None = None) -> WorkspaceView:
        self._ensure_open()
        query = query or BrowseQuery()
        sections = self._sections(query)
        return WorkspaceView(
            revision=self.revision,
            game_version=self.request.game_version,
            sources=dict(self.sources),
            sections=tuple(sections),
            diagnostics=tuple(self._diagnostics),
            dirty=self.dirty,
            can_undo=bool(self._history),
            can_redo=bool(self._redo),
        )

    def _sections(self, query: BrowseQuery) -> list[SectionView]:
        builders = {
            Section.SETTINGS: self._settings_view,
            Section.FRAGMENTS: self._fragments_view,
            Section.OWNERSHIP: self._ownership_view,
            Section.PARTNERS: self._partners_view,
            Section.FAVORITES: self._favorites_view,
            Section.STORY: self._story_view,
            Section.CHARACTER_SKILLS: self._skills_view,
            Section.FINALE: self._finale_view,
            Section.UNLOCKS: self._unlocks_view,
            Section.MISSIONS: self._missions_view,
            Section.SCORES: self._scores_view,
            Section.ACCOUNT_DIAGNOSTICS: self._account_view,
        }
        if query.section is not None:
            return [builders[query.section](query)]
        return [
            builder(BrowseQuery(section=section, limit=0)) for section, builder in builders.items()
        ]

    def _availability(self, required: SaveKind) -> Availability:
        if required not in self.sources:
            return Availability.NEEDS_SOURCE
        if self._profile_mismatch and not self.request.allow_version_override:
            return Availability.READ_ONLY
        return Availability.ENABLED

    @staticmethod
    def _paginate(items: list[ItemView], query: BrowseQuery) -> tuple[ItemView, ...]:
        if query.search:
            needle = query.search.casefold()
            items = [
                item
                for item in items
                if needle in item.label.casefold()
                or needle in item.id.casefold()
                or needle in item.subtitle.casefold()
                or any(needle in tag.casefold() for tag in item.tags)
            ]
        if query.limit <= 0:
            return ()
        return tuple(items[query.offset : query.offset + query.limit])

    def _section(
        self,
        section: Section,
        availability: Availability,
        items: list[ItemView],
        query: BrowseQuery,
        blocked_by: Iterable[str] = (),
    ) -> SectionView:
        filtered_total = len(items)
        if query.search:
            needle = query.search.casefold()
            filtered_total = sum(
                needle in item.label.casefold()
                or needle in item.id.casefold()
                or needle in item.subtitle.casefold()
                or any(needle in tag.casefold() for tag in item.tags)
                for item in items
            )
        return SectionView(
            section,
            f"section.{section.value}",
            availability,
            self._paginate(items, query),
            filtered_total,
            tuple(blocked_by),
        )

    def _settings_view(self, query: BrowseQuery) -> SectionView:
        availability = self._availability(SaveKind.PREFERENCES)
        items: list[ItemView] = []
        if self.preferences:
            for key, (label_id, editor) in SETTING_FIELDS.items():
                if key not in self.preferences:
                    continue
                if self.request.locale == "zh-Hans" and key in {"songSort", "songOrder"}:
                    labels = {
                        "TITLE": "标题",
                        "DIFFICULTY": "难度",
                        "GRADE": "评级",
                        "DATE": "日期",
                        "CLEARTYPE": "通关类型",
                        "ASCENDING": "升序",
                        "DESCENDING": "降序",
                    }
                    editor = replace(
                        editor,
                        choices=tuple(
                            (value, labels.get(str(value), label))
                            for value, label in editor.choices
                        ),
                    )
                items.append(
                    ItemView(
                        f"setting:{key}",
                        self._label(label_id),
                        fields=(FieldView("value", label_id, self.preferences.get(key), editor),),
                    )
                )
        return self._section(Section.SETTINGS, availability, items, query)

    def _fragments_view(self, query: BrowseQuery) -> SectionView:
        availability = self._availability(SaveKind.PREFERENCES)
        items: list[ItemView] = []
        if self.preferences and "fr_v" in self.preferences:
            items.append(
                ItemView(
                    "fragments",
                    self._label("section.fragments"),
                    fields=(
                        FieldView(
                            "amount",
                            "field.amount",
                            self.preferences.get("fr_v"),
                            EditorSpec(
                                "integer",
                                minimum=0,
                                maximum=99_999,
                                expert_minimum=-(2**31),
                                expert_maximum=2**31 - 1,
                            ),
                        ),
                    ),
                )
            )
        return self._section(Section.FRAGMENTS, availability, items, query)

    def _ownership_view(self, query: BrowseQuery) -> SectionView:
        availability = self._availability(SaveKind.PREFERENCES)
        blocked: list[str] = []
        if availability is Availability.ENABLED and not self.device_id:
            availability = Availability.NEEDS_CONTEXT
            blocked.append("Android device ID")
        items: list[ItemView] = []
        if self.preferences:
            owned_packs = set(self._csv("p_v"))
            for pack in self.catalog.packs:
                label = pack.name.resolve(self.request.locale, default=pack.id) or self._pretty(
                    pack.id
                )
                items.append(
                    ItemView(
                        f"pack:{pack.id}",
                        label,
                        "Song pack",
                        (
                            FieldView(
                                "owned",
                                "field.available",
                                pack.id in owned_packs,
                                EditorSpec("boolean"),
                            ),
                        ),
                        tags=(pack.id,),
                    )
                )
            singles = set(self._csv("s_v"))
            for song in self.catalog.songs:
                if not song.purchase_id:
                    continue
                label = song.title.resolve(self.request.locale, default=song.id)
                items.append(
                    ItemView(
                        f"single:{song.id}",
                        label,
                        "Memory Archive / Single",
                        (
                            FieldView(
                                "owned",
                                "field.available",
                                song.id in singles,
                                EditorSpec("boolean"),
                            ),
                        ),
                        tags=(song.id, song.artist or ""),
                    )
                )
            world = set(self._csv("wu_v"))
            for song in self.catalog.songs:
                if not song.world_unlock:
                    continue
                label = song.title.resolve(self.request.locale, default=song.id)
                items.append(
                    ItemView(
                        f"world-song:{song.id}",
                        label,
                        "World Mode song",
                        (
                            FieldView(
                                "unlocked",
                                "field.unlocked",
                                song.id in world,
                                EditorSpec("boolean"),
                            ),
                        ),
                        tags=(song.id,),
                    )
                )
            for chart in self.catalog.charts:
                if chart.difficulty.value != 3:
                    continue
                song = self.catalog.song(chart.song_id)
                label = song.title.resolve(self.request.locale, default=song.id)
                token = f"{song.id}3"
                items.append(
                    ItemView(
                        f"world-beyond:{song.id}",
                        f"{label} · BYD",
                        "Beyond difficulty",
                        (
                            FieldView(
                                "unlocked", "field.unlocked", token in world, EditorSpec("boolean")
                            ),
                        ),
                        tags=(song.id, "BYD"),
                    )
                )
        return self._section(Section.OWNERSHIP, availability, items, query, blocked)

    def _partners_view(self, query: BrowseQuery) -> SectionView:
        availability = self._availability(SaveKind.PREFERENCES)
        items: list[ItemView] = []
        if self.preferences:
            available = {int(value) for value in self._csv("ac_v") if value.lstrip("-").isdigit()}
            favorites = {int(value) for value in self._csv("fc_v") if value.lstrip("-").isdigit()}
            selected = self.preferences.get("ch")
            for partner in self.catalog.partners:
                fields = (
                    FieldView(
                        "available",
                        "field.available",
                        partner.id in available,
                        EditorSpec("boolean"),
                    ),
                    FieldView(
                        "favorite", "field.favorite", partner.id in favorites, EditorSpec("boolean")
                    ),
                    FieldView(
                        "selected", "field.selected", partner.id == selected, EditorSpec("boolean")
                    ),
                )
                items.append(
                    ItemView(
                        f"partner:{partner.id}",
                        self._partner_name(partner.name),
                        subtitle=self._partner_description(partner.id),
                        fields=fields,
                        tags=partner.search_terms + (partner.name,),
                    )
                )
        return self._section(Section.PARTNERS, availability, items, query)

    def _favorites_view(self, query: BrowseQuery) -> SectionView:
        availability = self._availability(SaveKind.PREFERENCES)
        items: list[ItemView] = []
        if self.preferences:
            favorites = set(self._csv("fs_v"))
            for song in self.catalog.songs:
                if song.deleted:
                    continue
                label = song.title.resolve(self.request.locale, default=song.id)
                items.append(
                    ItemView(
                        f"favorite-song:{song.id}",
                        label,
                        song.artist or "",
                        (
                            FieldView(
                                "favorite",
                                "field.favorite",
                                song.id in favorites,
                                EditorSpec("boolean"),
                            ),
                        ),
                        tags=(song.id, song.artist or ""),
                    )
                )
        return self._section(Section.FAVORITES, availability, items, query)

    def _story_view(self, query: BrowseQuery) -> SectionView:
        availability = self._availability(SaveKind.PREFERENCES)
        items: list[ItemView] = []
        if self.preferences:
            state = self._story_states()
            for entry in self.catalog.story_entries:
                key = (str(entry.key.main_id), int(entry.key.minor_id))
                completed, read = state.get(key, (False, False))
                route = entry.route.replace("_", " ").title()
                label = f"{route} · {entry.key.main_id}-{entry.key.minor_id}"
                items.append(
                    ItemView(
                        f"story:{entry.key.main_id}:{entry.key.minor_id}",
                        label,
                        fields=(
                            FieldView(
                                "completed", "field.completed", completed, EditorSpec("boolean")
                            ),
                            FieldView("read", "field.read", read, EditorSpec("boolean")),
                        ),
                        tags=(route, str(entry.key.main_id), str(entry.key.minor_id)),
                    )
                )
        return self._section(Section.STORY, availability, items, query)

    def _skills_view(self, query: BrowseQuery) -> SectionView:
        availability = self._availability(SaveKind.PREFERENCES)
        items: list[ItemView] = []
        if self.preferences:
            for key, (label, minimum, maximum) in SKILL_FIELDS.items():
                if key not in self.preferences:
                    continue
                items.append(
                    ItemView(
                        f"skill:{key}",
                        self._skill_label(key, label),
                        subtitle=self._local(
                            "Saved skill counter, not the Partner level. Changes remain a draft.",
                            "这是技能触发或累计进度，不是搭档等级；修改只会暂存于草稿。",
                        ),
                        fields=(
                            FieldView(
                                "progress",
                                "field.progress",
                                self.preferences.get(key),
                                EditorSpec(
                                    "integer",
                                    minimum=minimum,
                                    maximum=maximum,
                                    expert_minimum=-(2**31),
                                    expert_maximum=2**31 - 1,
                                ),
                            ),
                        ),
                    )
                )
        return self._section(Section.CHARACTER_SKILLS, availability, items, query)

    def _finale_view(self, query: BrowseQuery) -> SectionView:
        availability = self._availability(SaveKind.PREFERENCES)
        items: list[ItemView] = []
        if self.preferences:
            value = str(self.preferences.get("fin_v", ""))
            count = len(value.split("|")) if value else 0
            sentinel = value.rsplit("|", 1)[-1] if value else ""
            items.append(
                ItemView(
                    "finale-status",
                    self._local("Axiom of the End progress", "Axiom of the End 进度"),
                    self._local(
                        "Only the trailing 1337 marker is checked, not the full layout. "
                        "Indexed entries below retain raw values; their meanings are not inferred.",
                        "仅检查末尾 1337 标记，不代表完整结构或游戏进度有效。"
                        "下方按索引展示原始分段，不推测每个值的含义。",
                    ),
                    (
                        FieldView(
                            "layout",
                            "field.marker",
                            self._local("Present", "正常")
                            if sentinel == "1337"
                            else self._local("Missing / abnormal", "缺失或异常"),
                            EditorSpec("text"),
                            availability=Availability.READ_ONLY,
                        ),
                        FieldView(
                            "count",
                            "field.entry_count",
                            count,
                            EditorSpec("integer"),
                            Availability.READ_ONLY,
                        ),
                        FieldView(
                            "raw",
                            "field.raw_value",
                            value,
                            EditorSpec("text"),
                            Availability.READ_ONLY,
                            detail_only=True,
                        ),
                        FieldView(
                            "entries",
                            "field.indexed_values",
                            "\n".join(
                                f"[{i}]  {part}"
                                for i, part in enumerate(value.split("|") if value else [])
                            ),
                            EditorSpec("text"),
                            Availability.READ_ONLY,
                            detail_only=True,
                        ),
                    ),
                    availability=Availability.READ_ONLY,
                )
            )
        return self._section(
            Section.FINALE, Availability.READ_ONLY if items else availability, items, query
        )

    def _unlocks_view(self, query: BrowseQuery) -> SectionView:
        if self.unlocks is None:
            return self._section(
                Section.UNLOCKS, Availability.NEEDS_SOURCE, [], query, ("Song unlock progress",)
            )
        editable = self.preferences is not None and not (
            self._profile_mismatch and not self.request.allow_version_override
        )
        items: list[ItemView] = []
        self._unlock_handles.clear()
        for key, value in self.unlocks.items():
            parts = key.split("|")
            if len(parts) < 3:
                continue
            try:
                difficulty = int(parts[1])
                condition_type = int(parts[2])
            except ValueError:
                continue
            target = parts[0]
            try:
                song = self.catalog.song(target)
                song_label = song.title.resolve(self.request.locale, default=target)
            except Exception:
                if condition_type != 102:
                    continue
                base = target.removesuffix("_challenge")
                match = re.fullmatch(r"(.+)Challenge(\d+)", base)
                song_id = match[1] if match else base
                try:
                    name = self.catalog.song(song_id).title.resolve(
                        self.request.locale, default=song_id
                    )
                    song_label = name + self._local(" · Challenge", " · 挑战")
                    if match:
                        song_label += f" #{match[2]}"
                except Exception:
                    song_label = self._local("Unmapped challenge", "未映射挑战")
                song_label += f" [{target}]"
            type_label = self._local(
                *UNLOCK_TYPE_LABELS.get(condition_type, ("Unlock progress", "解锁进度"))
            )
            difficulty_name = (
                ("PST", "PRS", "FTR", "BYD", "ETR")[difficulty]
                if difficulty in range(5)
                else str(difficulty)
            )
            handle = f"unlock:{hashlib.sha1(key.encode('utf-8')).hexdigest()[:16]}"
            self._unlock_handles[handle] = key
            field_availability = Availability.ENABLED if editable else Availability.READ_ONLY
            editor = EditorSpec(
                "integer",
                minimum=0,
                maximum=self._unlock_maximum(condition_type),
                expert_minimum=-(2**31),
                expert_maximum=2**31 - 1,
            )
            items.append(
                ItemView(
                    handle,
                    f"{song_label} · {difficulty_name}",
                    type_label
                    + self._local("\nSave key: ", "\n存档键：")
                    + key
                    + self._local(
                        "\nTarget / difficulty code / condition type; unmapped IDs are kept "
                        "verbatim, not assigned a guessed task description.",
                        "\n键格式：目标 ID / 难度码 / 条件类型。未映射 ID 原样保留，"
                        "不猜测具体任务条件。",
                    ),
                    (
                        FieldView(
                            "progress", "field.progress", value.value, editor, field_availability
                        ),
                    ),
                    availability=field_availability,
                    tags=(song_label, difficulty_name, type_label, target, key),
                )
            )
        availability = Availability.ENABLED if editable else Availability.READ_ONLY
        blocked = (
            ()
            if editable
            else ("Game settings & progress is required to update the integrity value",)
        )
        return self._section(Section.UNLOCKS, availability, items, query, blocked)

    @staticmethod
    def _unlock_maximum(condition_type: int) -> int:
        return {
            0: 1,
            101: 100,
            103: 1,
            109: 3,
            112: 999,
        }.get(condition_type, 2**31 - 1)

    def _missions_view(self, query: BrowseQuery) -> SectionView:
        if self.missions is None:
            return self._section(
                Section.MISSIONS, Availability.NEEDS_SOURCE, [], query, ("Mission rewards",)
            )
        editable = self.preferences is not None and not (
            self._profile_mismatch and not self.request.allow_version_override
        )
        items: list[ItemView] = []
        for mission in self.catalog.missions:
            claimed = self.missions.get(mission.id) == "claimed"
            label = self._mission_label(mission.id)
            availability = Availability.ENABLED if editable else Availability.READ_ONLY
            items.append(
                ItemView(
                    f"mission:{mission.id}",
                    label,
                    f"Tier {mission.tier}",
                    (
                        FieldView(
                            "claimed", "field.claimed", claimed, EditorSpec("boolean"), availability
                        ),
                    ),
                    availability=availability,
                    tags=(mission.id, str(mission.tier)),
                )
            )
        availability = Availability.ENABLED if editable else Availability.READ_ONLY
        blocked = (
            ()
            if editable
            else ("Game settings & progress is required to update the integrity value",)
        )
        return self._section(Section.MISSIONS, availability, items, query, blocked)

    def _scores_view(self, query: BrowseQuery) -> SectionView:
        availability = self._availability(SaveKind.SCORE_DATABASE)
        items: list[ItemView] = []
        if self.score_database:
            clears = {record.key: record for record in self.score_database.clears}
            for record in self.score_database.scores:
                try:
                    song = self.catalog.song(record.key.song_id)
                    song_label = song.title.resolve(self.request.locale, default=record.key.song_id)
                except Exception:
                    song_label = "Custom chart"
                difficulty = (
                    ("PST", "PRS", "FTR", "BYD", "ETR")[record.key.difficulty]
                    if record.key.difficulty in range(5)
                    else str(record.key.difficulty)
                )
                clear = clears.get(record.key)
                fields = (
                    FieldView(
                        "score",
                        "field.score",
                        record.score,
                        EditorSpec("integer"),
                        Availability.READ_ONLY,
                        derived=True,
                    ),
                    FieldView(
                        "shiny_pure",
                        "field.shiny_pure",
                        record.shiny_perfect_count,
                        EditorSpec("integer", minimum=0),
                    ),
                    FieldView(
                        "pure", "field.pure", record.perfect_count, EditorSpec("integer", minimum=0)
                    ),
                    FieldView(
                        "far", "field.far", record.near_count, EditorSpec("integer", minimum=0)
                    ),
                    FieldView(
                        "lost", "field.lost", record.miss_count, EditorSpec("integer", minimum=0)
                    ),
                    FieldView(
                        "clear_type",
                        "field.clear_type",
                        clear.clear_type if clear else 0,
                        EditorSpec("choice", choices=CLEAR_TYPES),
                    ),
                    FieldView(
                        "preset",
                        "field.value",
                        "keep",
                        EditorSpec(
                            "choice",
                            choices=(
                                ("full_recall", "Full Recall"),
                                ("pure_memory", "Pure Memory"),
                            ),
                        ),
                    ),
                )
                item_id = (
                    f"score:{record.key.song_id}:{record.key.difficulty}:{record.key.control_type}"
                )
                items.append(
                    ItemView(
                        item_id,
                        f"{song_label} · {difficulty} · CT {record.key.control_type}",
                        f"{record.score:,}",
                        fields,
                        tags=(record.key.song_id, difficulty, song_label),
                    )
                )
        return self._section(Section.SCORES, availability, items, query)

    def _account_view(self, query: BrowseQuery) -> SectionView:
        availability = self._availability(SaveKind.PREFERENCES)
        items: list[ItemView] = []
        if self.preferences:
            items.append(
                ItemView(
                    "account-player",
                    self._local("Player identity", "玩家内部 ID"),
                    fields=(
                        FieldView(
                            "player_id",
                            "field.value",
                            self._mask_identifier(self.user_id),
                            EditorSpec("text"),
                            Availability.READ_ONLY,
                            sensitive=True,
                        ),
                    ),
                    availability=Availability.READ_ONLY,
                )
            )
            if self.preferences.get("a_t"):
                items.append(
                    ItemView(
                        "account-token",
                        self._local("Authentication token", "认证令牌"),
                        "••••••••",
                        (
                            FieldView(
                                "masked",
                                "field.value",
                                "••••••••",
                                EditorSpec("text"),
                                Availability.READ_ONLY,
                                sensitive=True,
                            ),
                        ),
                        Availability.READ_ONLY,
                    )
                )
        return self._section(
            Section.ACCOUNT_DIAGNOSTICS,
            Availability.READ_ONLY if items else availability,
            items,
            query,
        )

    def inspect_account_detail(self, item_id: str) -> ItemView:
        """Explicit local reveal; ordinary browse views remain redacted."""
        self._ensure_open()
        view = self._account_view(BrowseQuery(Section.ACCOUNT_DIAGNOSTICS))
        item = next((item for item in view.items if item.id == item_id), None)
        if item is None or self.preferences is None:
            raise InvalidEdit("unknown account record")
        value = self.user_id if item_id == "account-player" else self.preferences.get("a_t", "")
        return replace(item, fields=(replace(item.fields[0], value=value, sensitive=False),))

    def perform(self, operation: Operation) -> OperationReport:
        self._ensure_open()
        expected = getattr(operation, "expected_revision", None)
        if expected is not None and expected != self.revision:
            raise StaleRevision(expected=expected, actual=self.revision)
        if isinstance(operation, Undo):
            return self._undo()
        if isinstance(operation, Redo):
            return self._redo_operation()
        if isinstance(operation, SetDeviceIdentity):
            if not operation.device_id.strip():
                raise InvalidEdit("device ID must not be empty")
            if operation.user_id is not None and (
                isinstance(operation.user_id, bool)
                or not isinstance(operation.user_id, int)
                or operation.user_id < 0
            ):
                raise InvalidEdit("user ID must be a non-negative integer")
            previous_identity = (self.device_id, self.user_id)
            self.device_id = operation.device_id
            if operation.user_id is not None:
                self.user_id = int(operation.user_id)
            elif self.user_id is None:
                self.user_id = self._resolve_user_id()
            self._diagnostics.clear()
            self._integrity_blocked = False
            self._validate_open_state()
            self._diagnostics[:0] = self._profile_diagnostics
            if any(item.code == "integrity.identity_mismatch" for item in self._diagnostics):
                self.device_id, self.user_id = previous_identity
                self._diagnostics.clear()
                self._integrity_blocked = False
                self._validate_open_state()
                self._diagnostics[:0] = self._profile_diagnostics
                raise InvalidEdit("device identity does not match the save")
            self.revision += 1
            return OperationReport(self.revision, ())
        if self._profile_mismatch and not self.request.allow_version_override:
            raise CapabilityUnavailable(reason="version profile mismatch")
        if isinstance(operation, RepairIntegrity):
            if self._repair_requested:
                return OperationReport(self.revision, ())
            before = self._snapshot()
            self._repair_requested = True
            after = self._snapshot()
            change = SemanticChange(
                Section.ACCOUNT_DIAGNOSTICS,
                "Save integrity",
                "field.value",
                "Needs repair",
                "Repair scheduled",
                derived=True,
            )
            self._history.append(_HistoryEntry(before, after, (change,)))
            self._redo.clear()
            self.revision += 1
            return OperationReport(self.revision, (change,))
        if self._integrity_blocked and not self._repair_requested:
            raise CapabilityUnavailable(reason="integrity repair must be confirmed first")
        if isinstance(operation, SetField):
            return self._perform_set(operation)
        if isinstance(operation, BatchSet):
            return self._perform_batch(operation)
        raise TypeError(f"unsupported operation: {type(operation).__name__}")

    def _perform_set(self, operation: SetField) -> OperationReport:
        before = self._snapshot()
        try:
            changes = tuple(self._apply_field(operation))
        except Exception:
            self._restore(before)
            raise
        after = self._snapshot()
        if before == after:
            return OperationReport(self.revision, ())
        entry = _HistoryEntry(before, after, changes)
        self._history.append(entry)
        self._redo.clear()
        self.revision += 1
        return OperationReport(self.revision, changes)

    def _perform_batch(self, operation: BatchSet) -> OperationReport:
        before = self._snapshot()
        changes: list[SemanticChange] = []
        try:
            for change in sorted(operation.changes, key=self._batch_order):
                if change.expected_revision not in {None, self.revision}:
                    raise StaleRevision(expected=change.expected_revision, actual=self.revision)
                changes.extend(self._apply_field(change))
        except Exception:
            self._restore(before)
            raise
        after = self._snapshot()
        if before == after:
            return OperationReport(self.revision, ())
        entry = _HistoryEntry(before, after, tuple(changes))
        self._history.append(entry)
        self._redo.clear()
        self.revision += 1
        return OperationReport(self.revision, tuple(changes))

    def _undo(self) -> OperationReport:
        if not self._history:
            return OperationReport(self.revision, ())
        entry = self._history.pop()
        self._restore(entry.before)
        self._redo.append(entry)
        self.revision += 1
        reversed_changes = tuple(
            replace(change, before=change.after, after=change.before)
            for change in reversed(entry.changes)
        )
        return OperationReport(self.revision, reversed_changes)

    def _redo_operation(self) -> OperationReport:
        if not self._redo:
            return OperationReport(self.revision, ())
        entry = self._redo.pop()
        self._restore(entry.after)
        self._history.append(entry)
        self.revision += 1
        return OperationReport(self.revision, entry.changes)

    def _batch_order(self, operation: SetField) -> int:
        # Avoid invalid intermediate shiny-PURE > PURE states in a multi-field form.
        if self.score_database and operation.item_id.startswith("score:"):
            _, song, difficulty, control = operation.item_id.split(":", 3)
            record = self.score_database.get_score(ChartKey(song, int(difficulty), int(control)))
            if record and isinstance(operation.value, int):
                if operation.field_id == "pure" and operation.value >= record.perfect_count:
                    return -1
                if (
                    operation.field_id == "shiny_pure"
                    and operation.value <= record.shiny_perfect_count
                ):
                    return -1
        return 0

    def _apply_field(self, operation: SetField) -> list[SemanticChange]:
        return [
            replace(change, item_id=operation.item_id)
            for change in self._apply_field_value(operation)
        ]

    def _apply_field_value(self, operation: SetField) -> list[SemanticChange]:
        item, field, value = operation.item_id, operation.field_id, operation.value
        expert = operation.expert_override or self.request.expert
        if item.startswith("setting:"):
            return self._set_setting(item.split(":", 1)[1], value, expert)
        if item == "fragments":
            return self._set_pref_int(
                "fr_v", Section.FRAGMENTS, "Fragments", field, value, 0, 99_999, expert
            )
        if item.startswith("pack:"):
            self._require_identity()
            pack_id = item.split(":", 1)[1]
            try:
                self.catalog.pack(pack_id)
            except LookupError as exc:
                raise InvalidEdit("unknown song pack") from exc
            return self._toggle_list(
                "p_v",
                pack_id,
                self._require_boolean(value),
                Section.OWNERSHIP,
                self._pack_label(pack_id),
                field,
            )
        if item.startswith("single:"):
            self._require_identity()
            song_id = item.split(":", 1)[1]
            try:
                song = self.catalog.song(song_id)
            except LookupError as exc:
                raise InvalidEdit("unknown song") from exc
            if not song.purchase_id:
                raise InvalidEdit("the song is not a separately owned song")
            return self._toggle_list(
                "s_v",
                song_id,
                self._require_boolean(value),
                Section.OWNERSHIP,
                self._song_label(song_id),
                field,
            )
        if item.startswith("world-song:"):
            self._require_identity()
            song_id = item.split(":", 1)[1]
            try:
                song = self.catalog.song(song_id)
            except LookupError as exc:
                raise InvalidEdit("unknown song") from exc
            if not song.world_unlock:
                raise InvalidEdit("the song is not unlocked through World Mode")
            return self._toggle_list(
                "wu_v",
                song_id,
                self._require_boolean(value),
                Section.OWNERSHIP,
                self._song_label(song_id),
                field,
            )
        if item.startswith("world-beyond:"):
            self._require_identity()
            song_id = item.split(":", 1)[1]
            try:
                self.catalog.chart(song_id, 3)
            except LookupError as exc:
                raise InvalidEdit("unknown Beyond chart") from exc
            return self._toggle_list(
                "wu_v",
                song_id + "3",
                self._require_boolean(value),
                Section.OWNERSHIP,
                self._song_label(song_id) + " · BYD",
                field,
            )
        if item.startswith("partner:"):
            return self._set_partner(
                int(item.split(":", 1)[1]), field, self._require_boolean(value)
            )
        if item.startswith("favorite-song:"):
            song_id = item.split(":", 1)[1]
            try:
                song = self.catalog.song(song_id)
            except LookupError as exc:
                raise InvalidEdit("unknown song") from exc
            if song.deleted:
                raise InvalidEdit("a deleted song cannot be added to favorites")
            return self._toggle_list(
                "fs_v",
                song_id,
                self._require_boolean(value),
                Section.FAVORITES,
                self._song_label(song_id),
                field,
            )
        if item.startswith("story:"):
            _, major, minor = item.split(":", 2)
            minor_value = int(minor)
            if not any(
                str(entry.key.main_id) == major and entry.key.minor_id == minor_value
                for entry in self.catalog.story_entries
            ):
                raise InvalidEdit("unknown Story entry")
            return self._set_story(major, minor_value, field, self._require_boolean(value))
        if item.startswith("skill:"):
            key = item.split(":", 1)[1]
            try:
                label, minimum, maximum = SKILL_FIELDS[key]
            except KeyError as exc:
                raise InvalidEdit("unknown Partner skill counter") from exc
            return self._set_pref_int(
                key,
                Section.CHARACTER_SKILLS,
                self._skill_label(key, label),
                field,
                value,
                minimum,
                maximum,
                expert,
            )
        if item.startswith("unlock:"):
            return self._set_unlock(item, field, value, expert)
        if item.startswith("mission:"):
            mission_id = item.split(":", 1)[1]
            if not any(mission.id == mission_id for mission in self.catalog.missions):
                raise InvalidEdit("unknown mission")
            return self._set_mission(mission_id, self._require_boolean(value))
        if item.startswith("score:"):
            return self._set_score(item, field, value, expert)
        raise InvalidEdit(f"unknown game item: {item}")

    def _set_setting(self, key: str, value: Any, expert: bool) -> list[SemanticChange]:
        if not self.preferences or key not in SETTING_FIELDS:
            raise CapabilityUnavailable()
        label_id, editor = SETTING_FIELDS[key]
        self._validate_editor(value, editor, expert)
        before = self.preferences.get(key)
        self.preferences.set(key, value)
        return [
            SemanticChange(
                Section.SETTINGS, self._label(label_id), label_id, before, self.preferences.get(key)
            )
        ]

    def _set_pref_int(
        self,
        key: str,
        section: Section,
        label: str,
        field: str,
        value: Any,
        minimum: int,
        maximum: int,
        expert: bool,
    ) -> list[SemanticChange]:
        if not self.preferences:
            raise CapabilityUnavailable()
        value = self._require_integer(value)
        allowed_minimum, allowed_maximum = (-(2**31), 2**31 - 1) if expert else (minimum, maximum)
        if not allowed_minimum <= value <= allowed_maximum:
            raise InvalidEdit(f"value must be between {allowed_minimum} and {allowed_maximum}")
        before = self.preferences.get(key)
        self.preferences.set(key, value)
        return [SemanticChange(section, label, f"field.{field}", before, value)]

    def _toggle_list(
        self,
        key: str,
        token: str,
        enabled: bool,
        section: Section,
        label: str,
        field: str,
        *,
        numeric_sort: bool = False,
    ) -> list[SemanticChange]:
        if not self.preferences:
            raise CapabilityUnavailable()
        values = self._csv(key)
        before = token in values
        if enabled and not before:
            values.append(token)
        elif not enabled and before:
            values = [item for item in values if item != token]
        if numeric_sort:
            values.sort(key=lambda item: int(item))
        self.preferences.set(key, ",".join(values))
        return [SemanticChange(section, label, f"field.{field}", before, enabled)]

    def _set_partner(self, partner_id: int, field: str, enabled: bool) -> list[SemanticChange]:
        label = self._partner_name(self.catalog.partner(partner_id).name)
        if field == "available":
            self._require_identity()
            return self._toggle_list(
                "ac_v", str(partner_id), enabled, Section.PARTNERS, label, field
            )
        if field == "favorite":
            if enabled and str(partner_id) not in self._csv("ac_v"):
                raise InvalidEdit("a favorite Partner must be available")
            return self._toggle_list(
                "fc_v", str(partner_id), enabled, Section.PARTNERS, label, field, numeric_sort=True
            )
        if field == "selected":
            if not enabled:
                raise InvalidEdit("choose another selected Partner instead")
            if str(partner_id) not in self._csv("ac_v"):
                raise InvalidEdit("the selected Partner must be available")
            assert self.preferences
            before = self.preferences.get("ch")
            self.preferences.set("ch", partner_id)
            return [SemanticChange(Section.PARTNERS, label, "field.enabled", before, partner_id)]
        raise InvalidEdit("unknown Partner field")

    def _set_story(self, major: str, minor: int, field: str, value: bool) -> list[SemanticChange]:
        if not self.preferences:
            raise CapabilityUnavailable()
        state = self._story_states()
        key = (major, minor)
        completed, read = state.get(key, (False, False))
        before = completed if field == "completed" else read
        if field == "completed":
            completed = value
        elif field == "read":
            read = value
        else:
            raise InvalidEdit("unknown Story field")
        state[key] = (completed, read)
        self.preferences.set(
            "st_v",
            ",".join(
                f"{major_id}|{minor_id}|{int(flags[0])}|{int(flags[1])}"
                for (major_id, minor_id), flags in state.items()
            ),
        )
        label = f"Story {major}-{minor}"
        return [SemanticChange(Section.STORY, label, f"field.{field}", before, value)]

    def _set_unlock(
        self, handle: str, field: str, value: Any, expert: bool
    ) -> list[SemanticChange]:
        if self.unlocks is None or self.preferences is None:
            raise CapabilityUnavailable()
        key = self._unlock_handles.get(handle)
        if key is None:
            # A handle remains resolvable even if inspect pagination changed.
            for candidate in self.unlocks.keys():
                if f"unlock:{hashlib.sha1(candidate.encode('utf-8')).hexdigest()[:16]}" == handle:
                    key = candidate
                    break
        if key is None:
            raise InvalidEdit("stale unlock handle")
        parts = key.split("|")
        condition_type = int(parts[2])
        value = self._require_integer(value)
        maximum = self._unlock_maximum(condition_type)
        allowed_minimum, allowed_maximum = (-(2**31), 2**31 - 1) if expert else (0, maximum)
        if not allowed_minimum <= value <= allowed_maximum:
            raise InvalidEdit(f"progress must be between {allowed_minimum} and {allowed_maximum}")
        before = self.unlocks.get(key)
        self.unlocks.set(key, value, kind="integer")
        return [
            SemanticChange(
                Section.UNLOCKS,
                self._local(
                    *UNLOCK_TYPE_LABELS.get(condition_type, ("Unlock progress", "解锁进度"))
                )
                + f" · {parts[0]} · {parts[1]}",
                "field.progress",
                before,
                value,
            )
        ]

    def _set_mission(self, mission_id: str, claimed: bool) -> list[SemanticChange]:
        if self.missions is None or self.preferences is None:
            raise CapabilityUnavailable()
        before = self.missions.get(mission_id) == "claimed"
        if claimed:
            self.missions.set(mission_id, "claimed", kind="string")
        else:
            self.missions.delete(mission_id)
        return [
            SemanticChange(
                Section.MISSIONS, self._mission_label(mission_id), "field.claimed", before, claimed
            )
        ]

    def _set_score(self, item: str, field: str, value: Any, expert: bool) -> list[SemanticChange]:
        if self.score_database is None:
            raise CapabilityUnavailable()
        _, song_id, difficulty, control_type = item.split(":", 3)
        key = ChartKey(song_id, int(difficulty), int(control_type))
        record = self.score_database.get_score(key)
        if record is None:
            raise InvalidEdit("score record no longer exists")
        label = (
            self._song_label(song_id)
            + " · "
            + (
                ("PST", "PRS", "FTR", "BYD", "ETR")[key.difficulty]
                if key.difficulty in range(5)
                else str(key.difficulty)
            )
            + f" · CT {key.control_type}"
        )
        changes: list[SemanticChange] = []
        if field == "clear_type":
            before = self.score_database.get_clear(key)
            before_value = before.clear_type if before else 0
            clear_type = self._require_integer(value)
            self.score_database.set_clear_type(key, clear_type, expert=expert)
            changes.append(
                SemanticChange(Section.SCORES, label, "field.clear_type", before_value, clear_type)
            )
            return changes
        if field == "preset":
            total = record.perfect_count + record.near_count + record.miss_count
            if total <= 0:
                raise InvalidEdit("a preset requires a non-zero note count")
            if value == "pure_memory":
                updated = replace(
                    record,
                    perfect_count=total,
                    near_count=0,
                    miss_count=0,
                    shiny_perfect_count=min(record.shiny_perfect_count, total),
                )
                updated = replace(
                    updated,
                    score=calculate_score(updated.perfect_count, 0, 0, updated.shiny_perfect_count),
                )
                self.score_database.set_score(updated)
                self.score_database.set_clear_type(key, 3)
            elif value == "full_recall":
                updated = replace(
                    record,
                    near_count=record.near_count + record.miss_count,
                    miss_count=0,
                )
                updated = replace(
                    updated,
                    score=calculate_score(
                        updated.perfect_count, updated.near_count, 0, updated.shiny_perfect_count
                    ),
                )
                self.score_database.set_score(updated)
                self.score_database.set_clear_type(key, 2)
            else:
                raise InvalidEdit("unknown score preset")
            changes.append(SemanticChange(Section.SCORES, label, "field.value", "current", value))
            self._sync_grade_cache(updated)
            return changes
        field_map = {
            "shiny_pure": "shiny_perfect_count",
            "pure": "perfect_count",
            "far": "near_count",
            "lost": "miss_count",
        }
        attribute = field_map.get(field)
        if attribute is None:
            raise InvalidEdit("score is derived from judgement counts")
        numeric = self._require_integer(value)
        if not 0 <= numeric <= 2**31 - 1:
            raise InvalidEdit("judgement counts must be between 0 and 2147483647")
        before = getattr(record, attribute)
        if attribute == "shiny_perfect_count":
            updated = replace(record, shiny_perfect_count=numeric)
        elif attribute == "perfect_count":
            updated = replace(record, perfect_count=numeric)
        elif attribute == "near_count":
            updated = replace(record, near_count=numeric)
        else:
            updated = replace(record, miss_count=numeric)
        updated = replace(
            updated,
            score=calculate_score(
                updated.perfect_count,
                updated.near_count,
                updated.miss_count,
                updated.shiny_perfect_count,
            ),
        )
        self.score_database.set_score(updated, expert=expert)
        self._sync_grade_cache(updated)
        changes.append(SemanticChange(Section.SCORES, label, f"field.{field}", before, numeric))
        if updated.score != record.score:
            changes.append(
                SemanticChange(
                    Section.SCORES, label, "field.score", record.score, updated.score, derived=True
                )
            )
        return changes

    def _sync_grade_cache(self, record: ScoreRecord) -> None:
        if not self.preferences or "cs_v" not in self.preferences:
            return
        raw = str(self.preferences.get("cs_v", ""))
        records: dict[tuple[str, int], int] = {}
        order: list[tuple[str, int]] = []
        opaque: list[str] = []
        for token in filter(None, raw.split(",")):
            parts = token.split("|")
            if len(parts) != 3:
                opaque.append(token)
                continue
            try:
                key = (parts[0], int(parts[1]))
                grade = int(parts[2])
            except ValueError:
                opaque.append(token)
                continue
            if key not in records:
                order.append(key)
            records[key] = grade
        key = (record.key.song_id, record.key.difficulty)
        if key not in records:
            order.append(key)
        records[key] = grade_for_score(record.score)
        order.sort(key=lambda item: (item[0], item[1]))
        rendered = [
            f"{song}|{difficulty}|{records[(song, difficulty)]}" for song, difficulty in order
        ]
        rendered.extend(opaque)
        self.preferences.set("cs_v", ",".join(rendered))

    def _require_identity(self) -> None:
        if not self.device_id or self.user_id is None:
            raise CapabilityUnavailable(missing="device identity")

    @staticmethod
    def _validate_editor(value: Any, editor: EditorSpec, expert: bool) -> None:
        if editor.kind == "boolean" and not isinstance(value, bool):
            raise InvalidEdit("a boolean value is required")
        if editor.kind == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            raise InvalidEdit("an integer value is required")
        if editor.kind == "float" and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise InvalidEdit("a numeric value is required")
        if editor.choices and value not in {candidate[0] for candidate in editor.choices}:
            raise InvalidEdit("the selected value is not supported")
        minimum = editor.expert_minimum if expert else editor.minimum
        maximum = editor.expert_maximum if expert else editor.maximum
        if minimum is not None and value < minimum:
            raise InvalidEdit(f"minimum value is {minimum}")
        if maximum is not None and value > maximum:
            raise InvalidEdit(f"maximum value is {maximum}")

    @staticmethod
    def _require_boolean(value: Any) -> bool:
        if not isinstance(value, bool):
            raise InvalidEdit("a boolean value is required")
        return value

    @staticmethod
    def _require_integer(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise InvalidEdit("an integer value is required")
        return value

    @staticmethod
    def _mask_identifier(value: int | None) -> str:
        if value is None:
            return "Not available"
        text = str(value)
        if len(text) <= 4:
            return "•" * len(text)
        return "•" * (len(text) - 4) + text[-4:]

    def _csv(self, key: str) -> list[str]:
        if not self.preferences:
            return []
        raw = str(self.preferences.get(key, ""))
        return [item for item in raw.split(",") if item]

    def _story_states(self) -> dict[tuple[str, int], tuple[bool, bool]]:
        if not self.preferences:
            return {}
        state: dict[tuple[str, int], tuple[bool, bool]] = {}
        for token in filter(None, str(self.preferences.get("st_v", "")).split(",")):
            parts = token.split("|")
            if len(parts) != 4:
                continue
            try:
                state[(parts[0], int(parts[1]))] = (bool(int(parts[2])), bool(int(parts[3])))
            except ValueError:
                continue
        return state

    def _render_payloads(self) -> tuple[dict[SaveKind, ArtifactPayload], list[Diagnostic]]:
        diagnostics: list[Diagnostic] = []
        rendered: dict[SaveKind, bytes] = {}
        prefs = (
            SharedPrefsDocument.from_bytes(self.preferences.to_bytes())
            if self.preferences
            else None
        )
        if prefs is not None:
            baseline_prefs = SharedPrefsDocument.from_bytes(
                self._baseline_raw[SaveKind.PREFERENCES]
            )

            changed_preferences = {
                key for key in prefs if prefs.get(key) != baseline_prefs.get(key)
            }
            changed_maps = {
                name
                for name, kind, document in (
                    ("un", SaveKind.UNLOCK_PROGRESS, self.unlocks),
                    ("ms", SaveKind.MISSION_PROGRESS, self.missions),
                )
                if document is not None and document.to_bytes() != self._baseline_raw[kind]
            }
            report = evaluate_integrity(
                prefs,
                un=self.unlocks,
                ms=self.missions,
                device_id=self.device_id,
                user_id=self.user_id,
            )
            plan = plan_integrity(
                report,
                changed_preferences=changed_preferences,
                changed_maps=changed_maps,
                repair=self._repair_requested,
            )
            for result in plan.updates:
                prefs.set(result.key, result.computed, kind="string")
            for result in plan.unresolved:
                identity_missing = result.reason_code == "identity_context_missing"
                diagnostics.append(
                    Diagnostic(
                        "capability.identity_required"
                        if identity_missing
                        else "integrity.unresolved",
                        "error",
                        "error.capability_unavailable",
                        {
                            "key": result.key,
                            "area": "owned content" if identity_missing else result.source,
                        },
                    )
                )
            if self._integrity_blocked and not self._repair_requested:
                diagnostics.append(
                    Diagnostic(
                        "integrity.repair_required",
                        "error",
                        "error.commit_blocked",
                    )
                )
            rendered[SaveKind.PREFERENCES] = prefs.to_bytes()
        if self.unlocks is not None:
            rendered[SaveKind.UNLOCK_PROGRESS] = self.unlocks.to_bytes()
        if self.missions is not None:
            rendered[SaveKind.MISSION_PROGRESS] = self.missions.to_bytes()
        if self.score_database is not None:
            rendered[SaveKind.SCORE_DATABASE] = self.score_database.render_bytes()
        if self._profile_mismatch and not self.request.allow_version_override:
            diagnostics.append(
                Diagnostic(
                    "version.profile_mismatch",
                    "error",
                    "error.unsupported_format",
                    {"profile": self.request.game_version},
                )
            )

        payloads = {
            kind: ArtifactPayload(
                kind,
                self.sources[kind],
                self._baseline_raw[kind],
                value,
            )
            for kind, value in rendered.items()
        }
        # Re-open every staged XML representation before it reaches the transaction module.
        if SaveKind.PREFERENCES in rendered:
            SharedPrefsDocument.from_bytes(rendered[SaveKind.PREFERENCES])
        if SaveKind.UNLOCK_PROGRESS in rendered:
            PlistMapDocument.from_bytes(rendered[SaveKind.UNLOCK_PROGRESS])
        if SaveKind.MISSION_PROGRESS in rendered:
            PlistMapDocument.from_bytes(rendered[SaveKind.MISSION_PROGRESS])
        return payloads, diagnostics

    def preview(self) -> CommitPreview:
        self._ensure_open()
        payloads, diagnostics = self._render_payloads()
        token_source = {
            "revision": self.revision,
            "artifacts": {
                kind.value: sha256_bytes(payload.rendered) for kind, payload in payloads.items()
            },
            "diagnostics": [item.code for item in diagnostics],
        }
        token = hashlib.sha256(json.dumps(token_source, sort_keys=True).encode("utf-8")).hexdigest()
        artifacts = tuple(
            ArtifactPlan(
                kind,
                payload.source,
                payload.source,
                payload.original != payload.rendered,
                sha256_bytes(payload.original),
                sha256_bytes(payload.rendered),
            )
            for kind, payload in payloads.items()
        )
        return CommitPreview(
            token,
            self.revision,
            self._net_changes(),
            artifacts,
            tuple(diagnostics),
            not any(item.severity in {"error", "fatal"} for item in diagnostics),
        )

    def _net_changes(self) -> tuple[SemanticChange, ...]:
        merged: dict[tuple[Section, str, str], SemanticChange] = {}
        for entry in self._history:
            for change in entry.changes:
                key = (change.section, change.item_id or change.item_label, change.field_label_id)
                current = merged.get(key)
                if current is None:
                    merged[key] = change
                else:
                    merged[key] = replace(
                        current, after=change.after, derived=current.derived or change.derived
                    )
        return tuple(change for change in merged.values() if change.before != change.after)

    def commit(self, request: CommitRequest | None = None) -> CommitReceipt:
        self._ensure_open()
        request = request or CommitRequest()
        preview = self.preview()
        if request.preview_token is None:
            raise CommitBlocked("a current preview token is required")
        if request.preview_token != preview.token:
            raise CommitBlocked("preview token is stale")
        if not preview.can_commit:
            raise CommitBlocked("commit preview contains blocking diagnostics")
        payloads, _ = self._render_payloads()
        manager = TransactionManager()
        prepared = manager.prepare(
            payloads,
            destination=request.destination,
            overwrite_sources=request.overwrite_sources,
            create_backup=request.create_backup,
        )
        receipt = manager.commit(prepared, revision=self.revision)
        try:
            self._reopen_after_commit(receipt)
        except Exception as exc:
            self._reload_error = ReloadRequired(receipt, exc)
            raise self._reload_error from exc
        return receipt

    def _reopen_after_commit(self, receipt: CommitReceipt) -> None:
        # Construct a fully verified replacement before touching the current draft.
        request = replace(
            self.request,
            sources=tuple(SourceSpec(a.kind, a.destination) for a in receipt.artifacts),
            device_id=self.device_id,
            user_id=self.user_id,
        )
        candidate = type(self).open(request)
        candidate.revision = self.revision + 1
        self.__dict__.update(candidate.__dict__)

    def close(self, *, discard: bool = False) -> None:
        if self._closed:
            return
        if self.dirty and not discard:
            raise UncommittedChanges()
        self._closed = True

    def _label(self, message_id: str) -> str:
        english = {
            "section.fragments": "Fragments",
            "field.note_speed": "Note speed",
            "field.audio_offset": "Audio offset",
            "field.bluetooth_offset": "Bluetooth offset",
            "field.sfx_volume": "SFX volume",
            "field.colorblind": "Colorblind mode",
            "field.performance": "Performance mode",
            "field.game_language": "Game language",
            "field.song_sort": "Song sorting",
            "field.song_order": "Song order",
        }
        chinese = {
            "section.fragments": "残片",
            "field.note_speed": "谱面流速",
            "field.audio_offset": "音频偏移",
            "field.bluetooth_offset": "蓝牙偏移",
            "field.sfx_volume": "打击音音量",
            "field.colorblind": "色觉辅助模式",
            "field.performance": "性能模式",
            "field.game_language": "游戏语言",
            "field.song_sort": "歌曲排序方式",
            "field.song_order": "歌曲排列顺序",
        }
        return (chinese if self.request.locale == "zh-Hans" else english).get(
            message_id, message_id
        )

    def _local(self, english: str, chinese: str) -> str:
        return chinese if self.request.locale == "zh-Hans" else english

    @staticmethod
    def _pretty(value: str) -> str:
        return value.replace("_", " ").replace("-", " ").title()

    def _partner_name(self, value: str) -> str:
        partner = next(
            (partner for partner in self.catalog.partners if partner.name == value), None
        )
        if partner:
            return partner.display_name.resolve(self.request.locale, default=self._pretty(value))
        return self._pretty(value)

    def _partner_description(self, partner_id: int) -> str:
        skill = self.catalog.partner(partner_id).skill
        body = skill.description.resolve(self.request.locale)
        awakened = skill.uncapped_description.resolve(self.request.locale)
        if not body and not skill.id:
            body = self._local("No skill.", "无技能。")
        if awakened:
            body += self._local("\nAfter awakening: ", "\n觉醒后：") + awakened
        if skill.unlock_level:
            body += self._local(
                f"\nSkill unlocks at level {skill.unlock_level}.",
                f"\n技能在 {skill.unlock_level} 级解锁。",
            )
        if re.search(r"%\d*@", body):
            body = re.sub(r"%\d*@", self._local("〈parameter〉", "〈动态参数〉"), body)
            body += self._local(
                "\nParameters depend on the in-game level/state; "
                "they are not inferred from this save.",
                "\n动态参数取决于游戏内等级或状态，此处不根据存档猜测数值。",
            )
        return body

    def _skill_label(self, key: str, fallback: str) -> str:
        chinese = {
            "ca_wacca_luin_k": "露恩 · 残片技能进度",
            "ca_wacca_luin_awakened_k": "露恩（觉醒）· 残片技能进度",
            "ca_wacca_luin_awakened_lifebar_k": "露恩（觉醒）· 储存的回忆收集率",
            "ca_wacca_lily_k": "莉莉 · 残片技能进度",
            "ca_wacca_elizabeth_k": "伊莉莎白 · 残片技能进度",
            "ca_nonoka_rank_k": "野乃香 · 连续通关残片阶级",
            "casa_k": "白姬 · 距离下次翻倍累计的残片",
        }
        return self._local(fallback, chinese.get(key, fallback))

    def _song_label(self, song_id: str) -> str:
        try:
            return self.catalog.song(song_id).title.resolve(self.request.locale, default=song_id)
        except Exception:
            return "Custom chart"

    def _pack_label(self, pack_id: str) -> str:
        try:
            pack = self.catalog.pack(pack_id)
            return pack.name.resolve(self.request.locale, default=pack_id) or self._pretty(pack_id)
        except Exception:
            return self._pretty(pack_id)

    def _mission_label(self, mission_id: str) -> str:
        suffix = mission_id.rsplit("_", 1)[-1]
        labels = MISSION_LABELS.get(suffix)
        if labels:
            return self._local(*labels)
        if suffix == "end":
            return self._local(*MISSION_LABELS["end"])
        return self._local("Startup mission", "新手任务")
