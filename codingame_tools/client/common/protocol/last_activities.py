"""
JSON-serializable dataclasses for the LastActivities service's getLastActivities Codingame API
method. `CgLastActivityPuzzle`/`CgLastActivityContributor`/`CgPuzzleFeedback` are also reused by
the Puzzle service's findProgressByIds/findAllMinimalProgress/findProgressByPrettyId methods
(see puzzle.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ....common.dataclass_wizard_x import Alias, CatchAll, CgEpochMillis, JSONWizardX
from .contribution import CgHtml


@dataclass
class CgPuzzleTopicNode(JSONWizardX):
    """A single node in a puzzle's topic tree (`CgLastActivityPuzzle.topics`). Topics are
       organized hierarchically--e.g. a top-level "Uncategorized"/"Algorithms" node with more
       specific child topics like "BFS"/"Pathfinding" underneath."""

    handle: str
    """Opaque (but human-readable) slug for the topic, e.g. "bfs", "uncategorized"."""

    value: str
    """Display name for the topic, e.g. "BFS", "Uncategorized"."""

    extra_data: CatchAll = field(default_factory=dict)

    children: list[CgPuzzleTopicNode] = field(default_factory=list)
    """Child topics nested under this one. Often empty."""

    category: str | None = None
    """The topic's difficulty category, e.g. "FUNDAMENTALS", "INTERMEDIATE", "ADVANCED". Not
       always present--observed absent for at least one top-level "Uncategorized" node."""


@dataclass
class CgLastActivityContributor(JSONWizardX):
    """The codingamer who authored a puzzle, as embedded in `CgLastActivityPuzzle.contributor`."""

    user_id: int
    """The contributor's numeric ID."""

    public_handle: str
    """The contributor's opaque public handle string."""

    extra_data: CatchAll = field(default_factory=dict)

    pseudo: str | None = None
    """The contributor's display name. Not always present--confirmed live (2026-07-31, via `cg
       puzzle import` resolving a title search to a puzzle with such a contributor): a
       never-configured/minimal account can omit `pseudo` entirely, same already-documented
       pattern as `CgCodingamer.pseudo`/`CgCodingamerFollower.pseudo`."""

    avatar: int | None = None
    """The binary image ID of the contributor's avatar image."""

    cover: int | None = None
    """The binary image ID of the contributor's cover image."""


@dataclass
class CgPuzzleFeedback(JSONWizardX):
    """Community feedback/rating summary for a puzzle, as embedded in
       `CgLastActivityPuzzle.feedback` and `CgPuzzleMinimalProgress.feedback`
       (puzzle.py)."""

    feedback_id: int
    """Numeric ID of the feedback record."""

    feedbacks: list[int]
    """A histogram of community feedback ratings (bucket counts), lowest rating first."""

    extra_data: CatchAll = field(default_factory=dict)

    codingamer_feedback: int | None = None
    """The requesting codingamer's own feedback rating for this puzzle, if given (scale
       unconfirmed--observed values are 1-5-ish star ratings). Not always present--absent from
       every example seen via Puzzle/findAllMinimalProgress."""


