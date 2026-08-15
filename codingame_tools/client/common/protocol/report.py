"""
JSON-serializable dataclasses for the Report service's findReportBySubmission Codingame API
method.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ....common.dataclass_wizard_x import Alias, CatchAll, CgEpochMillis, JSONWizardX
from .last_activities import CgPuzzleFeedback


@dataclass
class CgReportPuzzleProgress(JSONWizardX):
    """Lightweight puzzle progress summary, as embedded in
       `CgSubmissionReport.puzzle_progress`. A much smaller summary than `CgLastActivityPuzzle`
       (last_activities.py) or `CgPuzzleMinimalProgress` (puzzle.py)."""

    id: int
    """Numeric ID of the puzzle."""

    achievement_count: int
    """Total number of achievements associated with this puzzle."""

    done_achievement_count: int
    """Number of this puzzle's achievements the codingamer has unlocked."""

    validator_score: int
    """Unclear why this differs from the enclosing `CgSubmissionReport.score`--observed as 0
       here despite a 100.0 `score`/`best_score` on the same report. Possibly stale/unrelated to
       this specific submission."""

    extra_data: CatchAll = field(default_factory=dict)


@dataclass
class CgValidatorResult(JSONWizardX):
    """A single server-side validator's result for a submission, as embedded in
       `CgSubmissionReport.validators`."""

    method_name: str
    """Internal name of the validator method, e.g. "Validator_1"."""

    name: str
    """Display name/label for the validator test case, e.g. "Miguel de Cervantes"."""

    difficulty: int
    """Relative difficulty/weight of this validator, e.g. 100."""

    success: bool
    """Whether the submission passed this validator."""

    extra_data: CatchAll = field(default_factory=dict)


@dataclass
class CgSubmissionReport(JSONWizardX):
    """The complete response to Report/findReportBySubmission: a report on a single puzzle
       submission's results.

       CAUTION, confirmed live (2026-07-31, and again 2026-08-01 for a puzzle with no prior
       submission at all--see `best_score`): calling `findReportBySubmission` right after
       `TestSession/submit` can race server-side grading--every field below except
       `validator_shareable` has been observed entirely absent (not merely `null`) in some
       partial-report snapshot. All of them are therefore Optional here except
       `validator_shareable`, the only field confirmed present in every observed case so far; a
       report is only "done" once they're all populated. See
       `CgReportServiceHelper.find_report_by_submission_when_ready`, which polls until that's
       true (or a timeout elapses) instead of returning a partial report."""

    validator_shareable: bool
    """Whether this submission's validator results are eligible to be shared."""

    # `extra_data` is deliberately the first field with a default: dataclass_wizard 1.0.0 mis-binds
    # any defaulted field positioned immediately before it (silently, no error) to the CatchAll's
    # own value. Keeping it first among the defaulted fields makes that impossible.
    extra_data: CatchAll = field(default_factory=dict)

    best_score: float | None = None
    """The codingamer's best-ever validator score for this puzzle, 0.0 to 100.0 (may be higher
       than `score` if this submission wasn't their best attempt). Confirmed live (2026-08-01)
       absent--not just this submission's own score, but this field specifically--for a puzzle
       the codingamer had never before submitted, i.e. there's no historical "best" yet at the
       moment this was polled. Absent while grading is still in progress--see the class
       docstring."""

    codingamer_id: int | None = None
    """The submitting codingamer's numeric ID. Absent while grading is still in progress--see the
       class docstring."""

    submission_id: int | None = None
    """Numeric ID of the submission this report is for (matches the `submission_id` argument).
       Absent while grading is still in progress--see the class docstring."""

    score: float | None = None
    """The submission's validator score, 0.0 to 100.0. Absent while grading is still in
       progress--see the class docstring."""

    achievements_completed: bool | None = None
    """Whether all achievements for this puzzle have been completed by the codingamer. Absent
       while grading is still in progress--see the class docstring."""

    shared: bool | None = None
    """Whether the codingamer has publicly shared their solution. Absent while grading is still
       in progress--see the class docstring."""

    puzzle_progress: CgReportPuzzleProgress | None = None
    """Lightweight puzzle progress summary. Absent while grading is still in progress--see the
       class docstring."""

    validators: list[CgValidatorResult] | None = None
    """Per-validator results for this submission. Absent while grading is still in progress--see
       the class docstring."""

    achievements: list[Any] | None = None
    """Achievements unlocked by this submission. Only observed as an empty list so far, so
       element shape is unknown otherwise. Absent while grading is still in progress--see the
       class docstring."""

    feedback: CgPuzzleFeedback | None = None
    """Community feedback/rating summary for the puzzle. Not confirmed to always be present
       (only a single example observed so far)."""

    _completed_time: CgEpochMillis | None = Alias("completedTime", default=None)
    """When this submission was completed. Absent while grading is still in progress--see the
       class docstring."""

    @property
    def completed_time(self) -> datetime | None:
        """See the field docstring for `_completed_time`. Always UTC. `None` if not yet done."""
        return self._completed_time

    @completed_time.setter
    def completed_time(self, value: datetime | None) -> None:
        self._completed_time = None if value is None else CgEpochMillis.upcast(value)

    def is_ready(self) -> bool:
        """Whether grading has finished--i.e. every field described as "absent while grading is
           still in progress" above is now populated. See the class docstring and
           `CgReportServiceHelper.find_report_by_submission_when_ready`."""
        return (
                self.best_score is not None
                and self.codingamer_id is not None and self.submission_id is not None
                and self.score is not None and self.achievements_completed is not None
                and self.shared is not None and self.puzzle_progress is not None
                and self.validators is not None and self.achievements is not None
                and self._completed_time is not None
            )


__all__ = [
    "CgPuzzleFeedback", "CgReportPuzzleProgress", "CgSubmissionReport", "CgValidatorResult",
]
