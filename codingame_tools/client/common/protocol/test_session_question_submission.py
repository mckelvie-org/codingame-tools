"""
JSON-serializable dataclasses for the TestSessionQuestionSubmission service's findAllSubmissions
Codingame API method.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ....common.dataclass_wizard_x import CatchAll, JSONWizardX
from .typedefs import CgSolutionLanguage


@dataclass
class CgTestSessionQuestionSubmission(JSONWizardX):
    """A single past submission summary for a puzzle, as returned (in a bare JSON array) by
       findAllSubmissions--most recent first."""

    test_session_question_submission_id: int
    """Numeric ID of the submission (matches `CgSubmissionReport.submission_id`/the ID returned
       by `TestSession.submit`)."""

    programming_language_id: CgSolutionLanguage
    """The programming language the submission was written in."""

    score: float
    """The submission's validator score, 0.0 to 100.0."""

    time_elapsed: int
    """Milliseconds elapsed working on the puzzle at the time of this submission (observed
       increasing across successive submissions for the same puzzle)."""

    extra_data: CatchAll = field(default_factory=dict)


__all__ = ["CgSolutionLanguage", "CgTestSessionQuestionSubmission"]
