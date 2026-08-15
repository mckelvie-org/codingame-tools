"""
JSON-serializable dataclasses for the Quest service's findQuestMap Codingame API method.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ....common.dataclass_wizard_x import Alias, CatchAll, CgEpochMillis, JSONWizardX

CgQuestType = str
"""The category of a quest, e.g. "INTRODUCTION", "ALGORITHMS", "OPTIMIZATION", "AI",
   "CLASH_OF_CODE", "CONTRIBUTE", "LEARNING"."""

CgQuestRewardType = str
"""The kind of reward for completing a quest, e.g. "XP", "TEXT"."""

CgCertificationCategory = str
"""The category of a quest certification, e.g. "ALGORITHMS", "OPTIMIZATION", "AI",
   "CODING_SPEED", "COLLABORATION"."""

CgCertificationLevel = str
"""The tier of a quest certification, e.g. "BRONZE", "SILVER", "GOLD", "LEGEND"."""


@dataclass
class CgQuestMapPoint(JSONWizardX):
    """A single waypoint coordinate in a `CgQuestMapLink`'s path."""

    x: float
    y: float

    extra_data: CatchAll = field(default_factory=dict)


@dataclass
class CgQuestMapLink(JSONWizardX):
    """A directed edge between two quest map nodes, as returned (in `CgQuestMap.links`) by
       findQuestMap."""

    from_node_id: int
    """`CgQuestMapNode.id` of the edge's origin node."""

    to_node_id: int
    """`CgQuestMapNode.id` of the edge's destination node."""

    path: list[CgQuestMapPoint]
    """Intermediate waypoints for drawing a curved/routed edge on the quest map; often empty
       (a straight line between the two nodes)."""

    extra_data: CatchAll = field(default_factory=dict)


@dataclass
class CgQuestReward(JSONWizardX):
    """A single reward granted for completing a quest, as embedded in
       `CgQuestDetails.rewards`."""

    id: int
    """Numeric ID of the reward."""

    reward_type: CgQuestRewardType = Alias("type")
    """The kind of reward; see `CgQuestRewardType`. Determines the shape of `data`."""

    extra_data: CatchAll = field(default_factory=dict)

    data: dict[str, Any] | None = None
    """Reward-type-specific payload; e.g. `{"xpPoints": 25}` for an "XP" reward. Left as a raw
       dict rather than a further-typed class pending examples of more reward types."""


@dataclass
class CgQuestCertification(JSONWizardX):
    """A certification awarded for completing a quest, as embedded in
       `CgQuest.certification`."""

    category: CgCertificationCategory
    """The certification's category; see `CgCertificationCategory`."""

    level: CgCertificationLevel
    """The certification's tier; see `CgCertificationLevel`."""

    description: str
    """Freeform description of what the certification represents."""

    extra_data: CatchAll = field(default_factory=dict)


@dataclass
class CgQuestDetails(JSONWizardX):
    """Full details for a quest, as embedded in `CgQuest.details`. Not always present--e.g.
       observed absent for some "CLASH_OF_CODE" quests that had neither details nor a
       certification."""

    handle: str
    """Opaque (but human-readable) slug for the quest, e.g. "optim-zombies"."""

    title: str
    """Display title of the quest, e.g. "Discover optimization games"."""

    description: str
    """Longer freeform description of the quest."""

    progress_title: str
    """Display text describing the completion goal, e.g. "Get a score of 40k+ points in Code
       vs Zombies"."""

    progress_max: int
    """The progress value that represents 100% completion; compare against
       `CgCodingamerQuest.progress`."""

    rewards: list[CgQuestReward]
    """Rewards granted for completing the quest."""

    extra_data: CatchAll = field(default_factory=dict)

    url: str | None = None
    """Relative URL path to the quest's target page (e.g. a multiplayer game). Not always
       present--observed absent for a few quests."""


