"""
JSON-serializable dataclasses for the TestSession service's startTestSession and play Codingame
API methods.

This is the API called by the web client when a codingamer clicks "Solve in IDE" on a puzzle
(startTestSession), and when they click "Test"/"Run" to run their code against a single test
case (play, as opposed to a full "Submit"). Only a single example (a previously-solved community
puzzle) has been observed so far, so field optionality beyond what's noted below is unconfirmed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ....common.dataclass_wizard_x import Alias, CatchAll, JSONWizardX
from .contribution import CgHtml, CgStubGenerator
from .last_activities import CgLastActivityContributor
from .typedefs import CgSolutionLanguage


@dataclass
class CgTestSessionAnswer(JSONWizardX):
    """The codingamer's current saved answer for a test session, as embedded in
       `CgTestSessionQuestion.answer`.

       `code`/`programming_language_id` are both Optional--confirmed live (2026-07-31): `answer`
       itself can be present as an empty JSON object (`{}`), NOT `null`/absent, even though no
       solution was ever submitted--i.e. `CgTestSessionQuestion.answer` being non-None does NOT
       by itself mean a real answer exists; check `code`/`programming_language_id` here too. The
       exact trigger for empty-object vs. `null`/absent isn't confirmed--the one observed case
       had a test session already created (the puzzle had been opened/viewed in the IDE) but no
       solution ever submitted, which suggests "a session exists for this puzzle" (not literally
       "ever attempted") may be what actually determines it; not fully verified either way."""

    extra_data: CatchAll = field(default_factory=dict)

    code: str | None = None
    """The codingamer's saved source code. `None` for the empty placeholder object--see class
       docstring."""

    programming_language_id: CgSolutionLanguage | None = None
    """The programming language `code` is written in. Same `None`-for-empty-placeholder caveat as
       `code`."""


@dataclass
class CgAvailableLanguage(JSONWizardX):
    """A programming language available to solve a puzzle in, as embedded in
       `CgTestSessionQuestionDetails.available_languages`."""

    id: CgSolutionLanguage
    """The language's ID, e.g. "Python3"."""

    name: str
    """The language's display name, e.g. "Python 3"."""

    extra_data: CatchAll = field(default_factory=dict)


@dataclass
class CgTestSessionContribution(JSONWizardX):
    """Lightweight contribution metadata, as embedded in
       `CgTestSessionQuestionDetails.contribution`. A much smaller summary than
       `CgContribution` (contribution.py)."""

    id: int
    """The contribution's unique identifier."""

    public_handle: str
    """The contribution's opaque public handle."""

    status: str
    """The contribution's status, e.g. "ACCEPTED"."""

    moderators: list[CgLastActivityContributor]
    """Codingamers who moderate this contribution."""

    contribution_type: str = Alias("type")
    """The type of the contribution, e.g. "PUZZLE_INOUT"."""

    extra_data: CatchAll = field(default_factory=dict)


@dataclass
class CgTestSessionTestCase(JSONWizardX):
    """A single test case, as embedded in `CgTestSessionQuestionDetails.test_cases`. Unlike
       `CgTestCase` (contribution.py), only references binary IDs for the input/output
       content rather than including it inline.

       NOTE: checked for schema overlap with `CgTestCase` (2026-07-26)--the two share zero field
       names (this one: index/input_binary_id/output_binary_id/label; `CgTestCase`:
       title/test_in/test_out/is_test/is_validator/need_validation). They represent the same
       underlying puzzle test case from two different API contexts (solve/IDE vs.
       contribution-authoring), so a shared conceptual model (e.g. one canonical "puzzle test
       case" type that each endpoint's shape adapts into/out of) may be worth revisiting if a
       third endpoint's test-case shape turns up, but there wasn't enough justification to force
       a merge from just these two structurally-disjoint shapes alone."""

    index: int
    """1-based position of this test case among the puzzle's test cases."""

    input_binary_id: int
    """Binary ID of the test case's input content."""

    output_binary_id: int
    """Binary ID of the test case's expected output content."""

    label: str
    """Display label for the test case, e.g. "Don Quixote"."""

    extra_data: CatchAll = field(default_factory=dict)


