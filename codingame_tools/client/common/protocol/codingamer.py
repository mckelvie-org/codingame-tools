"""
JSON-serializable dataclasses for the CodinGamer service's findCodingamePointsStatsByHandle,
findCodinGamerPublicInformations, findFollowers, and findFollowing Codingame API methods.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ....common.dataclass_wizard_x import Alias, CatchAll, CgEpochMillis, JSONWizardX


@dataclass
class CgCodingamer(JSONWizardX):
    """A codingamer's profile, as embedded in the response to findCodingamePointsStatsByHandle,
       and returned directly by findCodinGamerPublicInformations.

       `pseudo`/`country_id` are Optional--confirmed live (a fresh/minimal "dev" test account,
       level 1, no display name set, returned `{"userId": ..., "countryId": "US",
       "publicHandle": ..., "formValues": {}, "level": 1}` with no `pseudo` key at all). Matches
       the same already-documented behavior on the sibling class `CgCodingamerFollower` (used by
       findFollowers/findFollowing), which independently discovered `pseudo`/`country_id` missing
       for "apparently never-configured accounts"--`country_id` hasn't been directly observed
       missing here yet, but is made Optional pre-emptively given that precedent, rather than
       waiting to hit the same failure a second time for a different field."""

    user_id: int
    """The codingamer's numeric ID."""

    public_handle: str
    """The codingamer's opaque public handle string, as passed to findCodingamePointsStatsByHandle."""

    # `extra_data` is deliberately the first field with a default: dataclass_wizard 1.0.0 mis-binds
    # any defaulted field positioned immediately before it (silently, no error) to the CatchAll's
    # own value. Keeping it first among the defaulted fields makes that impossible.
    extra_data: CatchAll = field(default_factory=dict)

    pseudo: str | None = None
    """The codingamer's display name. Not always present; see class docstring."""

    country_id: str | None = None
    """ISO country code, e.g. "US", "GB". Not always present; see class docstring."""

    form_values: dict[str, str] | None = None
    """Freeform profile fields the codingamer has filled in, e.g. {"city": "Seattle",
       "school": "University of Arizona"}. Keys observed vary per codingamer."""

    school_id: int | None = None
    """Internal ID of the school selected in the codingamer's profile, if any."""

    rank: int | None = None
    """The codingamer's global points rank."""

    avatar: int | None = None
    """The binary image ID of the codingamer's avatar image."""

    cover: int | None = None
    """The binary image ID of the codingamer's cover image."""

    tagline: str | None = None
    """Short freeform tagline shown on the codingamer's profile."""

    company: str | None = None
    """Freeform current employer, as entered in the codingamer's profile."""

    city: str | None = None
    """Freeform city, as entered in the codingamer's profile. Observed duplicating `form_values["city"]`."""

    level: int | None = None
    """The codingamer's current level, derived from `xp` (see `CgCodingamePointsStats.xp_thresholds`)."""

    xp: int | None = None
    """The codingamer's total accumulated XP."""

    category: str | None = None
    """e.g. "PROFESSIONAL", "STUDENT"."""

    biography: str | None = None
    """Freeform biography text, as entered in the codingamer's profile."""

    _online_since: CgEpochMillis | None = Alias("onlineSince", default=None)

    @property
    def online_since(self) -> datetime | None:
        """When the codingamer was last online, always UTC."""
        return self._online_since

    @online_since.setter
    def online_since(self, value: datetime | None) -> None:
        self._online_since = None if value is None else CgEpochMillis.upcast(value)


@dataclass
class CgRankHistoryEntry(JSONWizardX):
    """A single dated snapshot in a codingamer's points-ranking history
       (`CgCodingamePointsRankingDto.rank_history`). All fields have been consistently present
       across every observed entry."""

    rank: int
    """The codingamer's global points rank as of this snapshot's date."""

    total: int
    """Total number of ranked codingamers as of this snapshot's date."""

    points: int
    """The codingamer's total points as of this snapshot's date (sum of the category points below)."""

    contest_points: int
    """Points earned from contests, as of this snapshot's date."""

    optim_points: int
    """Points earned from optimization puzzles, as of this snapshot's date."""

    codegolf_points: int
    """Points earned from code golf puzzles, as of this snapshot's date."""

    multi_training_points: int
    """Points earned from multiplayer training games, as of this snapshot's date."""

    clash_points: int
    """Points earned from Clash of Code, as of this snapshot's date."""

    _date: CgEpochMillis = Alias("date")

    extra_data: CatchAll = field(default_factory=dict)

    @property
    def date(self) -> datetime:
        """The date of this ranking snapshot, always UTC."""
        return self._date

    @date.setter
    def date(self, value: datetime) -> None:
        self._date = CgEpochMillis.upcast(value)