@dataclass
class CgLastActivityPuzzle(JSONWizardX):
    """A community puzzle summary, as embedded in a "PUZZLE"-type `CgLastActivity` entry
       (getLastActivities), and also returned directly by Puzzle/findProgressByIds (bare JSON
       array) and Puzzle/findProgressByPrettyId (bare JSON object--the richest of the three,
       populating `linked_achievements`/`moderators`/`statement`/`title_map`, which the other
       two never include)."""

    id: int
    """Numeric ID of the puzzle."""

    title: str
    """Display title of the puzzle."""

    pretty_id: str
    """URL-friendly slug for the puzzle, e.g. "logic-gates-detective"."""

    level: str
    """Difficulty level, e.g. "easy", "medium"."""

    details_page_url: str
    """Relative URL path to the puzzle's details/training page."""

    forum_link: str
    """Relative URL path (minus domain) to the puzzle's discussion forum thread."""

    feedback: CgPuzzleFeedback
    """Community feedback/rating summary for this puzzle."""

    topics: list[CgPuzzleTopicNode]
    """The puzzle's topic tree; see `CgPuzzleTopicNode`."""

    community_creation: bool
    """Whether this is a community-created puzzle (as opposed to an official CodinGame puzzle)."""

    achievement_count: int
    """Total number of achievements associated with this puzzle."""

    done_achievement_count: int
    """Number of this puzzle's achievements the requesting codingamer has unlocked."""

    attempt_count: int
    """Total number of attempts made on this puzzle across all codingamers."""

    solved_count: int
    """Total number of codingamers who have solved this puzzle."""

    rank: int
    """Unclear precise semantics; observed as 0 in all examples so far."""

    validator_score: int
    """Score achieved against the puzzle's validators, e.g. 100 for a fully-solved puzzle."""

    xp_points: int
    """XP points awarded for solving this puzzle."""

    puzzle_type: str = Alias("type")
    """The puzzle's own type discriminator, e.g. "CODE", "SOLO". Only observed via
       getLastActivities so far ("CODE"); findProgressByIds additionally returned "SOLO"."""

    _creation_time: CgEpochMillis = Alias("creationTime")
    """When the puzzle was created."""

    extra_data: CatchAll = field(default_factory=dict)

    contributor: CgLastActivityContributor | None = None
    """The codingamer who authored this puzzle, or `None` for a puzzle CodinGame provides itself.

       Confirmed live (2026-08-02) absent entirely--not `null`--for official puzzles, which is what
       `community_creation: False` marks. Only community-created puzzles have an author to name."""

    cover_binary_id: int | None = None
    """Binary image ID for the puzzle's cover image, or None for a puzzle that has no cover.

       Omitted entirely (not null) rather than defaulted--observed on 7 of 30 puzzles returned by a
       single Puzzle/findProgressByIds call (2026-08-03), so this is ordinary, not an edge case.
       Defaulted (rather than left required) for the same reason `contributor` is."""

    test_session_handle: str | None = None
    """Opaque handle for a test session against this puzzle. Not always present--absent (along
       with `last_activity`) for puzzles the codingamer has never attempted, observed via
       Puzzle/findProgressByIds."""

    _last_activity: CgEpochMillis | None = Alias("lastActivity", default=None)
    """When the requesting codingamer last interacted with this puzzle. Not always present; see
       `test_session_handle`."""

    linked_achievements: list[Any] | None = None
    """Achievements linked to this puzzle. Only observed as an empty list so far, so element
       shape is unknown. Only present via Puzzle/findProgressByPrettyId."""

    moderators: list[CgLastActivityContributor] | None = None
    """Codingamers who moderate this puzzle. Only present via Puzzle/findProgressByPrettyId."""

    statement: CgHtml | None = None
    """Rendered HTML of the puzzle's full problem statement. Only present via
       Puzzle/findProgressByPrettyId."""

    title_map: dict[str, str] | None = None
    """Localized title (locale ID as a string key, e.g. "1"/"2" -> title). Only present via
       Puzzle/findProgressByPrettyId."""

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
class CgLastActivity(JSONWizardX):
    """A single entry in a codingamer's recent activity feed, as returned (in a bare JSON array)
       by getLastActivities.

       Only `activity_type == "PUZZLE"` has been observed so far, in which case `puzzle` is
       populated. Other activity types (e.g. Clash of Code, contributions) likely exist and
       probably populate a different, not-yet-modeled field instead of `puzzle`--any such field
       would currently land in `extra_data` and be logged at DEBUG level (see JSONWizardX)."""

    activity_type: str = Alias("type")
    """Discriminator for the kind of activity; only "PUZZLE" observed so far."""

    extra_data: CatchAll = field(default_factory=dict)

    puzzle: CgLastActivityPuzzle | None = None
    """Populated when `activity_type == "PUZZLE"`."""


__all__ = [
    "CgLastActivity", "CgLastActivityContributor", "CgLastActivityPuzzle",
    "CgPuzzleFeedback", "CgPuzzleTopicNode",
]
