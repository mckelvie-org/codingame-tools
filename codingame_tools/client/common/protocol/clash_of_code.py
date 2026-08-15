"""
JSON-serializable dataclasses for the ClashOfCode service's getClashRankByCodinGamerId and
findClashByHandle Codingame API methods.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ....common.dataclass_wizard_x import Alias, CatchAll, CgEpochMillis, JSONWizardX
from .typedefs import CgSolutionLanguage

CgClashMode = str
"""A Clash of Code game mode, e.g. "FASTEST", "SHORTEST", "REVERSE"."""

CgClashType = str
"""The kind of clash, e.g. "FEATURED_EVENT". Only one value observed so far, so other kinds
   (e.g. an ad-hoc private/public clash created directly by codingamers) are unconfirmed, and
   may have a different set of fields--e.g. `CgClash.featured_event_handle` is presumably only
   populated for `clash_type == "FEATURED_EVENT"`."""

CgClashPlayerStatus = str
"""A clash player's status, e.g. "STANDARD". Only one value observed so far."""

CgTestSessionStatus = str
"""The status of a clash player's test session, e.g. "COMPLETED". Only one value observed so
   far (from a finished clash); an in-progress or not-yet-started clash's players likely use a
   different value pending further examples."""


@dataclass
class CgClashRank(JSONWizardX):
    """A codingamer's Clash of Code global ranking, as returned by getClashRankByCodinGamerId."""

    rank: int
    """The codingamer's global Clash of Code rank."""

    total_players: int
    """The total number of ranked Clash of Code players."""

    extra_data: CatchAll = field(default_factory=dict)


@dataclass
class CgClashPlayer(JSONWizardX):
    """A single player's participation/result in a clash, as embedded in `CgClash.players`.
       Only a single finished clash (6 players, all `status == "STANDARD"`) and a single
       not-yet-started clash (no players) have been observed so far, so optionality of fields
       beyond `codingamer_avatar_id` is unconfirmed--e.g. a player who left mid-clash or never
       submitted may be missing `score`/`rank`/`duration`."""

    codingamer_handle: str
    """The player's opaque public handle."""

    codingamer_id: int
    """The player's numeric codingamer ID."""

    codingamer_nickname: str
    """The player's display name (pseudo)."""

    duration: int
    """Milliseconds the player took to reach their final submission."""

    language_id: CgSolutionLanguage
    """The programming language the player used."""

    position: int
    """Unclear precise semantics--observed distinct from `rank` (e.g. position 2 ranked 1st).
       Possibly join order into the clash."""

    rank: int
    """The player's final rank in the clash (1 = best)."""

    score: int
    """The player's final validator score, 0-100."""

    solution_shared: bool
    """Whether the player chose to publicly share their solution after the clash."""

    status: CgClashPlayerStatus
    """The player's status in the clash; see `CgClashPlayerStatus`."""

    submission_id: int
    """Numeric ID of the player's final submission."""

    test_session_handle: str
    """Opaque handle for the player's test session against this clash."""

    test_session_status: CgTestSessionStatus
    """Status of the player's test session; see `CgTestSessionStatus`."""

    extra_data: CatchAll = field(default_factory=dict)

    codingamer_avatar_id: int | None = None
    """The binary image ID of the player's avatar image. Not always present--observed absent
       for at least half the players in one finished clash."""


@dataclass
class CgClash(JSONWizardX):
    """A single Clash of Code session, as returned by findClashByHandle. `handle` (the argument
       to this endpoint) must be a clash-instance handle (e.g. `CgFeaturedEvent`'s own `handle`
       does NOT work here--confirmed via 422--but a per-slot `CgClashSlot.clash_handle` from
       FeaturedEvent/findClashSlots does)."""

    clash_duration_type_id: str
    """Duration category for the clash, e.g. "SHORT"."""

    finished: bool
    """Whether the clash has ended."""

    mode: CgClashMode
    """The clash's (current/primary) game mode; see `CgClashMode`."""

    modes: list[CgClashMode]
    """All game modes in effect for this clash. Only ever observed containing the same single
       value as `mode`, so multi-mode clashes are unconfirmed."""

    ms_before_end: int
    """Milliseconds remaining before the clash ends. Negative if it has already ended."""

    ms_before_start: int
    """Milliseconds remaining before the clash starts. Negative if it has already started."""

    nb_players_min: int
    """Minimum number of players required for the clash to proceed."""

    players: list[CgClashPlayer]
    """Players who have joined the clash. Empty for a clash that hasn't started yet."""

    programming_languages: list[CgSolutionLanguage]
    """Programming languages available for this clash."""

    public_handle: str
    """The clash's opaque public handle, matching the `handle` argument passed in."""

    started: bool
    """Whether the clash has started."""

    _start_timestamp: CgEpochMillis = Alias("startTimestamp")
    """When the clash starts/started."""

    clash_type: CgClashType = Alias("type")
    """The kind of clash; see `CgClashType`."""

    extra_data: CatchAll = field(default_factory=dict)

    featured_event_handle: str | None = None
    """The `CgFeaturedEvent.handle` of the featured event this clash belongs to, if any. See
       `CgClashType` for the (unconfirmed) relationship to `clash_type`."""

    @property
    def start_timestamp(self) -> datetime:
        """See the field docstring for `_start_timestamp`. Always UTC."""
        return self._start_timestamp

    @start_timestamp.setter
    def start_timestamp(self, value: datetime) -> None:
        self._start_timestamp = CgEpochMillis.upcast(value)


__all__ = [
    "CgClash", "CgClashMode", "CgClashPlayer", "CgClashPlayerStatus", "CgClashRank",
    "CgClashType", "CgSolutionLanguage", "CgTestSessionStatus",
]
