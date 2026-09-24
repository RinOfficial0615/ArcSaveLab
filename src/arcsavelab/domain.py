"""Game-domain vocabulary used by catalog consumers.

The APK field names and persistence keys intentionally stop at the catalog
adapter.  UI, CLI, and plugin code consume the value objects in this module.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from types import MappingProxyType


class DomainValidationError(ValueError):
    """A value cannot represent the stated game concept."""


class DomainLookupError(LookupError):
    """A requested game entity is not present in a catalog."""


_VERSION_RE = re.compile(
    r"^(?P<numbers>(?:0|[1-9]\d*)(?:\.(?:0|[1-9]\d*)){1,2})(?P<channel>[A-Za-z]*)$"
)


@dataclass(frozen=True, slots=True, order=True)
class GameVersion:
    """Validated Arcaea content or game version."""

    release: tuple[int, ...]
    channel: str = ""

    def __post_init__(self) -> None:
        if len(self.release) not in (2, 3) or any(part < 0 for part in self.release):
            raise DomainValidationError("a game version needs two or three non-negative parts")
        if self.channel and not self.channel.isalpha():
            raise DomainValidationError("a game version channel must contain letters only")

    @property
    def value(self) -> str:
        return ".".join(str(part) for part in self.release) + self.channel

    @classmethod
    def parse(cls, value: str | GameVersion) -> GameVersion:
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise DomainValidationError("game version must be text")
        match = _VERSION_RE.fullmatch(value)
        if match is None:
            raise DomainValidationError(f"invalid game version: {value!r}")
        return cls(
            tuple(int(part) for part in match.group("numbers").split(".")),
            match.group("channel"),
        )

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    """Identity inputs used when the game binds integrity values to a device."""

    device_id: str
    player_id: int

    def __post_init__(self) -> None:
        if not isinstance(self.device_id, str) or not self.device_id or self.device_id.isspace():
            raise DomainValidationError("device identity must not be empty")
        if isinstance(self.player_id, bool) or not isinstance(self.player_id, int):
            raise DomainValidationError("player id must be an integer")
        if self.player_id < 0:
            raise DomainValidationError("player id must not be negative")

    @property
    def is_complete(self) -> bool:
        return True


class Difficulty(IntEnum):
    PAST = 0
    PRESENT = 1
    FUTURE = 2
    BEYOND = 3
    ETERNAL = 4

    @classmethod
    def from_code(cls, value: int | Difficulty) -> Difficulty:
        if isinstance(value, cls):
            return value
        try:
            return cls(value)
        except (TypeError, ValueError) as exc:
            raise DomainValidationError(f"unknown chart difficulty: {value!r}") from exc

    @property
    def short_name(self) -> str:
        return {
            Difficulty.PAST: "PST",
            Difficulty.PRESENT: "PRS",
            Difficulty.FUTURE: "FTR",
            Difficulty.BEYOND: "BYD",
            Difficulty.ETERNAL: "ETR",
        }[self]


class ScoreGrade(IntEnum):
    D = 0
    C = 1
    B = 2
    A = 3
    AA = 4
    EX = 5
    EX_PLUS = 6


def _canonical_locale(locale: str) -> str:
    normalized = locale.replace("_", "-")
    lower = normalized.lower()
    if lower in {"zh-cn", "zh-sg", "zh-hans"}:
        return "zh-Hans"
    if lower in {"zh-tw", "zh-hk", "zh-mo", "zh-hant"}:
        return "zh-Hant"
    if lower == "en-us" or lower == "en-gb":
        return "en"
    return normalized


@dataclass(frozen=True, slots=True)
class LocalizedText:
    """A deterministic, immutable set of localized labels."""

    entries: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        locales = [locale for locale, _text in self.entries]
        if len(locales) != len(set(locales)):
            raise DomainValidationError("localized text contains a duplicate locale")
        if any(not isinstance(locale, str) or not locale for locale in locales):
            raise DomainValidationError("localized text locale must not be empty")
        if any(not isinstance(text, str) for _locale, text in self.entries):
            raise DomainValidationError("localized text values must be text")

    @classmethod
    def from_mapping(cls, values: Mapping[str, str] | None) -> LocalizedText:
        if not values:
            return cls()
        return cls(tuple(sorted((str(locale), text) for locale, text in values.items())))

    @property
    def available_locales(self) -> tuple[str, ...]:
        return tuple(locale for locale, _text in self.entries)

    def resolve(
        self,
        locale: str = "en",
        *,
        fallback_locales: Sequence[str] = ("en",),
        default: str = "",
    ) -> str:
        values = dict(self.entries)
        requested = _canonical_locale(locale)
        candidates = [locale, requested]
        if "-" in requested:
            candidates.append(requested.split("-", 1)[0])
        candidates.extend(fallback_locales)
        for candidate in candidates:
            canonical = _canonical_locale(candidate)
            if candidate in values:
                return values[candidate]
            if canonical in values:
                return values[canonical]
        if self.entries:
            return self.entries[0][1]
        return default


@dataclass(frozen=True, slots=True)
class Song:
    id: str
    index: int
    title: LocalizedText
    artist: str | None
    pack_id: str | None
    purchase_id: str | None
    version: GameVersion | None
    side: int | None
    release_date: int | None
    category: str | None = None
    world_unlock: bool = False
    remote_download: bool = False
    streamable: bool = True
    deleted: bool = False

    def __post_init__(self) -> None:
        if not self.id:
            raise DomainValidationError("song id must not be empty")
        if self.index < 0:
            raise DomainValidationError("song index must not be negative")


@dataclass(frozen=True, slots=True)
class Chart:
    song_id: str
    difficulty: Difficulty
    rating: int
    rating_plus: bool
    chart_designer: str
    jacket_designer: str
    version: GameVersion | None
    release_date: int | None
    hidden_until: str | None = None
    hidden_until_unlocked: bool = False
    world_unlock: bool = False

    def __post_init__(self) -> None:
        if not self.song_id:
            raise DomainValidationError("chart song id must not be empty")
        if self.rating < 0:
            raise DomainValidationError("chart rating must not be negative")

    @property
    def display_rating(self) -> str:
        return f"{self.rating}{'+' if self.rating_plus else ''}"


@dataclass(frozen=True, slots=True)
class SongPack:
    id: str
    parent_id: str | None
    name: LocalizedText
    description: LocalizedText
    section: str
    featured_partner_id: int | None
    extend_pack: bool = False
    active_extend_pack: bool = False
    coming_soon: bool = False
    limited_sale_end_time: int | None = None


@dataclass(frozen=True, slots=True)
class PartnerStats:
    fragments: float
    step: float
    overdrive: float


@dataclass(frozen=True, slots=True)
class PartnerSkill:
    id: str
    uncapped_id: str | None
    unlock_level: int
    requires_uncap: bool = False
    description: LocalizedText = LocalizedText()
    uncapped_description: LocalizedText = LocalizedText()


@dataclass(frozen=True, slots=True)
class CoreRequirement:
    core_id: str
    amount: int


@dataclass(frozen=True, slots=True)
class Partner:
    id: int
    name: str
    search_terms: tuple[str, ...]
    pack_id: str | None
    base_partner: bool
    base_partner_id: int | None
    available: bool
    previewable: bool
    partner_type: int
    base_stats: PartnerStats
    maximum_stats: PartnerStats
    skill: PartnerSkill
    uncap_cores: tuple[CoreRequirement, ...]
    version_from: GameVersion
    uncap_version_from: GameVersion | None = None
    display_name: LocalizedText = LocalizedText()


@dataclass(frozen=True, slots=True)
class StoryPath:
    main_id: int | str
    story_type: int
    act: int
    partner_icon_id: int | None = None
    partner_icon_x_offset: int | None = None


@dataclass(frozen=True, slots=True, order=True)
class StoryEntryKey:
    main_id: int | str
    minor_id: int


@dataclass(frozen=True, slots=True)
class StoryRequirement:
    previous_minor_id: int | None = None
    purchase_id: str | None = None
    alternate_purchase_id: str | None = None
    clear_song_id: str | None = None
    clear_partner_id: int | None = None
    additional_entries: tuple[str, ...] = ()
    anomaly_id: str | None = None
    block_until_previous_read: bool = False


@dataclass(frozen=True, slots=True)
class StoryEntry:
    key: StoryEntryKey
    route: str
    story_type: str
    requirement: StoryRequirement
    unlocked_song_id: str | None = None
    hidden_from_count: bool = False
    has_alternative: bool = False
    icon_id: str | None = None
    primary_partner_id: int | None = None
    secondary_partner_id: int | None = None
    narrative_asset_id: str | None = None
    illustration_asset_id: str | None = None


class MissionRequirementKind(StrEnum):
    LOCAL_CLEAR = "local_clear"
    COMPLETE_MISSION = "complete_mission"
    CUSTOM_CONDITION = "custom_condition"
    MINIMUM_POTENTIAL = "minimum_potential"


@dataclass(frozen=True, slots=True)
class MissionRequirement:
    kind: MissionRequirementKind
    related_mission_id: str | None = None
    condition_id: str | None = None
    minimum_potential: int | None = None


class MissionRewardKind(StrEnum):
    FRAGMENTS = "fragments"
    GENERIC_CORES = "generic_cores"
    STAMINA = "stamina"
    PICK_TICKET = "pick_ticket"
    CONTENT_UNLOCK = "content_unlock"


@dataclass(frozen=True, slots=True)
class MissionReward:
    kind: MissionRewardKind
    amount: int = 1
    content_id: str | None = None


@dataclass(frozen=True, slots=True)
class Mission:
    id: str
    tier: int
    requirements: tuple[MissionRequirement, ...]
    rewards: tuple[MissionReward, ...]
    missions_to_clear_tier: int | None = None


class UnlockRequirementKind(StrEnum):
    CREDITS = "credits"
    CLEAR_SONG = "clear_song"
    PLAY_SONG = "play_song"
    PLAY_SONG_TIMES = "play_song_times"
    ANY_OF = "any_of"
    PLAYER_RATING = "player_rating"
    CLEAR_SONGS_OF_LEVEL = "clear_songs_of_level"
    LEGACY_SONG_CONDITION = "legacy_song_condition"
    SONG_LAMP = "song_lamp"
    CLEAR_SONGS_OF_DIFFICULTY = "clear_songs_of_difficulty"
    READ_STORY = "read_story"
    HIDDEN_DIFFICULTY_PLAY = "hidden_difficulty_play"
    SPECIAL_SEAL = "special_seal"
    CHALLENGE = "challenge"
    SPECIAL_SEAL_PARTNER = "special_seal_partner"
    FINALE = "finale"
    PARTNER_SKILL_TOGGLE = "partner_skill_toggle"
    SONG_PLAYABLE = "song_playable"
    SPELL_MAGNOLIA = "spell_magnolia"
    ARGHENA_STORIES = "arghena_stories"
    ARGHENA_PUZZLE = "arghena_puzzle"
    ARGHENA_COURSE = "arghena_course"
    ALTER_EGO_PUZZLE = "alter_ego_puzzle"
    CLEAR_STORY_ACT_SONGS = "clear_story_act_songs"
    KONZETSU_CHAIN = "konzetsu_chain"
    KONZETSU_PRECHALLENGE = "konzetsu_prechallenge"


@dataclass(frozen=True, slots=True)
class UnlockRequirement:
    kind: UnlockRequirementKind
    children: tuple[UnlockRequirement, ...] = ()
    song_id: str | None = None
    difficulty: Difficulty | None = None
    minimum_grade: ScoreGrade | None = None
    required_credits: int | None = None
    play_count: int | None = None
    minimum_player_rating: int | None = None
    song_level: int | None = None
    song_level_plus: bool | None = None
    required_clear_count: int | None = None
    minimum_lamp: int | None = None
    story_entry: StoryEntryKey | None = None
    range_minimum: int | None = None
    range_maximum: int | None = None
    partner_id: int | None = None
    requires_awakened_partner: bool | None = None
    inverted: bool | None = None
    puzzle_index: int | None = None
    story_act: int | None = None

    def __post_init__(self) -> None:
        if self.kind is UnlockRequirementKind.ANY_OF and not self.children:
            raise DomainValidationError("an any-of unlock requirement needs children")
        if self.kind is not UnlockRequirementKind.ANY_OF and self.children:
            raise DomainValidationError("only an any-of requirement can contain children")


@dataclass(frozen=True, slots=True)
class SongUnlock:
    song_id: str
    difficulty: Difficulty
    requirements: tuple[UnlockRequirement, ...]


@dataclass(frozen=True, slots=True)
class CatalogSummary:
    song_count: int
    live_song_count: int
    chart_count: int
    pack_count: int
    partner_count: int
    story_path_count: int
    story_entry_count: int
    mission_count: int
    song_unlock_count: int
    simplified_chinese_message_count: int


def _unique_index[Entity, EntityKey: Hashable](
    items: Iterable[Entity], key: Callable[[Entity], EntityKey], label: str
) -> dict[EntityKey, Entity]:
    result: dict[EntityKey, Entity] = {}
    for item in items:
        item_key = key(item)
        if item_key in result:
            raise DomainValidationError(f"duplicate {label}: {item_key!r}")
        result[item_key] = item
    return result


class GameCatalog:
    """Immutable aggregate and lookup seam for one game version."""

    __slots__ = (
        "_version",
        "_songs",
        "_charts",
        "_packs",
        "_partners",
        "_story_paths",
        "_story_entries",
        "_missions",
        "_song_unlocks",
        "_translations",
        "_song_index",
        "_chart_index",
        "_pack_index",
        "_partner_index",
        "_story_path_index",
        "_story_entry_index",
        "_mission_index",
        "_unlock_index",
        "_summary",
    )

    def __init__(
        self,
        *,
        version: GameVersion,
        songs: Iterable[Song],
        charts: Iterable[Chart],
        packs: Iterable[SongPack],
        partners: Iterable[Partner],
        story_paths: Iterable[StoryPath],
        story_entries: Iterable[StoryEntry],
        missions: Iterable[Mission],
        song_unlocks: Iterable[SongUnlock],
        translations: Mapping[str, Mapping[str, str]],
    ) -> None:
        self._version = version
        self._songs = tuple(songs)
        self._charts = tuple(charts)
        self._packs = tuple(packs)
        self._partners = tuple(partners)
        self._story_paths = tuple(story_paths)
        self._story_entries = tuple(story_entries)
        self._missions = tuple(missions)
        self._song_unlocks = tuple(song_unlocks)
        self._translations = MappingProxyType(
            {locale: MappingProxyType(dict(messages)) for locale, messages in translations.items()}
        )
        self._song_index = _unique_index(self._songs, lambda item: item.id, "song id")
        self._chart_index = _unique_index(
            self._charts,
            lambda item: (item.song_id, item.difficulty),
            "chart key",
        )
        self._pack_index = _unique_index(self._packs, lambda item: item.id, "pack id")
        self._partner_index = _unique_index(self._partners, lambda item: item.id, "partner id")
        self._story_path_index = _unique_index(
            self._story_paths, lambda item: item.main_id, "story path"
        )
        self._story_entry_index = _unique_index(
            self._story_entries, lambda item: item.key, "story entry"
        )
        self._mission_index = _unique_index(self._missions, lambda item: item.id, "mission id")
        self._unlock_index = _unique_index(
            self._song_unlocks,
            lambda item: (item.song_id, item.difficulty),
            "song unlock",
        )
        live_songs = sum(not song.deleted for song in self._songs)
        zh_hans_messages = len(self._translations.get("zh-Hans", {}))
        self._summary = CatalogSummary(
            song_count=len(self._songs),
            live_song_count=live_songs,
            chart_count=len(self._charts),
            pack_count=len(self._packs),
            partner_count=len(self._partners),
            story_path_count=len(self._story_paths),
            story_entry_count=len(self._story_entries),
            mission_count=len(self._missions),
            song_unlock_count=len(self._song_unlocks),
            simplified_chinese_message_count=zh_hans_messages,
        )

    @property
    def version(self) -> GameVersion:
        return self._version

    @property
    def summary(self) -> CatalogSummary:
        return self._summary

    @property
    def songs(self) -> tuple[Song, ...]:
        return self._songs

    @property
    def charts(self) -> tuple[Chart, ...]:
        return self._charts

    @property
    def packs(self) -> tuple[SongPack, ...]:
        return self._packs

    @property
    def partners(self) -> tuple[Partner, ...]:
        return self._partners

    @property
    def story_paths(self) -> tuple[StoryPath, ...]:
        return self._story_paths

    @property
    def story_entries(self) -> tuple[StoryEntry, ...]:
        return self._story_entries

    @property
    def missions(self) -> tuple[Mission, ...]:
        return self._missions

    @property
    def song_unlocks(self) -> tuple[SongUnlock, ...]:
        return self._song_unlocks

    def song(self, song_id: str) -> Song:
        try:
            return self._song_index[song_id]
        except KeyError as exc:
            raise DomainLookupError(f"unknown song: {song_id}") from exc

    def chart(self, song_id: str, difficulty: Difficulty | int) -> Chart:
        key = (song_id, Difficulty.from_code(difficulty))
        try:
            return self._chart_index[key]
        except KeyError as exc:
            raise DomainLookupError(f"unknown chart: {song_id}/{key[1].short_name}") from exc

    def charts_for_song(self, song_id: str) -> tuple[Chart, ...]:
        self.song(song_id)
        return tuple(chart for chart in self._charts if chart.song_id == song_id)

    def pack(self, pack_id: str) -> SongPack:
        try:
            return self._pack_index[pack_id]
        except KeyError as exc:
            raise DomainLookupError(f"unknown song pack: {pack_id}") from exc

    def partner(self, partner_id: int) -> Partner:
        try:
            return self._partner_index[partner_id]
        except KeyError as exc:
            raise DomainLookupError(f"unknown partner: {partner_id}") from exc

    def story_path(self, main_id: int | str) -> StoryPath:
        try:
            return self._story_path_index[main_id]
        except KeyError as exc:
            raise DomainLookupError(f"unknown story path: {main_id}") from exc

    def story_entry(self, main_id: int | str, minor_id: int) -> StoryEntry:
        key = StoryEntryKey(main_id, minor_id)
        try:
            return self._story_entry_index[key]
        except KeyError as exc:
            raise DomainLookupError(f"unknown story entry: {main_id}-{minor_id}") from exc

    def mission(self, mission_id: str) -> Mission:
        try:
            return self._mission_index[mission_id]
        except KeyError as exc:
            raise DomainLookupError(f"unknown mission: {mission_id}") from exc

    def song_unlock(self, song_id: str, difficulty: Difficulty | int) -> SongUnlock:
        key = (song_id, Difficulty.from_code(difficulty))
        try:
            return self._unlock_index[key]
        except KeyError as exc:
            raise DomainLookupError(f"unknown song unlock: {song_id}/{key[1].short_name}") from exc

    def translate(self, source_text: str, *, locale: str = "zh-Hans") -> str:
        canonical = _canonical_locale(locale)
        messages = self._translations.get(canonical)
        if messages is None:
            return source_text
        return messages.get(source_text, source_text)
