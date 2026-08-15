"""
JSON-serializable dataclasses for the Vote service's findVotableValuesById Codingame API method.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ....common.dataclass_wizard_x import CatchAll, JSONWizardX


@dataclass
class CgVotableValue(JSONWizardX):
    """A single votable's current up/down-vote tally, as returned (in a bare JSON array, one
       entry per requested votable ID) by Vote/findVotableValuesById--CodinGame's generic,
       no-privilege-required community vote mechanism, shared across multiple object types
       (confirmed for a contribution's `CgContribution.votable_id`; presumably also used
       elsewhere, e.g. comments). `up_votes`/`down_votes` here are the live values; `CgContribution.
       up_votes`/`down_votes` mirror the same tally as of whenever `findContribution` was last
       called.

       Deliberately distinct from CodinGame's moderator approve/reject gate, which decides
       whether a PENDING contribution gets published or rejected (requires moderator/high-level
       privilege, tracked with named voters)--that has no known API field yet; see this class's
       docstring in context for why they must not be conflated."""

    votable_id: int
    """The votable entity's ID (e.g. `CgContribution.votable_id`)."""

    up_votes: int
    """Current up-vote count."""

    down_votes: int
    """Current down-vote count."""

    user_vote_value: int
    """The querying codingamer's own vote on this votable. Only one example observed so far
       (`0`, no vote cast)--presumably `1`/`-1` for an up-vote/down-vote, unconfirmed."""

    extra_data: CatchAll = field(default_factory=dict)


__all__ = ["CgVotableValue"]
