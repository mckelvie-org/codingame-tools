"""
JSON-serializable dataclasses for the Search service's search Codingame API method.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ....common.dataclass_wizard_x import CatchAll, JSONWizardX

CgSearchResultType = str
"""The kind of object a search result refers to, e.g. "USER", "PUZZLE", "PLAYGROUND". Determines
   how `CgSearchResult.id` should be interpreted (an opaque public handle for "USER", a numeric
   ID as a string for other types so far observed). Only a few values have been observed so far,
   so this is left as a plain string rather than an enum pending broader coverage."""


@dataclass
class CgSearchResult(JSONWizardX):
    """A single search result, as returned (in a bare JSON array) by Search/search."""

    id: str
    """Opaque identifier for the matched object. For `type == "USER"`, this is the codingamer's
       opaque public handle (as used by e.g. findCodingamePointsStatsByHandle). For other types
       observed so far, this is a numeric ID rendered as a string."""

    name: str
    """Display name of the matched object, e.g. a codingamer's pseudo or a puzzle's title."""

    type: CgSearchResultType
    """The kind of object matched; see `CgSearchResultType`."""

    # `extra_data` is deliberately the first field with a default: dataclass_wizard 1.0.0 mis-binds
    # any defaulted field positioned immediately before it (silently, no error) to the CatchAll's
    # own value. Keeping it first among the defaulted fields makes that impossible.
    extra_data: CatchAll = field(default_factory=dict)

    image_binary_id: int | None = None
    """The binary image ID of the matched object's thumbnail/avatar image. Not always present--e.g.
       absent for some "USER" results."""

    level: str | None = None
    """Difficulty level, e.g. "easy", "medium". Only present for `type == "PUZZLE"` so far."""


__all__ = ["CgSearchResult", "CgSearchResultType"]