@dataclass
class CgTestSessionQuestionDetails(JSONWizardX):
    """Full puzzle/question details, as embedded in `CgTestSessionQuestion.question`."""

    id: int
    """Numeric ID of the question (matches the enclosing puzzle's question ID, not necessarily
       `CgTestSessionPuzzle.id`)."""

    title: str
    """Display title of the puzzle."""

    statement: CgHtml
    """Rendered HTML of the puzzle's full problem statement."""

    stub_generator: CgStubGenerator
    """Stub-generation script for this puzzle; see `CgStubGenerator`."""

    duration: int
    """Unclear precise semantics (not documented)--observed as a very large number of
       milliseconds; possibly a maximum/elapsed session duration."""

    index: int
    """Unclear precise semantics; observed as 0 in the only example so far."""

    initial_id: int
    """Unclear precise semantics; observed equal to `id` in the only example so far."""

    user_id: int
    """Unclear precise semantics; observed equal to `contributor.user_id` in the only example
       so far--possibly redundant with it."""

    available_languages: list[CgAvailableLanguage]
    """Programming languages available to solve this puzzle in."""

    test_cases: list[CgTestSessionTestCase]
    """The puzzle's test cases (metadata only--content is referenced by binary ID)."""

    question_type: str = Alias("type")
    """Discriminator for the kind of question, e.g. "MULTIPLE_LANGUAGES". Only one value
       observed so far."""

    extra_data: CatchAll = field(default_factory=dict)

    contributor: CgLastActivityContributor | None = None
    """The codingamer who authored this puzzle, or `None` for a puzzle CodinGame itself provides.

       Confirmed live (2026-08-02) absent entirely--not `null`--for an official puzzle
       ("Temperatures"), which also reports the sentinel `user_id: -2`. Only community
       *contributions* have an author to name."""

    contribution: CgTestSessionContribution | None = None
    """Lightweight contribution metadata, or `None` for a puzzle CodinGame itself provides--an
       official puzzle was never a community contribution, so there's nothing to describe.

       Confirmed live (2026-08-02) absent entirely for "Temperatures". Note this is the only source
       of a puzzle's `contribution_type`, so that is simply unknowable for an official puzzle--see
       `codingame_tools.puzzle_manager.manager.CgPuzzleManager.import_`, which treats absence as a
       standard in/out puzzle rather than refusing every official puzzle outright."""


@dataclass
class CgTestSessionQuestion(JSONWizardX):
    """The active question in a test session, as returned in `CgTestSession.current_question`."""

    question: CgTestSessionQuestionDetails
    """Full puzzle/question details."""

    extra_data: CatchAll = field(default_factory=dict)

    last_submission_id: int | None = None
    """Numeric ID of the codingamer's last submission for this question. Confirmed live
       (2026-07-31) to be absent entirely (not just `null`) when no solution has ever been
       submitted--same underlying "session exists, never submitted" case as `answer`'s
       empty-object caveat; see `CgTestSessionAnswer`'s docstring."""

    answer: CgTestSessionAnswer | None = None
    """The codingamer's current saved answer. Populated (with the codingamer's own previously
       submitted code) once a solution has been submitted. Confirmed live (2026-07-31), this
       field can also be present-but-empty (a `CgTestSessionAnswer` with `code`/
       `programming_language_id` both `None`) rather than `None`/absent, when no solution was
       ever submitted--check those two fields, not just `answer is None`, to tell "has a real
       saved answer" from "no answer yet." See `CgTestSessionAnswer`'s docstring for what is/
       isn't confirmed about exactly when each shape (`null` vs. empty object) occurs."""


