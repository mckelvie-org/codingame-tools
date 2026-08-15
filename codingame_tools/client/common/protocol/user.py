"""
JSON-serializable dataclasses for the User service's updateUserProperties Codingame API method.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ....common.dataclass_wizard_x import Alias, CatchAll, CgEpochMillis, JSONWizardX


@dataclass
class CgUserProperties(JSONWizardX):
    """A partial set of a codingamer's account properties, for use with
       User/updateUserProperties. This is a request payload we construct, not a response we
       parse--every field is optional (defaulting to None), and (thanks to
       `Meta.skip_defaults`) any field left as None is omitted from the JSON payload entirely,
       matching the endpoint's documented "unchanged fields are omitted" semantics--only fields
       explicitly set are updated; all others are left alone server-side.

       Only one property is known and modeled so far. This schema is expected to grow
       incrementally as more properties are discovered; follow the existing field's pattern
       (private field + public `datetime`-typed property for epoch-millis values, or a plain
       Optional field otherwise)."""

    # `extra_data` is the first field here since every other field in this class is itself
    # optional--see the note in CgTopic (contribution.py) for why `extra_data` must
    # always be the first field with a real default.
    extra_data: CatchAll = field(default_factory=dict)

    _contributions_list_last_visit: CgEpochMillis | None = Alias(
            "contributionsListLastVisit", default=None)
    """When the codingamer last visited their contributions list page."""

    @property
    def contributions_list_last_visit(self) -> datetime | None:
        """See the field docstring for `_contributions_list_last_visit`. Always UTC."""
        return self._contributions_list_last_visit

    @contributions_list_last_visit.setter
    def contributions_list_last_visit(self, value: datetime | None) -> None:
        self._contributions_list_last_visit = None if value is None else CgEpochMillis.upcast(value)


__all__ = ["CgUserProperties"]