@dataclass
class CgCodingamePointsRankingDto(JSONWizardX):
    """Points-ranking summary and history for a codingamer.

       Per CodinGame's own ranking rework documentation (https://www.codingame.com/help/rank,
       https://www.codingame.com/blog/ranking-rework-competition/): rewards are split into XP
       (contributes to Level, i.e. platform activity) and CodinPoints/"CP" (contributes to rank,
       i.e. multiplayer performance), and CP is tracked as five *separate* per-category
       leaderboards (contests, bot programming, Clash of Code, optimization, code golf), each
       computed per-game via `(BASE * min(N/500, 1)) ^ ((N-C+1)/N)` with a category-specific
       `BASE`--i.e. the per-category fields below are NOT simple counts of "points earned" on a
       shared scale, and are NOT meant to be summed. Confirmed live (2026-08-01): summing all
       seven `codingame_points_*` fields below does *not* reproduce `codingame_points_total`
       (off by more than an order of magnitude)--whatever `codingame_points_total`/
       `codingame_points_rank` are actually derived from isn't fully understood; treat the
       per-category fields as informational only, not as a breakdown of the total/rank."""

    codingame_points_total: int
    """The codingamer's current total points, used for `codingame_points_rank`. NOT confirmed to
       be a sum of the `codingame_points_*` category fields below--see the class docstring."""

    codingame_points_rank: int
    """The codingamer's current global points rank."""

    codingame_points_contests: int
    """Current CodinPoints in the contests leaderboard category--see the class docstring for why
       this isn't a simple "points earned" count comparable across categories."""

    codingame_points_achievements: int
    """Current points attributed to achievements. Per CodinGame's ranking rework, achievement
       rewards now convert to XP rather than CP--this field's exact current meaning/scale is
       unconfirmed."""

    codingame_points_xp: int
    """Observed live to exactly equal `CgCodingamer.xp` (raw lifetime XP)--i.e. this appears to
       just mirror XP for display here, not a CP-scaled value; see the class docstring."""

    codingame_points_optim: int
    """Current CodinPoints in the optimization-puzzles leaderboard category--see the class
       docstring."""

    codingame_points_codegolf: int
    """Current CodinPoints in the code-golf-puzzles leaderboard category--see the class
       docstring."""

    codingame_points_multi_training: int
    """Current CodinPoints in the multiplayer-training-games leaderboard category--see the class
       docstring."""

    codingame_points_clash: int
    """Current CodinPoints in the Clash of Code leaderboard category--see the class docstring."""

    number_codingamers: int
    """Total number of codingamers ranked in the codingamer's local ranking scope."""

    number_codingamers_global: int
    """Total number of codingamers ranked globally."""

    extra_data: CatchAll = field(default_factory=dict)

    rank_history: list[CgRankHistoryEntry] = field(default_factory=list)
    """Dated snapshots of the codingamer's ranking/points over time."""


@dataclass
class CgXpThreshold(JSONWizardX):
    """One entry in the per-level XP threshold/progression table
       (`CgCodingamePointsStats.xp_thresholds`)."""

    level: int
    """The level this entry describes."""

    xp_threshold: int
    """XP required to advance from this level to the next."""

    cumulative_xp: int
    """Total XP required to reach this level from level 1."""

    extra_data: CatchAll = field(default_factory=dict)

    reward_languages: dict[str, str] | None = None
    """Localized flavor text for the reward unlocked at this level (locale code -> text). Only
       present for some levels."""