@dataclass
class CgTestSessionPuzzle(JSONWizardX):
    """Lightweight puzzle metadata, as embedded in `CgTestSession.puzzle`. A much smaller
       summary than `CgLastActivityPuzzle` (last_activities.py)."""

    id: int
    """Numeric ID of the puzzle."""

    handle: str
    """Opaque handle for the puzzle (distinct from `CgTestSession.test_session_handle`)."""

    pretty_id: str
    """URL-friendly slug for the puzzle, e.g. "literary-alfabet-soupe"."""

    title: str
    """Display title of the puzzle."""

    level: str
    """Difficulty level, e.g. "easy", "medium"."""

    details_page_url: str
    """Relative URL path to the puzzle's details/training page."""

    forum_post_id: str
    """Relative URL path (minus domain) to the puzzle's discussion forum thread."""

    extra_data: CatchAll = field(default_factory=dict)

    hints: list[Any] | None = None
    """Hints available for this puzzle. Only observed as an empty list so far, so element shape
       is unknown."""


@dataclass
class CgTestSessionQuestionSummary(JSONWizardX):
    """A single entry in `CgTestSession.questions`--a summary of one question in the test
       session (as opposed to `CgTestSession.current_question`'s full details)."""

    question_id: int
    """Numeric ID of the question; matches `CgTestSessionQuestionDetails.id` for the current
       question."""

    title: str
    """Display title of the question."""

    has_result: bool
    """Whether the codingamer has a recorded result (e.g. a submission) for this question."""

    extra_data: CatchAll = field(default_factory=dict)

    score: float | None = None
    """The codingamer's score for this question, from 0.0 to 1.0. Confirmed live (2026-07-31) to
       be absent entirely when `has_result` is `False`--nothing to score yet."""


@dataclass
class CgTestSession(JSONWizardX):
    """The complete response to TestSession/startTestSession--the interactive IDE session
       state created when a codingamer clicks "Solve in IDE" on a puzzle."""

    test_session_handle: str
    """Opaque handle identifying this test session (matches the `handle` argument passed in)."""

    test_session_id: int
    """Numeric ID of the test session."""

    user_id: int
    """The codingamer's numeric ID."""

    test_type: str
    """The kind of test session, e.g. "PUZZLE"."""

    direct: bool
    """Unclear precise semantics; observed False in the only example so far."""

    need_account: bool
    """Whether an account is required to use this test session (e.g. False for a fully public
       trial session)."""

    shareable: bool
    """Whether the test session can be shared (e.g. via a public results link)."""

    show_replay_prompt: bool
    """Whether the UI should prompt the codingamer to replay/retry."""

    current_question: CgTestSessionQuestion
    """The active question in this test session."""

    puzzle: CgTestSessionPuzzle
    """Lightweight metadata for the puzzle this test session is for."""

    questions: list[CgTestSessionQuestionSummary]
    """Summary of all questions in this test session (usually just the one in
       `current_question`)."""

    extra_data: CatchAll = field(default_factory=dict)


@dataclass
class CgMultipleLanguagesTestParams(JSONWizardX):
    """Test-case selection parameters specific to puzzles whose
       `CgTestSessionQuestionDetails.question_type == "MULTIPLE_LANGUAGES"`, as embedded in
       `CgPlayRequest.multiple_languages`. Other question types likely need a different,
       not-yet-modeled params object instead."""

    test_index: int
    """1-based index selecting which of the puzzle's test cases to run against; see
       `CgTestSessionTestCase.index`."""

    extra_data: CatchAll = field(default_factory=dict)


@dataclass
class CgPlayRequest(JSONWizardX):
    """The request payload for TestSession/play: run a codingamer's code against a single test
       case within a test session."""

    code: str
    """The source code to run."""

    programming_language_id: CgSolutionLanguage
    """The programming language `code` is written in."""

    extra_data: CatchAll = field(default_factory=dict)

    multiple_languages: CgMultipleLanguagesTestParams | None = None
    """Test-case selection, for `question_type == "MULTIPLE_LANGUAGES"` puzzles; see
       `CgMultipleLanguagesTestParams`. Presumably required in that case and unset/differently-
       shaped otherwise, but this is unconfirmed--only one question type has been observed."""


