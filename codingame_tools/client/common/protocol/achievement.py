"""
JSON-serializable dataclasses for the Achievement service's findByCodingamerId
Codingame API method.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ....common.dataclass_wizard_x import Alias, CatchAll, CgEpochMillis, JSONWizardX

CgAchievementLevel = str
"""The tier of an achievement, e.g. "BRONZE", "SILVER", "GOLD", "PLATINUM"."""


@dataclass
class CgAchievement(JSONWizardX):
    """A single achievement unlocked by a codingamer, as returned (in a bare JSON array) by
       findByCodingamerId. Only unlocked achievements appear to be returned--`completion_time`
       was present on all 273 achievements observed for a long-active account."""

    id: str
    """Opaque (but human-readable) identifier for the achievement, e.g. "PZ_EXPERT_3"."""

    title: str
    """Display title of the achievement, e.g. "Damn I'm good"."""

    description: str
    """Freeform description of what the achievement requires."""

    category_id: str
    """Broad category the achievement belongs to, e.g. "puzzle", "coder", "contest", "social"."""

    group_id: str
    """Narrower grouping within the category, e.g. "puzzle-level", "coder-python3"."""

    level: CgAchievementLevel
    """The achievement's tier; see `CgAchievementLevel`."""

    points: int
    """Points awarded for unlocking this achievement."""

    weight: float
    """Rarity/prestige weight of the achievement; higher values observed for rarer-seeming
       achievements. Precise semantics/scale unconfirmed."""

    progress: int
    """The codingamer's progress towards `progress_max`. Can exceed `progress_max` (observed
       4 progress against a max of 3 for one achievement)--reason unclear."""

    progress_max: int
    """The progress value required to unlock the achievement."""

    puzzle_id: int
    """ID of a specific puzzle this achievement is tied to. Always observed as 0 so far
       (i.e., not tied to a specific puzzle) for every achievement seen."""

    image_binary_id: int
    """The binary image ID of the achievement's badge/icon image."""

    _completion_time: CgEpochMillis = Alias("completionTime")
    """When the codingamer unlocked this achievement."""

    extra_data: CatchAll = field(default_factory=dict)

    unit: str | None = None
    """Display unit for `progress`/`progress_max`, e.g. "very hard puzzles". Not always
       present--observed absent for most achievements."""

    unlock_text: str | None = None
    """Short display text describing how to unlock the achievement, e.g. "reach 100%". Not
       always present--observed absent for most achievements."""

    @property
    def completion_time(self) -> datetime:
        """See the field docstring for `_completion_time`. Always UTC."""
        return self._completion_time

    @completion_time.setter
    def completion_time(self, value: datetime) -> None:
        self._completion_time = CgEpochMillis.upcast(value)


__all__ = ["CgAchievement", "CgAchievementLevel"]