@dataclass
class CgCodingamePointsStats(JSONWizardX):
    """The complete response to findCodingamePointsStatsByHandle.

       `codingamer_points` is Optional--confirmed live (a fresh/minimal "dev" test account, the
       same one that exposed `CgCodingamer.pseudo`/`country_id` being Optional): the top-level
       `codingamerPoints` key was entirely absent for that account. Originally documented here as
       "duplicating" `codingame_points_ranking_dto.codingame_points_total`, based on both being 0
       for that same degenerate (all-zero) test account--**since disproven** on a real account
       (2026-08-01): `codingamer_points` was `7150` while `codingame_points_total` was `2800` at
       the same moment--they are two distinct values, not a duplicate pair. See
       `CgCodingamePointsRankingDto`'s docstring for more on what is/isn't understood about how
       these numbers relate to each other."""

    achievement_count: int
    """The number of achievements the codingamer has unlocked."""

    codingamer: CgCodingamer
    """The codingamer's profile."""

    codingame_points_ranking_dto: CgCodingamePointsRankingDto
    """The codingamer's points-ranking summary and history."""

    extra_data: CatchAll = field(default_factory=dict)

    codingamer_points: int | None = None
    """The codingamer's current total points--**not** confirmed equal to `codingame_points_ranking_dto.
       codingame_points_total` (disproven live--see class docstring); exact relationship
       unconfirmed. Not always present; see class docstring."""

    xp_thresholds: list[CgXpThreshold] = field(default_factory=list)
    """The per-level XP threshold/progression table, up to (at least) the codingamer's current level."""


@dataclass
class CgCodingamerFollower(JSONWizardX):
    """A single codingamer in a followers/following list, as returned by findFollowers and
       findFollowing. Distinct from
       `CgCodingamer`: adds follow-relationship flags (`is_follower`/`is_following`) and a few
       differently-named/differently-shaped profile fields, and omits fields not returned by
       this endpoint (`form_values`, `school_id`, `xp`, `category`, `online_since`, `biography`).
       `pseudo`/`country_id` are Optional--observed absent for a few apparently never-configured
       accounts (rank ~1080871, 0 points)."""

    user_id: int
    """The codingamer's numeric ID."""

    public_handle: str
    """The codingamer's opaque public handle string."""

    is_follower: bool
    """Whether this codingamer follows the `current_codingamer_id` passed to `findFollowers`
       (normally the logged-in codingamer)."""

    is_following: bool
    """Whether the `current_codingamer_id` passed to `findFollowers` (normally the logged-in
       codingamer) follows this codingamer."""

    level: int
    """The codingamer's current level."""

    points: int
    """The codingamer's total points."""

    rank: int
    """The codingamer's global points rank."""

    extra_data: CatchAll = field(default_factory=dict)

    pseudo: str | None = None
    """The codingamer's display name. Not always present; see class docstring."""

    country_id: str | None = None
    """ISO country code, e.g. "US", "GB". Not always present; see class docstring."""

    avatar: int | None = None
    """The binary image ID of the codingamer's avatar image."""

    cover: int | None = None
    """The binary image ID of the codingamer's cover image."""

    city: str | None = None
    """Freeform city, as entered in the codingamer's profile."""

    company_field: str | None = None
    """Freeform current employer, as entered in the codingamer's profile. Named differently
       from `CgCodingamer.company`, for reasons unknown."""

    school_field: str | None = None
    """Freeform school, as entered in the codingamer's profile. Named differently from
       `CgCodingamer.school_id` (an internal school ID rather than freeform text), for reasons
       unknown."""

    tagline: str | None = None
    """Short freeform tagline shown on the codingamer's profile."""

    languages: str | None = None
    """A JSON-encoded array of programming language names the codingamer uses, e.g.
       '["JavaScript","Python"]'. Left as a raw (unparsed) string rather than a list: the server
       has been observed to double-encode this value for some codingamers--e.g. the literal
       2-character string '"[]"' (an already-JSON-encoded empty array, itself JSON-encoded as a
       string a second time)."""


__all__ = [
    "CgCodingamer", "CgCodingamerFollower", "CgRankHistoryEntry",
    "CgCodingamePointsRankingDto", "CgXpThreshold", "CgCodingamePointsStats",
]