@dataclass
class CgSubmitRequest(JSONWizardX):
    """The request payload for TestSession/submit: submit a final solution to a puzzle for
       credit, validated against the puzzle's private validator test cases (as opposed to
       `TestSession/play`, which only runs one local test case). Unlike `CgPlayRequest`, no
       `multiple_languages`-style test-case selector is used here--submission validates against
       every validator test case, not one chosen local test."""

    code: str
    """The source code to submit."""

    programming_language_id: CgSolutionLanguage
    """The programming language `code` is written in."""

    extra_data: CatchAll = field(default_factory=dict)


@dataclass
class CgPlayStackFrame(JSONWizardX):
    """A single stack frame in `CgPlayError.stacktrace`."""

    container: str
    """Source file the frame is in, e.g. "Answer.py"."""

    function: str
    """Name of the function/scope the frame is in. Observed as descriptive text rather than a
       bare identifier for top-level frames, e.g. " in <module>", " not in a function"."""

    line: int
    """1-based line number within `container`."""

    location: str
    """Unclear precise semantics; observed as "ANSWER" (i.e. the codingamer's own code) in every
       frame so far--presumably distinguishes the codingamer's code from puzzle-provided
       harness/boilerplate code in a full stack trace."""

    extra_data: CatchAll = field(default_factory=dict)


@dataclass
class CgPlayError(JSONWizardX):
    """A runtime/compile-time error from running the code, as returned in
       `CgPlayResult.error`--present instead of a normal `CgPlayResult.comparison` result when
       the code failed to compile/parse or raised an uncaught exception."""

    message: str
    """Short description of the error, e.g. "ValueError: boom", "SyntaxError: invalid syntax"."""

    stacktrace: list[CgPlayStackFrame]
    """Stack frames for the error."""

    extra_data: CatchAll = field(default_factory=dict)


@dataclass
class CgPlayComparison(JSONWizardX):
    """The result of comparing the code's output against the test case's expected output, as
       returned in `CgPlayResult.comparison`."""

    success: bool
    """Whether the code's output matched the expected output."""

    extra_data: CatchAll = field(default_factory=dict)

    expected: str | None = None
    """The test case's expected output. Not always present--absent when the code raised an
       error before producing any comparable output (see `CgPlayResult.error`)."""

    found: str | None = None
    """The code's actual output (possibly truncated, e.g. observed ending in "..."). Not always
       present--absent both on success and when the code errored out; only observed populated
       for a genuine wrong-answer mismatch."""


@dataclass
class CgPlayResult(JSONWizardX):
    """The complete response to TestSession/play."""

    output: str
    """Combined stdout+stderr produced by running the code, interleaved into a single stream
       exactly as shown in the IDE's console output pane--confirmed empirically (code that
       wrote to stderr in a loop, then a single stdout line, produced an `output` containing
       all the stderr lines followed by the stdout line). Empty if the code errored out before
       producing any output.

       `comparison`, by contrast, is computed from genuine stdout only--also confirmed
       empirically (the same test's `comparison.expected` correctly reflected comparing just
       the one real stdout line against the puzzle's expected output, unaffected by the
       interleaved stderr noise in `output`)."""

    comparison: CgPlayComparison
    """Comparison of the code's actual stdout (not `output`, which also includes stderr)
       against the test case's expected output."""

    extra_data: CatchAll = field(default_factory=dict)

    error: CgPlayError | None = None
    """Present if the code failed to compile/parse or raised an uncaught exception. `comparison`
       is still present alongside it in that case (with `success: False`, and `expected` set but
       `found` absent)."""


__all__ = [
    "CgAvailableLanguage", "CgHtml", "CgLastActivityContributor",
    "CgMultipleLanguagesTestParams", "CgPlayComparison", "CgPlayError", "CgPlayRequest",
    "CgPlayResult", "CgPlayStackFrame", "CgSolutionLanguage", "CgStubGenerator",
    "CgSubmitRequest", "CgTestSession", "CgTestSessionAnswer", "CgTestSessionContribution",
    "CgTestSessionPuzzle", "CgTestSessionQuestion", "CgTestSessionQuestionDetails",
    "CgTestSessionQuestionSummary", "CgTestSessionTestCase",
]
