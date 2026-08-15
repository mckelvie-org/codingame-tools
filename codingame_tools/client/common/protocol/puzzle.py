"""
JSON-serializable dataclasses for the Puzzle service's countSolvedPuzzlesByProgrammingLanguage,
findPuzzleOfTheWeek, findAllMinimalProgress, and findBestFollowingProgress Codingame API
methods.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ....common.dataclass_wizard_x import Alias, CatchAll, CgEpochMillis, JSONWizardX
from .last_activities import CgLastActivityPuzzle, CgPuzzleFeedback, CgPuzzleTopicNode
from .typedefs import CgSolutionLanguage


@dataclass
class CgLanguageCertification(JSONWizardX):
    """A codingamer's language certification (e.g. from a proctored/self-assessed language
       skill test), as embedded in a `CgSolvedPuzzlesByLanguage` entry. Only a single example
       has been observed so far, so field optionality beyond what's seen here is unconfirmed."""

    can_edit_name: bool
    """Whether the codingamer can still edit the name shown on the certification/diploma."""

    candidate_id: int
    """Numeric ID of the certification candidacy."""

    certification_history_id: int
    """Numeric ID of this specific certification attempt/history entry."""

    certification_number: int
    """The certification's displayed certificate number."""

    codingamer_id: int
    """The codingamer's numeric ID."""

    community_stats: list[int]
    """A histogram of community scores (bucket counts) used to compute `comparative_score`."""

    comparative_score: float
    """The codingamer's score as a percentile relative to the community, from 0.0 to 100.0."""

    diploma_preview_id: int
    """Binary image ID for a preview image of the certification diploma."""

    first_name: str
    """First name shown on the certification/diploma."""

    handle: str
    """Opaque handle identifying this certification (e.g. for a public diploma URL)."""

    language_name: str
    """Display name of the certified programming language, e.g. "Python 3"."""

    last_name: str
    """Last name shown on the certification/diploma."""

    legacy: bool
    """Whether this is a legacy-format certification."""

    lower_score_warning: bool
    """Whether the UI should warn that the score is on the lower end."""

    programming_language_id: CgSolutionLanguage
    """The certified programming language's ID, e.g. "Python3"."""

    score: float
    """The codingamer's raw certification score, from 0.0 to 100.0."""

    visible: bool
    """Whether the certification is publicly visible on the codingamer's profile."""

    _date: CgEpochMillis = Alias("date")
    """When the certification was obtained."""

    _last_try_date: CgEpochMillis = Alias("lastTryDate")
    """When the certification test was last attempted."""

    certification_type: str = Alias("type")
    """The kind of certification, e.g. "LANGUAGE". Only one value observed so far."""

    extra_data: CatchAll = field(default_factory=dict)

    @property
    def date(self) -> datetime:
        """See the field docstring for `_date`. Always UTC."""
        return self._date

    @date.setter
    def date(self, value: datetime) -> None:
        self._date = CgEpochMillis.upcast(value)

    @property
    def last_try_date(self) -> datetime:
        """See the field docstring for `_last_try_date`. Always UTC."""
        return self._last_try_date

    @last_try_date.setter
    def last_try_date(self, value: datetime) -> None:
        self._last_try_date = CgEpochMillis.upcast(value)


@dataclass
class CgSolvedPuzzlesByLanguage(JSONWizardX):
    """A codingamer's solved-puzzle count for a single programming language, as returned (in a
       bare JSON array) by countSolvedPuzzlesByProgrammingLanguage."""

    language_name: str
    """Display name of the programming language, e.g. "Python 3"."""

    logo_id: int
    """Binary image ID for the language's logo."""

    programming_language_id: CgSolutionLanguage
    """The programming language's ID, e.g. "Python3"."""

    puzzle_count: int
    """Number of puzzles the codingamer has solved using this language."""

    extra_data: CatchAll = field(default_factory=dict)

    certification: CgLanguageCertification | None = None
    """The codingamer's certification for this language, if any."""


@dataclass
class CgPuzzleOfTheWeek(JSONWizardX):
    """The current puzzle of the week, as returned by findPuzzleOfTheWeek."""

    puzzle_id: int
    """Numeric ID of the puzzle."""

    picture_binary_id: int
    """Binary image ID for the puzzle's featured picture."""

    extra_data: CatchAll = field(default_factory=dict)


