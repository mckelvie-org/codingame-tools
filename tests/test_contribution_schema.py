"""Schema tests for `codingame_tools.client.common.protocol.contribution`.

Pure/local--no network. These pin down shapes confirmed against the live API, especially the ones
where a field is *absent* rather than null, which is what breaks a required-field dataclass.
"""

from __future__ import annotations

import pytest

from codingame_tools.client.common.protocol.contribution.schema import (
    CgContributionStatusChange,
    CgContributionStatusHistoryEntry,
)

# Every `data` shape observed in one real contribution's statusHistory (2026-08-12), covering its
# whole life: automatically refused for inactivity, moved back to pending by an edit, then accepted.
OBSERVED_STATUS_CHANGES = [
    ("REFUSED", {"author": "SYSTEM", "reason": "INACTIVITY"}),
    ("PENDING", {"author": "ACTION", "reason": "EDIT"}),
    ("ACCEPTED", {"author": "ACTION"}),
]


@pytest.mark.parametrize("status,data", OBSERVED_STATUS_CHANGES)
def test_every_observed_status_change_shape_decodes(status: str, data: dict[str, str]) -> None:
    change = CgContributionStatusChange.from_dict(data)

    assert change.author == data["author"]
    assert change.reason == data.get("reason")


def test_an_accepted_transition_has_no_reason() -> None:
    """The regression this exists for. `reason` was required, so the *first* call touching a
       contribution's status history after it was accepted failed to decode--and that includes
       `updateContribution`, meaning an accepted contribution could not be edited at all."""
    change = CgContributionStatusChange.from_dict({"author": "ACTION"})

    assert change.reason is None


def test_unknown_fields_land_in_the_catchall_and_nowhere_else() -> None:
    """`extra_data` sits first among the defaulted fields on purpose: dataclass_wizard 1.0.0
       silently mis-binds a defaulted field placed immediately before a CatchAll to the CatchAll's
       own value. Neither mypy nor ruff can see that, so it takes a real round trip."""
    change = CgContributionStatusChange.from_dict(
            {"author": "ACTION", "reason": "EDIT", "somethingNew": 1})

    assert change.author == "ACTION"
    assert change.reason == "EDIT"
    assert change.extra_data == {"somethingNew": 1}


def test_a_reasonless_change_still_decodes_inside_a_history_entry() -> None:
    """The failure arrived nested, not standalone--`statusHistory[].data`."""
    entry = CgContributionStatusHistoryEntry.from_dict(
            {"data": {"author": "ACTION"}, "date": 1786536570520, "status": "ACCEPTED"})

    assert entry.status == "ACCEPTED"
    assert entry.data.reason is None
    assert entry._date.year == 2026
