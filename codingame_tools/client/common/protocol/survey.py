"""
JSON-serializable dataclasses for the Survey service's findSurvey Codingame API method.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ....common.dataclass_wizard_x import CatchAll, JSONWizardX


@dataclass
class CgSurvey(JSONWizardX):
    """A survey to potentially show a codingamer, as returned by findSurvey.

       UNVERIFIED PLACEHOLDER: no populated example was available to test against at the time
       this was written--every account tested (including the maintainer's own) returned a bare
       `null`, seemingly meaning "no survey currently applicable". Every field observed on first
       live use will show up in `extra_data` (see JSONWizardX's debug logging of unrecognized
       fields)--fill in real fields here once that happens, following the pattern used for the
       other schema classes in this project."""

    extra_data: CatchAll = field(default_factory=dict)


__all__ = ["CgSurvey"]