@dataclass
class CgQuest(JSONWizardX):
    """A quest definition, as embedded in `CgQuestMapNode.quest`. `details` and/or
       `certification` may each independently be present, both present, or both absent,
       depending on the quest."""

    id: int
    """Numeric ID of the quest. Matches the enclosing `CgQuestMapNode.id`."""

    quest_type: CgQuestType = Alias("type")
    """The quest's category; see `CgQuestType`."""

    extra_data: CatchAll = field(default_factory=dict)

    details: CgQuestDetails | None = None
    """Full quest details (title, description, progress goal, rewards). See class docstring."""

    certification: CgQuestCertification | None = None
    """The certification awarded for completing this quest, if any. See class docstring."""


@dataclass
class CgCodingamerQuest(JSONWizardX):
    """The requesting codingamer's own progress on a quest, as embedded in
       `CgQuestMapNode.codingamer_quest`. Absent from the enclosing node entirely for quests
       the codingamer has not started."""

    quest_id: int
    """`CgQuest.id` this progress record refers to."""

    progress: int
    """Current progress value; compare against `CgQuestDetails.progress_max`."""

    _creation_time: CgEpochMillis = Alias("creationTime")
    """When the codingamer started this quest."""

    extra_data: CatchAll = field(default_factory=dict)

    _completion_time: CgEpochMillis | None = Alias("completionTime", default=None)
    """When the codingamer completed this quest. None if not yet completed."""

    _loot_time: CgEpochMillis | None = Alias("lootTime", default=None)
    """When the codingamer claimed/looted this quest's rewards. None if not yet claimed."""

    @property
    def creation_time(self) -> datetime:
        """See the field docstring for `_creation_time`. Always UTC."""
        return self._creation_time

    @creation_time.setter
    def creation_time(self, value: datetime) -> None:
        self._creation_time = CgEpochMillis.upcast(value)

    @property
    def completion_time(self) -> datetime | None:
        """See the field docstring for `_completion_time`. Always UTC. None if not completed."""
        return self._completion_time

    @completion_time.setter
    def completion_time(self, value: datetime | None) -> None:
        self._completion_time = None if value is None else CgEpochMillis.upcast(value)

    @property
    def loot_time(self) -> datetime | None:
        """See the field docstring for `_loot_time`. Always UTC. None if not yet claimed."""
        return self._loot_time

    @loot_time.setter
    def loot_time(self, value: datetime | None) -> None:
        self._loot_time = None if value is None else CgEpochMillis.upcast(value)


@dataclass
class CgQuestMapNode(JSONWizardX):
    """A single node in a codingamer's quest map, as returned (in `CgQuestMap.nodes`) by
       findQuestMap. Only `node_type == "QUEST"` has been observed so far."""

    id: int
    """Numeric ID of the node. Matches `quest.id`, and is referenced by `CgQuestMapLink`."""

    x: int
    """Horizontal position of the node on the quest map."""

    y: int
    """Vertical position of the node on the quest map."""

    quest: CgQuest
    """The quest this node represents."""

    node_type: str = Alias("type")
    """Discriminator for the kind of node; only "QUEST" observed so far."""

    extra_data: CatchAll = field(default_factory=dict)

    codingamer_quest: CgCodingamerQuest | None = None
    """The requesting codingamer's progress on this quest. Absent if not yet started."""


@dataclass
class CgQuestMap(JSONWizardX):
    """The complete response to findQuestMap: a codingamer's quest map as a graph of nodes and
       directed links between them."""

    nodes: list[CgQuestMapNode]
    """All quest nodes on the map."""

    links: list[CgQuestMapLink]
    """Directed edges connecting nodes on the map."""

    extra_data: CatchAll = field(default_factory=dict)


__all__ = [
    "CgCertificationCategory", "CgCertificationLevel", "CgCodingamerQuest", "CgQuest",
    "CgQuestCertification", "CgQuestDetails", "CgQuestMap", "CgQuestMapLink",
    "CgQuestMapNode", "CgQuestMapPoint", "CgQuestReward", "CgQuestRewardType", "CgQuestType",
]
