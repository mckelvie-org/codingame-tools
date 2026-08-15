"""
JSON-serializable dataclasses for the FeaturedEvent service's
findUpcomingAndOngoingFeaturedEvents, findClashSlots, and findByHandle Codingame API methods.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ....common.dataclass_wizard_x import Alias, CatchAll, CgEpochMillis, JSONWizardX
from .clash_of_code import CgClashMode
from .typedefs import CgSolutionLanguage

CgFeaturedEventType = str
"""The kind of featured event, e.g. "CLASH_OF_CODE", "PUZZLE"."""


@dataclass
class CgFeaturedEvent(JSONWizardX):
    """A scheduled/ongoing site-wide featured event, as returned by
       findUpcomingAndOngoingFeaturedEvents and findByHandle."""

    id: int
    """Numeric ID of the featured event."""

    handle: str
    """Opaque handle for the event. For a `event_type == "CLASH_OF_CODE"` event, this was NOT
       accepted by ClashOfCode/findClashByHandle (rejected with a 422)--it appears to be a
       FeaturedEvent-specific handle, not the handle of a joinable clash instance."""

    draft: bool
    """Whether the event is still in draft (not yet published)."""

    participant_count: int
    """Number of codingamers registered/participating."""

    event_type: CgFeaturedEventType = Alias("type")
    """The kind of event; see `CgFeaturedEventType`."""

    _publish_time: CgEpochMillis = Alias("publishTime")
    """When the event was/will be published (made visible)."""

    _start_time: CgEpochMillis = Alias("startTime")
    """When the event starts/started."""

    _end_time: CgEpochMillis = Alias("endTime")
    """When the event ends/ended."""

    extra_data: CatchAll = field(default_factory=dict)

    registered: bool | None = None
    """Whether the requesting codingamer is registered for this event. Only present when the
       event was looked up in a codingamer-specific context (e.g.
       findUpcomingAndOngoingFeaturedEvents); absent from findByHandle, which has no
       codingamer context to compute this against."""

    _review_date: CgEpochMillis | None = Alias("reviewDate", default=None)
    """Unclear precise semantics; observed only for a "PUZZLE" event so far, absent for
       "CLASH_OF_CODE". Possibly the date results/scoring are reviewed or finalized."""

    @property
    def publish_time(self) -> datetime:
        """See the field docstring for `_publish_time`. Always UTC."""
        return self._publish_time

    @publish_time.setter
    def publish_time(self, value: datetime) -> None:
        self._publish_time = CgEpochMillis.upcast(value)

    @property
    def start_time(self) -> datetime:
        """See the field docstring for `_start_time`. Always UTC."""
        return self._start_time

    @start_time.setter
    def start_time(self, value: datetime) -> None:
        self._start_time = CgEpochMillis.upcast(value)

    @property
    def end_time(self) -> datetime:
        """See the field docstring for `_end_time`. Always UTC."""
        return self._end_time

    @end_time.setter
    def end_time(self, value: datetime) -> None:
        self._end_time = CgEpochMillis.upcast(value)

    @property
    def review_date(self) -> datetime | None:
        """See the field docstring for `_review_date`. Always UTC. None if not applicable."""
        return self._review_date

    @review_date.setter
    def review_date(self, value: datetime | None) -> None:
        self._review_date = None if value is None else CgEpochMillis.upcast(value)


@dataclass
class CgClashSlot(JSONWizardX):
    """A single scheduled Clash of Code slot belonging to a featured event, as returned (in a
       bare JSON array) by findClashSlots. `clash_handle` (distinct from the parent
       `CgFeaturedEvent.handle`/`CgFeaturedEvent.id`) can be passed to
       ClashOfCode/findClashByHandle to fetch full details/results for this specific clash."""

    clash_handle: str
    """Opaque handle for this specific clash instance--see class docstring."""

    clash_mode: CgClashMode
    """The clash's game mode; see `CgClashMode`."""

    id: int
    """Numeric ID of the clash slot."""

    programming_languages: list[CgSolutionLanguage]
    """Programming languages available for this clash slot."""

    subscribed_to_notifications: bool
    """Whether the requesting codingamer has subscribed to notifications for this slot."""

    _end_time: CgEpochMillis = Alias("endTime")
    """When this clash slot ends."""

    _joinable_time: CgEpochMillis = Alias("joinableTime")
    """When this clash slot becomes joinable (typically shortly before `start_time`)."""

    _start_time: CgEpochMillis = Alias("startTime")
    """When this clash slot starts."""

    extra_data: CatchAll = field(default_factory=dict)

    @property
    def end_time(self) -> datetime:
        """See the field docstring for `_end_time`. Always UTC."""
        return self._end_time

    @end_time.setter
    def end_time(self, value: datetime) -> None:
        self._end_time = CgEpochMillis.upcast(value)

    @property
    def joinable_time(self) -> datetime:
        """See the field docstring for `_joinable_time`. Always UTC."""
        return self._joinable_time

    @joinable_time.setter
    def joinable_time(self, value: datetime) -> None:
        self._joinable_time = CgEpochMillis.upcast(value)

    @property
    def start_time(self) -> datetime:
        """See the field docstring for `_start_time`. Always UTC."""
        return self._start_time

    @start_time.setter
    def start_time(self, value: datetime) -> None:
        self._start_time = CgEpochMillis.upcast(value)


__all__ = [
    "CgClashMode", "CgClashSlot", "CgFeaturedEvent", "CgFeaturedEventType", "CgSolutionLanguage",
]