@dataclass
class CgPuzzleMinimalProgress(JSONWizardX):
    """A codingamer's minimal progress summary for a single puzzle, as returned (in a bare JSON
       array) by findAllMinimalProgress. Covers every puzzle in some way related to the
       codingamer (not just solved/attempted ones--`submitted` distinguishes the two)."""

    id: int
    """Numeric ID of the puzzle."""

    level: str
    """Difficulty level, e.g. "easy", "medium", "hard", "expert", "tutorial", "multi", "optim",
       or a "codegolf-"-prefixed variant thereof."""

    community_creation: bool
    """Whether this is a community-created puzzle (as opposed to an official CodinGame puzzle)."""

    rank: int
    """Unclear precise semantics; observed as 0 in every example so far."""

    solved_count: int
    """Total number of codingamers who have solved this puzzle."""

    submitted: bool
    """Whether the codingamer has submitted a solution to this puzzle."""

    validator_score: int
    """The codingamer's score against the puzzle's validators, e.g. 100 for a fully-solved
       puzzle. 0 if not submitted."""

    _creation_time: CgEpochMillis = Alias("creationTime")
    """When the puzzle was created."""

    extra_data: CatchAll = field(default_factory=dict)

    feedback: CgPuzzleFeedback | None = None
    """Community feedback/rating summary for this puzzle. Not always present."""

    _last_activity: CgEpochMillis | None = Alias("lastActivity", default=None)
    """When the codingamer last interacted with this puzzle. Not always present--absent for
       puzzles the codingamer has never submitted a solution to."""

    @property
    def creation_time(self) -> datetime:
        """See the field docstring for `_creation_time`. Always UTC."""
        return self._creation_time

    @creation_time.setter
    def creation_time(self, value: datetime) -> None:
        self._creation_time = CgEpochMillis.upcast(value)

    @property
    def last_activity(self) -> datetime | None:
        """See the field docstring for `_last_activity`. Always UTC. None if not applicable."""
        return self._last_activity

    @last_activity.setter
    def last_activity(self, value: datetime | None) -> None:
        self._last_activity = None if value is None else CgEpochMillis.upcast(value)


@dataclass
class CgFollowingCodingamer(JSONWizardX):
    """A followed codingamer's profile snippet, as embedded in
       `CgFollowingPuzzleProgress.codin_gamer`. Only a single example has been observed so far,
       so optionality of fields beyond the core identity ones is a best guess based on similar
       profile snippets elsewhere in this API (e.g. `CgCodingamerFollower`)."""

    user_id: int
    """The codingamer's numeric ID."""

    pseudo: str
    """The codingamer's display name."""

    public_handle: str
    """The codingamer's opaque public handle string."""

    extra_data: CatchAll = field(default_factory=dict)

    avatar: int | None = None
    """The binary image ID of the codingamer's avatar image."""

    cover: int | None = None
    """The binary image ID of the codingamer's cover image."""

    level: int | None = None
    """The codingamer's current level."""

    tagline: str | None = None
    """Short freeform tagline shown on the codingamer's profile."""

    biography: str | None = None
    """Freeform biography text, as entered in the codingamer's profile."""


@dataclass
class CgFollowingPuzzleProgress(JSONWizardX):
    """A followed codingamer's progress on a single puzzle, as returned (in a bare JSON array)
       by findBestFollowingProgress. Empty if the followed codingamer(s) haven't attempted the
       given puzzle. Only a single followed codingamer has been observed in this session's test
       account, so it's unconfirmed whether more than one entry can be returned (e.g. one per
       followed codingamer who has attempted the puzzle) or how "best" is determined among them."""

    id: int
    """Numeric ID of the puzzle (matches the `puzzle_id` argument)."""

    level: str
    """Difficulty level, e.g. "easy", "medium"."""

    pretty_id: str
    """URL-friendly slug for the puzzle, e.g. "suguru-solver"."""

    details_page_url: str
    """Relative URL path to the puzzle's details/training page."""

    codin_gamer: CgFollowingCodingamer
    """The followed codingamer this progress belongs to."""

    topics: list[CgPuzzleTopicNode]
    """The puzzle's topic tree; see `CgPuzzleTopicNode`."""

    achievement_count: int
    """Total number of achievements associated with this puzzle."""

    done_achievement_count: int
    """Number of this puzzle's achievements the followed codingamer has unlocked."""

    validator_score: int
    """The followed codingamer's score against the puzzle's validators, e.g. 100 for a
       fully-solved puzzle."""

    xp_points: int
    """XP points awarded for solving this puzzle."""

    _last_activity: CgEpochMillis = Alias("lastActivity")
    """When the followed codingamer last interacted with this puzzle."""

    extra_data: CatchAll = field(default_factory=dict)

    @property
    def last_activity(self) -> datetime:
        """See the field docstring for `_last_activity`. Always UTC."""
        return self._last_activity

    @last_activity.setter
    def last_activity(self, value: datetime) -> None:
        self._last_activity = CgEpochMillis.upcast(value)


@dataclass
class CgGeneratedPuzzleSession(JSONWizardX):
    """The response to Puzzle/generateSessionFromPuzzlePrettyId. Confirmed live (2026-07-30):
       *not* a bare handle string, despite that being all that's actually needed downstream (see
       `CgPuzzleService.generate_session_from_puzzle_pretty_id`, which unwraps this to just
       `handle` for callers)."""

    handle: str
    """The test session handle--pass to `TestSession/startTestSession`."""

    extra_data: CatchAll = field(default_factory=dict)

    report_ready: bool | None = None
    """Unclear precise semantics; observed `False` in the only example so far."""

    direct: bool | None = None
    """Unclear precise semantics; observed `False` in the only example so far--possibly related
       to `CgTestSession.direct`."""


__all__ = [
    "CgFollowingCodingamer", "CgFollowingPuzzleProgress", "CgGeneratedPuzzleSession",
    "CgLanguageCertification", "CgLastActivityPuzzle", "CgPuzzleFeedback",
    "CgPuzzleMinimalProgress", "CgPuzzleOfTheWeek", "CgPuzzleTopicNode", "CgSolutionLanguage",
    "CgSolvedPuzzlesByLanguage",
]
