"""Unit tests for codingame_tools.puzzle_manager.manager.CgPuzzleManager (`import_`/`repair`/
   `diff`/`discard_local`/`submit`/`play`), against a fake, duck-typed client (services.puzzle,
   services.test_session)--no real CgClient/network involved.

These are pure/local tests--no network--so they run under the default `pdm run test` invocation.
"""

from __future__ import annotations

import shutil
from datetime import timezone
from pathlib import Path
from typing import Any

import pytest

from codingame_tools.client.common.protocol.last_activities import CgLastActivityPuzzle, CgPuzzleFeedback
from codingame_tools.client.common.protocol.report import CgReportPuzzleProgress, CgSubmissionReport, CgValidatorResult
from codingame_tools.client.common.protocol.search import CgSearchResult
from codingame_tools.client.common.protocol.test_session import (
    CgLastActivityContributor,
    CgPlayComparison,
    CgPlayRequest,
    CgPlayResult,
    CgSubmitRequest,
    CgTestSession,
    CgTestSessionAnswer,
    CgTestSessionContribution,
    CgTestSessionPuzzle,
    CgTestSessionQuestion,
    CgTestSessionQuestionDetails,
    CgTestSessionTestCase,
)
from codingame_tools.client.common.raw_client import CgClientHttpError, CgDownloadFileResult
from codingame_tools.common.dataclass_wizard_x import CgEpochMillis
from codingame_tools.puzzle_manager.manager import (
    CgPuzzleLocalTestFailedError,
    CgPuzzleManager,
    CgPuzzleManagerError,
)
from codingame_tools.puzzle_manager.schema import CgPuzzleServerData
from codingame_tools.puzzle_manager.test_cases_dir import (
    TEST_META_FILE_NAME,
    CgPuzzleTestCaseMeta,
    normalize_test_label,
)


def _make_test_session(
            *,
            answer: CgTestSessionAnswer | None = None,
            contribution_type: str | None = "PUZZLE_INOUT",
            title: str = "Literary Alfabet Soupe",
            pretty_id: str = "literary-alfabet-soupe",
            puzzle_id: int = 10075,
            puzzle_handle: str = "puzzle-handle-1",
            test_session_handle: str = "session-handle-1",
            statement: str = "<p>statement</p>",
            stub_generator: str = "read a:int",
        ) -> CgTestSession:
    contributor = CgLastActivityContributor(user_id=1, pseudo="someone", public_handle="contributor-handle")
    # contribution_type=None models a puzzle CodinGame provides itself, which omits both
    # `contributor` and `contribution` entirely--see CgTestSessionQuestionDetails.
    contribution = None if contribution_type is None else CgTestSessionContribution(
            id=1, public_handle="contribution-handle", status="ACCEPTED", moderators=[],
            contribution_type=contribution_type,
        )
    question = CgTestSessionQuestionDetails(
            id=1094622, title=title, statement=statement, stub_generator=stub_generator,
            duration=1000, index=0, initial_id=1094622, user_id=1, available_languages=[],
            contributor=None if contribution_type is None else contributor,
            contribution=contribution,
            test_cases=[
                    CgTestSessionTestCase(index=1, input_binary_id=1, output_binary_id=2, label="Test 1"),
                    CgTestSessionTestCase(index=2, input_binary_id=3, output_binary_id=4, label="Test 2"),
                ],
            question_type="MULTIPLE_LANGUAGES",
        )
    current_question = CgTestSessionQuestion(last_submission_id=1, question=question, answer=answer)
    puzzle = CgTestSessionPuzzle(
            id=puzzle_id, handle=puzzle_handle, pretty_id=pretty_id, title=title, level="medium",
            details_page_url="/training/medium/literary-alfabet-soupe",
            forum_post_id="community-puzzle-literary-alfabet-soupe-puzzle-discussion/1",
        )
    return CgTestSession(
            test_session_handle=test_session_handle, test_session_id=1, user_id=1, test_type="PUZZLE",
            direct=False, need_account=True, shareable=True, show_replay_prompt=False,
            current_question=current_question, puzzle=puzzle, questions=[],
        )


def _make_progress(
            *,
            puzzle_id: int = 10075,
            pretty_id: str = "literary-alfabet-soupe",
            title: str = "Literary Alfabet Soupe",
            test_session_handle: str | None = "session-handle-1",
        ) -> CgLastActivityPuzzle:
    contributor = CgLastActivityContributor(user_id=1, pseudo="someone", public_handle="contributor-handle")
    progress = CgLastActivityPuzzle(
            id=puzzle_id, title=title, pretty_id=pretty_id, level="medium",
            details_page_url="/training/medium/literary-alfabet-soupe",
            forum_link="/community-puzzle-literary-alfabet-soupe-puzzle-discussion/1",
            contributor=contributor, feedback=CgPuzzleFeedback(feedback_id=1, feedbacks=[0, 0, 0, 0, 1]),
            topics=[], community_creation=True, cover_binary_id=1, achievement_count=0,
            done_achievement_count=0, attempt_count=1, solved_count=1, rank=0, validator_score=100,
            xp_points=10, puzzle_type="CODE", _creation_time=CgEpochMillis.fromtimestamp(0, tz=timezone.utc),
            test_session_handle=test_session_handle,
        )
    return progress


def _make_submission_report(
            *, submission_id: int = 424242, score: float = 100.0,
        ) -> CgSubmissionReport:
    return CgSubmissionReport(
            codingamer_id=1, submission_id=submission_id, score=score, best_score=score,
            achievements_completed=True, shared=False, validator_shareable=True,
            puzzle_progress=CgReportPuzzleProgress(
                    id=10075, achievement_count=1, done_achievement_count=1, validator_score=0,
                ),
            validators=[CgValidatorResult(method_name="Validator_1", name="Test 1", difficulty=100, success=True)],
            achievements=[], _completed_time=CgEpochMillis.fromtimestamp(0, tz=timezone.utc),
        )


class _FakePuzzleService:
    def __init__(self, handle: str = "session-handle-1") -> None:
        self.handle = handle
        self.generate_calls: list[dict[str, Any]] = []
        self.progress_results: list[CgLastActivityPuzzle] = []
        self.find_progress_calls: list[list[int]] = []
        # find_progress_by_pretty_id defaults to always succeeding (echoing back a match for
        # whatever pretty_id was queried)--matching the old, pre-_resolve_puzzle_ref() behavior
        # of every existing test here, which all pass an already-valid pretty id straight
        # through. Set pretty_id_not_found=True to simulate an unrecognized pretty id (the live-
        # confirmed 200-with-null-body signature), or pretty_id_result to a specific object.
        self.pretty_id_result: CgLastActivityPuzzle | None = None
        self.pretty_id_not_found: bool = False
        self.find_pretty_id_calls: list[str] = []

    async def generate_session_from_puzzle_pretty_id(
                self, puzzle_pretty_id: str, codingamer_id: int | None = None,
            ) -> str:
        self.generate_calls.append({"puzzle_pretty_id": puzzle_pretty_id, "codingamer_id": codingamer_id})
        return self.handle

    async def find_progress_by_ids(
                self, puzzle_ids: list[int], codingamer_id: int | None = None, arg3: int = 2,
            ) -> list[CgLastActivityPuzzle]:
        self.find_progress_calls.append(puzzle_ids)
        return self.progress_results

    async def find_progress_by_pretty_id(
                self, pretty_id: str, codingamer_id: int | None = None,
            ) -> CgLastActivityPuzzle:
        self.find_pretty_id_calls.append(pretty_id)
        if self.pretty_id_not_found:
            raise CgClientHttpError(status_code=200)
        if self.pretty_id_result is not None:
            return self.pretty_id_result
        return _make_progress(pretty_id=pretty_id)


class _FakeSearchService:
    def __init__(self) -> None:
        self.results: list[CgSearchResult] = []
        self.calls: list[dict[str, Any]] = []

    async def search(
                self, query: str, locale: str = "en", type_filter: str | None = None,
            ) -> list[CgSearchResult]:
        self.calls.append({"query": query, "locale": locale, "type_filter": type_filter})
        return self.results


class _FakeReportService:
    def __init__(self, report: CgSubmissionReport | None = None) -> None:
        self.report = report or _make_submission_report()
        self.find_calls: list[int] = []
        self.helper = self

    async def find_report_by_submission(self, submission_id: int) -> CgSubmissionReport:
        self.find_calls.append(submission_id)
        return self.report

    async def find_report_by_submission_when_ready(
                self, submission_id: int, *, max_wait_seconds: float = 60.0,
            ) -> CgSubmissionReport:
        return await self.find_report_by_submission(submission_id)


class _FakeTestSessionService:
    def __init__(
                self, session: CgTestSession, *, play_result: CgPlayResult | None = None,
                submit_result: int = 424242,
                previous_code: dict[str, str] | None = None,
            ) -> None:
        self.session = session
        self.play_result = play_result
        self.submit_result = submit_result
        # CodinGame stores the codingamer's most recent source per language; a language absent
        # here models one they've never attempted, which the real API answers with null.
        self.previous_code: dict[str, str] = dict(previous_code or {})
        self.start_calls: list[str] = []
        self.play_calls: list[dict[str, Any]] = []
        self.submit_calls: list[dict[str, Any]] = []
        self.previous_code_calls: list[tuple[str, str]] = []

    async def start_test_session(self, test_session_handle: str) -> CgTestSession:
        self.start_calls.append(test_session_handle)
        return self.session

    async def play(self, test_session_handle: str, request: CgPlayRequest) -> CgPlayResult:
        self.play_calls.append({"test_session_handle": test_session_handle, "request": request})
        assert self.play_result is not None
        return self.play_result

    async def submit(
                self, test_session_handle: str, request: CgSubmitRequest, arg3: Any = None,
            ) -> int:
        self.submit_calls.append({"test_session_handle": test_session_handle, "request": request})
        return self.submit_result

    async def get_previous_code_by_language_id(
                self, test_session_handle: str, programming_language_id: str,
            ) -> str | None:
        self.previous_code_calls.append((test_session_handle, programming_language_id))
        return self.previous_code.get(programming_language_id)


class _FakeServices:
    def __init__(
                self, puzzle: _FakePuzzleService, test_session: _FakeTestSessionService,
                search: _FakeSearchService, report: _FakeReportService,
            ) -> None:
        self.puzzle = puzzle
        self.test_session = test_session
        self.search = search
        self.report = report


class _FakeFileServletServlet:
    def __init__(self) -> None:
        self.download_calls: list[int] = []

    async def __call__(self, id: int) -> CgDownloadFileResult:  # noqa: A002
        self.download_calls.append(id)
        content = f"content-for-binary-{id}\n".encode()
        return CgDownloadFileResult.create(id=id, content=content, content_type="text/plain")


class _FakeServlets:
    def __init__(self, file_servlet: _FakeFileServletServlet) -> None:
        self.file_servlet = file_servlet


class _FakeClient:
    def __init__(
                self, puzzle: _FakePuzzleService, test_session: _FakeTestSessionService,
                file_servlet: _FakeFileServletServlet, search: _FakeSearchService,
                report: _FakeReportService,
            ) -> None:
        self.services = _FakeServices(puzzle, test_session, search, report)
        self.servlets = _FakeServlets(file_servlet)


def _make_fake_client(
            session: CgTestSession, *, play_result: CgPlayResult | None = None,
            previous_code: dict[str, str] | None = None,
        ) -> tuple[_FakeClient, _FakePuzzleService, _FakeTestSessionService, _FakeFileServletServlet]:
    puzzle_service = _FakePuzzleService(session.test_session_handle)
    test_session_service = _FakeTestSessionService(
            session, play_result=play_result, previous_code=previous_code)
    file_servlet = _FakeFileServletServlet()
    search_service = _FakeSearchService()
    report_service = _FakeReportService()
    client = _FakeClient(puzzle_service, test_session_service, file_servlet, search_service, report_service)
    return client, puzzle_service, test_session_service, file_servlet


# --- import_ -----------------------------------------------------------------------------


async def test_import_with_existing_answer_uses_it(tmp_path: Path) -> None:
    answer = CgTestSessionAnswer(code="print('existing answer')\n", programming_language_id="Java")
    session = _make_test_session(answer=answer)
    client, puzzle_service, test_session_service, file_servlet = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]

    puzzle_data = await manager.import_("literary-alfabet-soupe")

    assert puzzle_service.generate_calls == [{"puzzle_pretty_id": "literary-alfabet-soupe", "codingamer_id": None}]
    assert test_session_service.start_calls == ["session-handle-1"]

    assert manager.load_solution() == "print('existing answer')\n"
    assert manager.load_statement_html() == "<p>statement</p>"
    assert (tmp_path / ".meta" / "stub_generator.cgstub").read_text() == "read a:int\n"
    assert (tmp_path / ".gitignore").read_text() == ".meta/\n"

    identity = manager.load_identity()
    assert identity is not None
    assert identity.puzzle_id == 10075
    assert identity.puzzle_handle == "puzzle-handle-1"

    server_data = manager.load_server_data()
    assert server_data is not None
    assert server_data.puzzle_pretty_id == "literary-alfabet-soupe"
    assert server_data.test_session_handle == "session-handle-1"
    assert server_data.title == "Literary Alfabet Soupe"
    assert server_data.puzzle_type == "PUZZLE_INOUT"
    assert server_data.difficulty == "medium"

    assert puzzle_data.solution_language == "Java"  # from the existing answer, not the --language default
    assert manager.load_puzzle_data() == puzzle_data

    # test_cases=[index=1, input=1, output=2, label="Test 1"], [index=2, input=3, output=4, label="Test 2"]
    assert sorted(file_servlet.download_calls) == [1, 2, 3, 4]
    test1_dir = tmp_path / ".meta" / "tests" / "01" / "Test-1"
    assert (test1_dir / "input.txt").read_bytes() == b"content-for-binary-1\n"
    assert (test1_dir / "output.txt").read_bytes() == b"content-for-binary-2\n"
    assert (test1_dir / "test.json").read_text()
    test2_dir = tmp_path / ".meta" / "tests" / "02" / "Test-2"
    assert (test2_dir / "input.txt").read_bytes() == b"content-for-binary-3\n"
    assert (test2_dir / "output.txt").read_bytes() == b"content-for-binary-4\n"


async def test_import_without_existing_answer_uses_placeholder_and_language_flag(tmp_path: Path) -> None:
    session = _make_test_session(answer=None)
    client, _, _, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]

    puzzle_data = await manager.import_("literary-alfabet-soupe", language="Python3")

    assert puzzle_data.solution_language == "Python3"
    content = manager.load_solution()
    assert "TODO" in content
    assert "Literary Alfabet Soupe" in content


async def test_import_without_existing_answer_and_unknown_comment_syntax_uses_empty_placeholder(
        tmp_path: Path) -> None:
    """Regression test: the placeholder previously emitted an unconditional `# TODO: ...` line
       regardless of language, which is invalid syntax for any language whose comment prefix
       isn't `#`. A language with no known comment syntax (e.g. "Rust", which only has legacy
       extension data, not a real subpackage) must get an empty file instead of a wrong guess."""
    session = _make_test_session(answer=None)
    client, _, _, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]

    puzzle_data = await manager.import_("literary-alfabet-soupe", language="Rust")

    assert puzzle_data.solution_language == "Rust"
    content = manager.load_solution()
    assert content == ""


async def test_import_treats_empty_placeholder_answer_object_as_no_real_answer(tmp_path: Path) -> None:
    """Regression test: confirmed live (2026-07-31) that `answer` can be present as an empty
       JSON object (`code`/`programming_language_id` both `None`) with no solution ever
       submitted--NOT the `answer=None` shape every other test here uses. Must be treated the
       same as `answer=None`, not crash or be mistaken for a real saved answer."""
    answer = CgTestSessionAnswer(code=None, programming_language_id=None)
    session = _make_test_session(answer=answer)
    client, _, _, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]

    puzzle_data = await manager.import_("literary-alfabet-soupe", language="Python3")

    assert puzzle_data.solution_language == "Python3"
    content = manager.load_solution()
    assert "TODO" in content


async def test_import_refuses_unsupported_contribution_type(tmp_path: Path) -> None:
    session = _make_test_session(contribution_type="PUZZLE_OPTI")
    client, _, _, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]

    with pytest.raises(CgPuzzleManagerError):
        await manager.import_("literary-alfabet-soupe")

    assert manager.load_identity() is None
    assert not (tmp_path / "data" / "solution.py").exists()


async def test_import_refuses_if_already_imported(tmp_path: Path) -> None:
    session = _make_test_session()
    client, _, _, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")

    with pytest.raises(CgPuzzleManagerError):
        await manager.import_("literary-alfabet-soupe")


# --- import_ puzzle reference resolution (_resolve_puzzle_ref) ------------------------------


async def test_import_resolves_numeric_puzzle_id(tmp_path: Path) -> None:
    session = _make_test_session()
    client, puzzle_service, _, _ = _make_fake_client(session)
    puzzle_service.progress_results = [_make_progress(puzzle_id=10075, pretty_id="literary-alfabet-soupe")]
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]

    await manager.import_("10075")

    assert puzzle_service.find_progress_calls == [[10075]]
    assert puzzle_service.find_pretty_id_calls == []  # numeric ID resolved first--no need to try the rest
    assert puzzle_service.generate_calls == [{"puzzle_pretty_id": "literary-alfabet-soupe", "codingamer_id": None}]


async def test_import_numeric_puzzle_id_with_no_match_raises_immediately(tmp_path: Path) -> None:
    session = _make_test_session()
    client, puzzle_service, _, _ = _make_fake_client(session)
    search_service = client.services.search
    puzzle_service.progress_results = []  # no puzzle with this ID
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]

    with pytest.raises(CgPuzzleManagerError):
        await manager.import_("99999999")

    # doesn't fall through to title search--a bare number is meant as an ID, not a title
    assert search_service.calls == []


async def test_import_resolves_already_valid_pretty_id(tmp_path: Path) -> None:
    session = _make_test_session()
    client, puzzle_service, _, _ = _make_fake_client(session)
    search_service = client.services.search
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]

    await manager.import_("literary-alfabet-soupe")

    assert puzzle_service.find_pretty_id_calls == ["literary-alfabet-soupe"]
    assert search_service.calls == []  # resolved directly--no need for a title search


async def test_import_falls_back_to_exact_title_match_when_not_a_valid_pretty_id(tmp_path: Path) -> None:
    session = _make_test_session()
    client, puzzle_service, _, _ = _make_fake_client(session)
    search_service = client.services.search
    puzzle_service.pretty_id_not_found = True
    search_service.results = [
            CgSearchResult(id="literary-alfabet-soupe", name="Literary Alfabet Soupe", type="PUZZLE"),
            CgSearchResult(id="some-other-puzzle", name="Something Else Entirely", type="PUZZLE"),
        ]
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]

    await manager.import_("Literary Alfabet Soupe")

    assert search_service.calls == [{"query": "Literary Alfabet Soupe", "locale": "en", "type_filter": "PUZZLE"}]
    assert puzzle_service.generate_calls == [{"puzzle_pretty_id": "literary-alfabet-soupe", "codingamer_id": None}]


async def test_import_falls_back_to_case_insensitive_title_match(tmp_path: Path) -> None:
    session = _make_test_session()
    client, puzzle_service, _, _ = _make_fake_client(session)
    search_service = client.services.search
    puzzle_service.pretty_id_not_found = True
    search_service.results = [
            CgSearchResult(id="literary-alfabet-soupe", name="Literary Alfabet Soupe", type="PUZZLE"),
        ]
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]

    # doesn't exactly match "Literary Alfabet Soupe"--only case-insensitively
    await manager.import_("literary alfabet soupe")

    assert puzzle_service.generate_calls == [{"puzzle_pretty_id": "literary-alfabet-soupe", "codingamer_id": None}]


async def test_import_prefers_exact_title_match_over_case_insensitive(tmp_path: Path) -> None:
    session = _make_test_session()
    client, puzzle_service, _, _ = _make_fake_client(session)
    search_service = client.services.search
    puzzle_service.pretty_id_not_found = True
    search_service.results = [
            CgSearchResult(id="wrong-case-match", name="literary alfabet soupe", type="PUZZLE"),
            CgSearchResult(id="literary-alfabet-soupe", name="Literary Alfabet Soupe", type="PUZZLE"),
        ]
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]

    await manager.import_("Literary Alfabet Soupe")

    assert puzzle_service.generate_calls == [{"puzzle_pretty_id": "literary-alfabet-soupe", "codingamer_id": None}]


async def test_import_raises_when_nothing_resolves(tmp_path: Path) -> None:
    session = _make_test_session()
    client, puzzle_service, _, _ = _make_fake_client(session)
    search_service = client.services.search
    puzzle_service.pretty_id_not_found = True
    search_service.results = []
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]

    with pytest.raises(CgPuzzleManagerError):
        await manager.import_("Some Puzzle That Does Not Exist")


# --- repair ----------------------------------------------------------------------------------


async def test_repair_requires_prior_import(tmp_path: Path) -> None:
    manager = CgPuzzleManager(tmp_path, object())  # type: ignore[arg-type]
    with pytest.raises(FileNotFoundError):
        await manager.repair()


async def test_repair_refuses_if_meta_already_present(tmp_path: Path) -> None:
    session = _make_test_session()
    client, _, _, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")

    with pytest.raises(CgPuzzleManagerError):
        await manager.repair()


async def test_repair_reconstructs_meta_reusing_cached_test_session_handle(tmp_path: Path) -> None:
    session = _make_test_session()
    client, puzzle_service, test_session_service, file_servlet = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")

    # Simulate a fresh clone: .meta/ (gitignored) is gone, data/ survives.
    shutil.rmtree(tmp_path / ".meta")
    (tmp_path / ".gitignore").unlink()
    file_servlet.download_calls.clear()

    puzzle_service.progress_results = [_make_progress(test_session_handle="session-handle-1")]
    puzzle_service.generate_calls.clear()

    server_data = await manager.repair()

    assert puzzle_service.find_progress_calls == [[10075]]
    assert puzzle_service.generate_calls == []  # reused the cached-affinity handle, no re-generation
    assert server_data.test_session_handle == "session-handle-1"
    assert server_data.puzzle_type == "PUZZLE_INOUT"
    assert server_data.difficulty == "medium"
    assert (tmp_path / ".meta" / "statement.html").is_file()
    assert (tmp_path / ".gitignore").read_text() == ".meta/\n"
    assert manager.load_server_data() == server_data
    assert sorted(file_servlet.download_calls) == [1, 2, 3, 4]  # tests/ re-downloaded too
    assert (tmp_path / ".meta" / "tests" / "01" / "Test-1" / "input.txt").read_bytes() == b"content-for-binary-1\n"


async def test_repair_falls_back_to_generate_when_no_cached_test_session_handle(tmp_path: Path) -> None:
    session = _make_test_session()
    client, puzzle_service, test_session_service, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")

    shutil.rmtree(tmp_path / ".meta")

    puzzle_service.progress_results = [_make_progress(test_session_handle=None)]
    puzzle_service.generate_calls.clear()

    server_data = await manager.repair()

    assert len(puzzle_service.generate_calls) == 1
    assert server_data.test_session_handle == "session-handle-1"


async def test_repair_refuses_on_puzzle_id_mismatch(tmp_path: Path) -> None:
    session = _make_test_session()
    client, puzzle_service, _, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")

    shutil.rmtree(tmp_path / ".meta")

    puzzle_service.progress_results = [_make_progress(puzzle_id=99999)]

    with pytest.raises(CgPuzzleManagerError):
        await manager.repair()

    assert manager.load_server_data() is None


async def test_repair_refuses_if_no_local_solution(tmp_path: Path) -> None:
    session = _make_test_session()
    client, _, _, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")

    shutil.rmtree(tmp_path / ".meta")
    (tmp_path / "data" / "solution.py").unlink()

    with pytest.raises(FileNotFoundError):
        await manager.repair()


# --- submit --------------------------------------------------------------------------------


async def test_submit_submits_current_local_content(tmp_path: Path) -> None:
    answer = CgTestSessionAnswer(code="print('old')\n", programming_language_id="Python3")
    session = _make_test_session(answer=answer)
    client, _, test_session_service, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")
    (tmp_path / "data" / "solution.py").write_text("print('new solution')\n")

    report = await manager.submit()

    assert report is client.services.report.report
    assert client.services.report.find_calls == [424242]
    assert len(test_session_service.submit_calls) == 1
    call = test_session_service.submit_calls[0]
    assert call["test_session_handle"] == "session-handle-1"
    # The file's terminator is this client's, not part of the value--see common.text_files.
    assert call["request"].code == "print('new solution')"
    assert call["request"].programming_language_id == "Python3"


async def test_submit_requires_prior_import(tmp_path: Path) -> None:
    manager = CgPuzzleManager(tmp_path, object())  # type: ignore[arg-type]
    with pytest.raises(FileNotFoundError):
        await manager.submit()


async def test_submit_requires_meta_present(tmp_path: Path) -> None:
    session = _make_test_session()
    client, _, _, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")

    shutil.rmtree(tmp_path / ".meta")

    with pytest.raises(CgPuzzleManagerError):
        await manager.submit()


# --- play ------------------------------------------------------------------------------------


async def test_play_with_no_args_runs_every_downloaded_test_case(tmp_path: Path) -> None:
    session = _make_test_session()  # 2 downloaded test cases: index 1 "Test 1", index 2 "Test 2"
    play_result = CgPlayResult(output="1\n", comparison=CgPlayComparison(success=True))
    client, _, test_session_service, _ = _make_fake_client(session, play_result=play_result)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")

    items = await manager.play()

    assert [item.index for item in items] == [1, 2]
    assert [item.label for item in items] == ["Test 1", "Test 2"]
    assert all(item.result.comparison.success for item in items)
    assert len(test_session_service.play_calls) == 2
    indices = [c["request"].multiple_languages.test_index for c in test_session_service.play_calls]
    assert indices == [1, 2]


async def test_play_with_explicit_index(tmp_path: Path) -> None:
    session = _make_test_session()
    play_result = CgPlayResult(output="", comparison=CgPlayComparison(success=False, expected="x", found="y"))
    client, _, test_session_service, _ = _make_fake_client(session, play_result=play_result)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")

    items = await manager.play([2])

    assert len(items) == 1
    assert items[0].index == 2
    assert items[0].label == "Test 2"
    assert items[0].result is play_result
    request = test_session_service.play_calls[0]["request"]
    assert request.multiple_languages is not None
    assert request.multiple_languages.test_index == 2


async def test_play_with_multiple_explicit_indices_runs_each_in_order(tmp_path: Path) -> None:
    session = _make_test_session()
    play_result = CgPlayResult(output="1\n", comparison=CgPlayComparison(success=True))
    client, _, test_session_service, _ = _make_fake_client(session, play_result=play_result)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")

    # index 5 isn't downloaded locally--play() doesn't require that, only a real server index.
    items = await manager.play([2, 5, 1])

    assert [item.index for item in items] == [2, 5, 1]
    assert [item.label for item in items] == ["Test 2", "test 5", "Test 1"]
    indices = [c["request"].multiple_languages.test_index for c in test_session_service.play_calls]
    assert indices == [2, 5, 1]


async def test_play_with_no_downloaded_tests_and_no_args_raises(tmp_path: Path) -> None:
    session = _make_test_session()
    client, _, _, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")
    shutil.rmtree(manager.tests_dir)

    with pytest.raises(FileNotFoundError):
        await manager.play()


# --- resolve_play_indices / play_one (the pieces play() is built from, exposed for a caller ----
# --- that wants to stream results one at a time--see `cg puzzle play-server`'s CLI handler) -----


async def test_resolve_play_indices_returns_given_indices_unchanged(tmp_path: Path) -> None:
    session = _make_test_session()
    client, _, _, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")

    assert manager.resolve_play_indices([5, 2, 5]) == [5, 2, 5]


async def test_resolve_play_indices_defaults_to_every_downloaded_test_case(tmp_path: Path) -> None:
    session = _make_test_session()  # 2 downloaded test cases: index 1 "Test 1", index 2 "Test 2"
    client, _, _, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")

    assert manager.resolve_play_indices() == [1, 2]


async def test_resolve_play_indices_requires_prior_import(tmp_path: Path) -> None:
    manager = CgPuzzleManager(tmp_path, object())  # type: ignore[arg-type]
    with pytest.raises(FileNotFoundError):
        manager.resolve_play_indices()


async def test_resolve_play_indices_with_no_downloaded_tests_and_no_args_raises(tmp_path: Path) -> None:
    session = _make_test_session()
    client, _, _, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")
    shutil.rmtree(manager.tests_dir)

    with pytest.raises(FileNotFoundError):
        manager.resolve_play_indices()


async def test_play_one_runs_a_single_index(tmp_path: Path) -> None:
    session = _make_test_session()
    play_result = CgPlayResult(output="1\n", comparison=CgPlayComparison(success=True))
    client, _, test_session_service, _ = _make_fake_client(session, play_result=play_result)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")

    item = await manager.play_one(2)

    assert item.index == 2
    assert item.label == "Test 2"
    assert item.result is play_result
    assert len(test_session_service.play_calls) == 1


async def test_play_is_equivalent_to_looping_resolve_play_indices_and_play_one(tmp_path: Path) -> None:
    """play() is documented as a thin convenience wrapper--confirm it actually behaves like one."""
    session = _make_test_session()
    play_result = CgPlayResult(output="1\n", comparison=CgPlayComparison(success=True))
    client, _, test_session_service, _ = _make_fake_client(session, play_result=play_result)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")

    via_play = await manager.play([2, 1])
    test_session_service.play_calls.clear()
    via_manual_loop = [await manager.play_one(index) for index in manager.resolve_play_indices([2, 1])]

    assert [i.index for i in via_play] == [i.index for i in via_manual_loop]
    assert [i.label for i in via_play] == [i.label for i in via_manual_loop]


# --- diff ------------------------------------------------------------------------------------


async def test_diff_empty_when_matching(tmp_path: Path) -> None:
    answer = CgTestSessionAnswer(code="print('same')\n", programming_language_id="Python3")
    session = _make_test_session(answer=answer)
    # diff() compares against the server's saved code *for the local language*, not against
    # whatever language the session happens to be in--so the fake has to hold it per language.
    client, _, _, _ = _make_fake_client(session, previous_code={"Python3": "print('same')\n"})
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")

    assert await manager.diff() == ""


async def test_diff_shows_local_vs_server_differences(tmp_path: Path) -> None:
    answer = CgTestSessionAnswer(code="print('server version')\n", programming_language_id="Python3")
    session = _make_test_session(answer=answer)
    client, _, _, _ = _make_fake_client(
            session, previous_code={"Python3": "print('server version')\n"})
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")
    (tmp_path / "data" / "solution.py").write_text("print('local version')\n")

    diff_text = await manager.diff()

    assert "server version" in diff_text
    assert "local version" in diff_text


# --- discard_local ---------------------------------------------------------------------------


async def test_discard_local_overwrites_with_server_answer(tmp_path: Path) -> None:
    answer = CgTestSessionAnswer(code="print('server version')\n", programming_language_id="Python3")
    session = _make_test_session(answer=answer)
    client, _, _, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")
    (tmp_path / "data" / "solution.py").write_text("print('local edit')\n")

    result = await manager.discard_local()

    assert result.code == "print('server version')\n"
    assert manager.load_solution() == "print('server version')\n"


async def test_discard_local_updates_recorded_language_if_it_changed(tmp_path: Path) -> None:
    original_answer = CgTestSessionAnswer(code="print('py')\n", programming_language_id="Python3")
    session = _make_test_session(answer=original_answer)
    client, _, test_session_service, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")
    puzzle_data = manager.load_puzzle_data()
    assert puzzle_data is not None
    assert puzzle_data.solution_language == "Python3"

    new_answer = CgTestSessionAnswer(code="System.out.println(1);\n", programming_language_id="Java")
    test_session_service.session = _make_test_session(answer=new_answer)

    result = await manager.discard_local()

    assert result.solution_language == "Java"
    puzzle_data = manager.load_puzzle_data()
    assert puzzle_data is not None
    assert puzzle_data.solution_language == "Java"


async def test_discard_local_refuses_without_server_answer(tmp_path: Path) -> None:
    session = _make_test_session(answer=None)
    client, _, _, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")

    with pytest.raises(CgPuzzleManagerError):
        await manager.discard_local()


# --- load_solution -----------------------------------------------------------------------------


async def test_load_solution_requires_prior_import(tmp_path: Path) -> None:
    manager = CgPuzzleManager(tmp_path, object())  # type: ignore[arg-type]
    with pytest.raises(FileNotFoundError):
        manager.load_solution()


async def test_load_solution_returns_current_content(tmp_path: Path) -> None:
    session = _make_test_session()
    client, _, _, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")
    (tmp_path / "data" / "solution.py").write_text("print('hi')\n")

    # The file's terminator is this client's, not part of the value--see common.text_files.
    assert manager.load_solution() == "print('hi')"


# --- play_local ------------------------------------------------------------------------------


def _write_downloaded_test_case(tests_dir: Path, index: int, label: str, input_text: str, output_text: str) -> None:
    named_dir = tests_dir / str(index).zfill(2) / normalize_test_label(label)
    named_dir.mkdir(parents=True, exist_ok=True)
    CgPuzzleTestCaseMeta(label=label).save(named_dir / TEST_META_FILE_NAME)
    (named_dir / "input.txt").write_text(input_text)
    (named_dir / "output.txt").write_text(output_text)


async def _import_with_doubling_solution(tmp_path: Path) -> CgPuzzleManager:
    """A manager whose `data/solution.src` doubles an integer read from stdin, with a fresh
       `.meta/tests/` (real files, not the fake client's placeholder content--`play_local` never
       touches the network, so there's no need to route this through the fake client)."""
    session = _make_test_session()
    client, _, _, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")
    (tmp_path / "data" / "solution.py").write_text("n = int(input())\nprint(n * 2)\n")
    shutil.rmtree(manager.tests_dir)
    _write_downloaded_test_case(manager.tests_dir, 1, "Doubles", "21\n", "42\n")
    _write_downloaded_test_case(manager.tests_dir, 2, "Doubles Again", "10\n", "20\n")
    return manager


async def test_play_local_all_pass(tmp_path: Path) -> None:
    manager = await _import_with_doubling_solution(tmp_path)

    results = await manager.play_local()

    assert [r.index for r in results] == [1, 2]
    assert all(r.passed for r in results)
    assert results[0].actual_output == "42\n"


async def test_play_local_with_explicit_test_index_runs_only_that_one(tmp_path: Path) -> None:
    manager = await _import_with_doubling_solution(tmp_path)

    results = await manager.play_local([2])

    assert [r.index for r in results] == [2]
    assert results[0].passed


async def test_play_local_with_multiple_explicit_indices_runs_each_in_given_order(tmp_path: Path) -> None:
    manager = await _import_with_doubling_solution(tmp_path)

    results = await manager.play_local([2, 1])

    assert [r.index for r in results] == [2, 1]
    assert all(r.passed for r in results)


async def test_play_local_unknown_test_index_raises(tmp_path: Path) -> None:
    manager = await _import_with_doubling_solution(tmp_path)

    with pytest.raises(CgPuzzleManagerError):
        await manager.play_local([99])


async def test_play_local_raises_and_reports_mismatch(tmp_path: Path) -> None:
    manager = await _import_with_doubling_solution(tmp_path)
    (tmp_path / "data" / "solution.py").write_text("n = int(input())\nprint(n * 3)\n")  # wrong

    with pytest.raises(CgPuzzleLocalTestFailedError) as exc_info:
        await manager.play_local()

    results = exc_info.value.results
    assert [r.index for r in results] == [1, 2]
    assert all(not r.passed for r in results)
    assert results[0].actual_output == "63\n"
    assert results[0].expected_output == "42\n"


async def test_play_local_requires_prior_import(tmp_path: Path) -> None:
    manager = CgPuzzleManager(tmp_path, object())  # type: ignore[arg-type]
    with pytest.raises(FileNotFoundError):
        await manager.play_local()


async def test_play_local_requires_downloaded_tests(tmp_path: Path) -> None:
    session = _make_test_session()
    client, _, _, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")
    shutil.rmtree(manager.tests_dir)

    with pytest.raises(FileNotFoundError):
        await manager.play_local()


# --- resolve_play_local_test_cases / play_local_one (the pieces play_local() is built from, ----
# --- exposed for a caller that wants to stream results--see `cg puzzle play`'s CLI handler) -----


async def test_resolve_play_local_test_cases_returns_given_indices_in_order(tmp_path: Path) -> None:
    manager = await _import_with_doubling_solution(tmp_path)

    test_cases = manager.resolve_play_local_test_cases([2, 1])

    assert [tc.index for tc in test_cases] == [2, 1]


async def test_resolve_play_local_test_cases_defaults_to_every_downloaded_test_case(tmp_path: Path) -> None:
    manager = await _import_with_doubling_solution(tmp_path)

    test_cases = manager.resolve_play_local_test_cases()

    assert [tc.index for tc in test_cases] == [1, 2]


async def test_resolve_play_local_test_cases_unknown_index_raises(tmp_path: Path) -> None:
    manager = await _import_with_doubling_solution(tmp_path)

    with pytest.raises(CgPuzzleManagerError):
        manager.resolve_play_local_test_cases([99])


async def test_resolve_play_local_test_cases_requires_prior_import(tmp_path: Path) -> None:
    manager = CgPuzzleManager(tmp_path, object())  # type: ignore[arg-type]
    with pytest.raises(FileNotFoundError):
        manager.resolve_play_local_test_cases()


async def test_play_local_one_runs_a_single_test_case(tmp_path: Path) -> None:
    manager = await _import_with_doubling_solution(tmp_path)
    test_case = manager.resolve_play_local_test_cases([2])[0]

    result = await manager.play_local_one(test_case)

    assert result.index == 2
    assert result.passed
    assert result.actual_output == "20\n"


async def test_play_local_one_never_raises_for_a_failing_test(tmp_path: Path) -> None:
    manager = await _import_with_doubling_solution(tmp_path)
    (tmp_path / "data" / "solution.py").write_text("n = int(input())\nprint(n * 3)\n")  # wrong
    test_case = manager.resolve_play_local_test_cases([1])[0]

    result = await manager.play_local_one(test_case)

    assert not result.passed
    assert result.actual_output == "63\n"


async def test_play_local_is_equivalent_to_looping_resolve_and_play_local_one(tmp_path: Path) -> None:
    """play_local() is documented as a thin convenience wrapper--confirm it behaves like one."""
    manager = await _import_with_doubling_solution(tmp_path)

    via_play_local = await manager.play_local([2, 1])
    via_manual_loop = [
            await manager.play_local_one(tc) for tc in manager.resolve_play_local_test_cases([2, 1])
        ]

    assert [r.index for r in via_play_local] == [r.index for r in via_manual_loop]
    assert [r.actual_output for r in via_play_local] == [r.actual_output for r in via_manual_loop]


# --- status ------------------------------------------------------------------------------------


async def test_status_default_is_local_only(tmp_path: Path) -> None:
    answer = CgTestSessionAnswer(code="print('same')\n", programming_language_id="Python3")
    session = _make_test_session(answer=answer)
    client, puzzle_service, test_session_service, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")
    start_calls_after_import = len(test_session_service.start_calls)
    find_progress_calls_after_import = len(puzzle_service.find_progress_calls)

    status = await manager.status()

    assert status.puzzle_id == 10075
    assert status.puzzle_handle == "puzzle-handle-1"
    assert status.title == "Literary Alfabet Soupe"
    assert status.puzzle_pretty_id == "literary-alfabet-soupe"
    assert status.puzzle_type == "PUZZLE_INOUT"
    assert status.difficulty == "medium"
    assert status.solution_language == "Python3"
    assert status.local_dirty is None
    assert status.progress is None
    # no network calls beyond whatever import_() itself already made
    assert len(test_session_service.start_calls) == start_calls_after_import
    assert len(puzzle_service.find_progress_calls) == find_progress_calls_after_import


async def test_status_puzzle_type_and_difficulty_none_for_pre_existing_cache(tmp_path: Path) -> None:
    """A `.meta/puzzle-server-data.json` written before `puzzle_type`/`difficulty` existed should
       still load fine, with those two fields simply absent (None), not raise/crash."""
    session = _make_test_session()
    client, _, _, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")
    server_data = manager.load_server_data()
    assert server_data is not None
    CgPuzzleServerData(
            test_session_handle=server_data.test_session_handle, title=server_data.title,
            puzzle_pretty_id=server_data.puzzle_pretty_id,
        ).save(manager.server_data_file)

    status = await manager.status()

    assert status.puzzle_type is None
    assert status.difficulty is None


async def test_status_refresh_detects_matching_and_diverging_local_edits(tmp_path: Path) -> None:
    answer = CgTestSessionAnswer(code="print('same')\n", programming_language_id="Python3")
    session = _make_test_session(answer=answer)
    # local_dirty is bool(diff()), and diff() reads the server's code for the *local* language.
    client, _, _, _ = _make_fake_client(session, previous_code={"Python3": "print('same')\n"})
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")

    status = await manager.status(refresh=True)
    assert status.local_dirty is False

    (tmp_path / "data" / "solution.py").write_text("print('local edit')\n")
    status2 = await manager.status(refresh=True)
    assert status2.local_dirty is True


async def test_status_refresh_fetches_progress(tmp_path: Path) -> None:
    session = _make_test_session()
    client, puzzle_service, _, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")
    progress = _make_progress()
    puzzle_service.progress_results = [progress]

    status = await manager.status(refresh=True)

    assert status.progress == progress
    assert puzzle_service.find_progress_calls[-1] == [10075]


async def test_status_refresh_progress_none_when_no_matching_result(tmp_path: Path) -> None:
    session = _make_test_session()
    client, puzzle_service, _, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")
    puzzle_service.progress_results = []  # no match at all

    status = await manager.status(refresh=True)

    assert status.progress is None


async def test_status_requires_prior_import(tmp_path: Path) -> None:
    manager = CgPuzzleManager(tmp_path, object())  # type: ignore[arg-type]
    with pytest.raises(FileNotFoundError):
        await manager.status()


async def test_status_requires_meta_present(tmp_path: Path) -> None:
    session = _make_test_session()
    client, _, _, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")

    shutil.rmtree(tmp_path / ".meta")

    with pytest.raises(CgPuzzleManagerError):
        await manager.status()


# --- delete --------------------------------------------------------------------------------


async def test_delete_removes_the_whole_working_directory(tmp_path: Path) -> None:
    session = _make_test_session()
    client, _, _, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")
    assert tmp_path.is_dir()

    await manager.delete()

    assert not tmp_path.exists()


async def test_delete_requires_prior_import(tmp_path: Path) -> None:
    manager = CgPuzzleManager(tmp_path, object())  # type: ignore[arg-type]
    with pytest.raises(FileNotFoundError):
        await manager.delete()


# --- language context / build (invariants the Docker work depends on) --------------------------


async def test_language_context_is_infallible_on_a_bare_directory(tmp_path: Path) -> None:
    """The context is documented as infallible--constructible over a directory that was never
       imported, with no puzzle.json and no solution file."""
    manager = CgPuzzleManager(tmp_path, object())  # type: ignore[arg-type]

    ctx = manager.language_context("Python3")

    assert ctx.root == manager.puzzle_dir
    assert ctx.solution_file == manager.solution_file
    assert ctx.meta_dir == manager.meta_dir


async def test_language_context_points_at_the_one_real_solution_file(tmp_path: Path) -> None:
    """One path, carrying the language's own extension, and not a symlink. There is no second
       candidate for a build or a debugger to choose between."""
    manager = await _import_with_doubling_solution(tmp_path)

    ctx = manager.language_context("Python3")

    assert ctx.solution_file == tmp_path / "data" / "solution.py"
    assert ctx.solution_file.is_file()
    assert not ctx.solution_file.is_symlink()


async def test_language_context_ignores_the_language_it_is_handed(tmp_path: Path) -> None:
    """The solution file is whatever is on disk, not a name derived from the argument. That matters
       because the two can disagree--a directory written by an older cg, or a fetch that changed the
       language before the rename ran--and the file that exists is the one being edited."""
    manager = await _import_with_doubling_solution(tmp_path)

    assert (manager.language_context("TotallyUnknownLang").solution_file
            == manager.language_context("Python3").solution_file)


async def test_build_solution_is_a_no_op_success_for_python(tmp_path: Path) -> None:
    manager = await _import_with_doubling_solution(tmp_path)

    result = await manager.build_solution()

    assert result.ok
    assert result.up_to_date


async def test_build_solution_requires_prior_import(tmp_path: Path) -> None:
    manager = CgPuzzleManager(tmp_path, object())  # type: ignore[arg-type]

    with pytest.raises(FileNotFoundError):
        await manager.build_solution()


async def test_import_accepts_an_official_puzzle_with_no_contribution(tmp_path: Path) -> None:
    """A puzzle CodinGame provides itself has no contribution, so its contribution type is
       unknowable. Refusing on that would block importing every official puzzle on the site
       (confirmed live 2026-08-02 with "Temperatures"), so absence is treated as a standard in/out
       puzzle and recorded as an unknown type."""
    session = _make_test_session(answer=None, contribution_type=None)
    client, _, _, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]

    await manager.import_("literary-alfabet-soupe", language="C++")

    server_data = manager.load_server_data()
    assert server_data is not None
    assert server_data.puzzle_type is None


async def test_import_still_refuses_a_known_unsupported_contribution_type(tmp_path: Path) -> None:
    """Absence is tolerated; a type that's present and unsupported is still rejected."""
    session = _make_test_session(contribution_type="PUZZLE_OPTI")
    client, _, _, _ = _make_fake_client(session)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]

    with pytest.raises(CgPuzzleManagerError):
        await manager.import_("literary-alfabet-soupe")


# --- import --language / set_language ------------------------------------------------------------


async def test_import_with_a_language_restores_saved_code_for_that_language(tmp_path: Path) -> None:
    """An explicit --language means "start in this one", not "use it only if nothing is saved".
       CodinGame keeps your latest source per language, so asking for a language you'd previously
       written a solution in must bring that solution back rather than a placeholder."""
    answer = CgTestSessionAnswer(code="print('py')\n", programming_language_id="Python3")
    session = _make_test_session(answer=answer)
    client, _, ts, _ = _make_fake_client(session, previous_code={"C++": "int main(){}\n"})
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]

    puzzle_data = await manager.import_("literary-alfabet-soupe", language="C++")

    assert puzzle_data.solution_language == "C++"
    assert manager.load_solution() == "int main(){}\n"
    assert ("session-handle-1", "C++") in ts.previous_code_calls


async def test_import_with_an_unused_language_falls_back_to_a_placeholder(tmp_path: Path) -> None:
    answer = CgTestSessionAnswer(code="print('py')\n", programming_language_id="Python3")
    session = _make_test_session(answer=answer)
    client, _, _, _ = _make_fake_client(session)  # nothing saved in any other language
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]

    puzzle_data = await manager.import_("literary-alfabet-soupe", language="C++")

    assert puzzle_data.solution_language == "C++"
    assert "TODO" in manager.load_solution()


async def test_import_without_a_language_still_uses_the_saved_answer(tmp_path: Path) -> None:
    """The no---language path is unchanged: whichever language you last used."""
    answer = CgTestSessionAnswer(code="print('py')\n", programming_language_id="Python3")
    session = _make_test_session(answer=answer)
    client, _, ts, _ = _make_fake_client(session, previous_code={"C++": "int main(){}\n"})
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]

    puzzle_data = await manager.import_("literary-alfabet-soupe")

    assert puzzle_data.solution_language == "Python3"
    assert manager.load_solution() == "print('py')\n"
    assert ts.previous_code_calls == []  # no need to ask; the session already answered


async def _imported(tmp_path: Path, **kwargs: Any) -> tuple[CgPuzzleManager, _FakeTestSessionService]:
    answer = CgTestSessionAnswer(code="print('py')\n", programming_language_id="Python3")
    session = _make_test_session(answer=answer)
    client, _, ts, _ = _make_fake_client(session, **kwargs)
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe")
    return manager, ts


async def test_set_language_restores_saved_code_for_the_new_language(tmp_path: Path) -> None:
    manager, _ = await _imported(tmp_path, previous_code={
            "Python3": "print('py')\n", "C++": "int main(){ /* mine */ }\n"})

    result = await manager.set_language("C++")

    assert result.previous_language == "Python3"
    assert result.language == "C++"
    assert result.from_server
    assert manager.load_solution() == "int main(){ /* mine */ }\n"
    puzzle_data = manager.load_puzzle_data()
    assert puzzle_data is not None
    assert puzzle_data.solution_language == "C++"
    # The file itself is renamed to follow the language, and the old name is gone--there is never
    # more than one solution file.
    assert (tmp_path / "data" / "solution.cpp").is_file()
    assert not (tmp_path / "data" / "solution.py").exists()
    assert sorted(p.name for p in (tmp_path / "data").glob("solution.*")) == ["solution.cpp"]


async def test_set_language_writes_a_placeholder_for_a_never_used_language(tmp_path: Path) -> None:
    manager, _ = await _imported(tmp_path, previous_code={"Python3": "print('py')\n"})

    result = await manager.set_language("C++")

    assert not result.from_server
    assert "TODO" in manager.load_solution()


async def test_set_language_refuses_when_local_edits_are_unsaved(tmp_path: Path) -> None:
    """Switching overwrites solution.src, so work the server doesn't have would be lost."""
    manager, _ = await _imported(tmp_path, previous_code={"Python3": "print('py')\n"})
    manager.solution_file.write_text("print('my unsaved edit')\n")

    with pytest.raises(CgPuzzleManagerError, match="discard"):
        await manager.set_language("C++")

    assert manager.load_solution() == "print('my unsaved edit')"  # untouched


async def test_set_language_force_discards_unsaved_edits(tmp_path: Path) -> None:
    manager, _ = await _imported(tmp_path, previous_code={
            "Python3": "print('py')\n", "C++": "int main(){}\n"})
    manager.solution_file.write_text("print('my unsaved edit')\n")

    await manager.set_language("C++", force=True)

    assert manager.load_solution() == "int main(){}\n"


async def test_set_language_tolerates_a_trailing_newline_difference(tmp_path: Path) -> None:
    """The server's stored code and a locally-written file routinely differ by one trailing
       newline; that must not read as "you have unsaved changes" on an untouched directory."""
    manager, _ = await _imported(tmp_path, previous_code={
            "Python3": "print('py')", "C++": "int main(){}\n"})  # note: no trailing \n

    await manager.set_language("C++")  # must not raise

    assert manager.load_solution() == "int main(){}\n"


async def test_set_language_treats_our_own_placeholder_as_safe_to_discard(tmp_path: Path) -> None:
    """Importing with a language you've never used writes a placeholder that was never saved
       server-side. Without this, such a directory could never switch away without --force."""
    answer = CgTestSessionAnswer(code=None, programming_language_id=None)
    session = _make_test_session(answer=answer)
    client, _, _, _ = _make_fake_client(session, previous_code={"C++": "int main(){}\n"})
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe", language="Rust")  # placeholder, never saved

    await manager.set_language("C++")  # must not raise

    assert manager.load_solution() == "int main(){}\n"


async def test_untouched_solution_is_recognized_even_if_placeholder_generation_changes(
            tmp_path: Path,
            monkeypatch: pytest.MonkeyPatch,
        ) -> None:
    """The reason the snapshot is *recorded* rather than regenerated: placeholder generation is not
       guaranteed byte-identical forever (a template tweak, or an embedded timestamp, would be
       enough). Regenerating and comparing would make an untouched working directory suddenly claim
       it had unsaved changes and refuse to switch."""
    answer = CgTestSessionAnswer(code=None, programming_language_id=None)
    session = _make_test_session(answer=answer)
    client, _, _, _ = _make_fake_client(session, previous_code={"C++": "int main(){}\n"})
    manager = CgPuzzleManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("literary-alfabet-soupe", language="Rust")
    untouched = manager.load_solution()

    # Simulate a future release generating a different placeholder.
    monkeypatch.setattr(
            "codingame_tools.puzzle_manager.manager._placeholder_solution",
            lambda language, title, pretty_id: "# COMPLETELY DIFFERENT TEMPLATE\n")

    await manager.set_language("C++")  # must still not raise

    assert manager.load_solution() == "int main(){}\n"
    assert untouched != "# COMPLETELY DIFFERENT TEMPLATE\n"  # the templates really do differ


async def test_set_language_refuses_when_the_snapshot_is_missing_and_server_differs(
            tmp_path: Path,
        ) -> None:
    """Fail-safe: a directory with no snapshot (fresh clone, or imported by an older version) falls
       back to the server comparison, which errs toward refusing rather than discarding silently."""
    manager, _ = await _imported(tmp_path, previous_code={"C++": "int main(){}\n"})
    manager.solution_snapshot_file.unlink()

    with pytest.raises(CgPuzzleManagerError, match="discard"):
        await manager.set_language("C++")


async def test_writing_the_solution_always_records_a_snapshot(tmp_path: Path) -> None:
    """Every writer of solution.src goes through one funnel, so the snapshot can't drift."""
    manager, _ = await _imported(tmp_path, previous_code={"C++": "int main(){}\n"})

    snapshot = manager.load_solution_snapshot()
    assert snapshot is not None
    assert snapshot.solution_language == "Python3"
    assert snapshot.code == manager.load_solution()

    await manager.set_language("C++")

    snapshot = manager.load_solution_snapshot()
    assert snapshot is not None
    assert snapshot.solution_language == "C++"
    assert snapshot.code == manager.load_solution()


async def test_set_language_rejects_an_unknown_language(tmp_path: Path) -> None:
    manager, _ = await _imported(tmp_path, previous_code={"Python3": "print('py')\n"})

    with pytest.raises(CgPuzzleManagerError, match="isn't a language"):
        await manager.set_language("Cobol")


async def test_set_language_rejects_switching_to_the_current_language(tmp_path: Path) -> None:
    manager, _ = await _imported(tmp_path, previous_code={"Python3": "print('py')\n"})

    with pytest.raises(CgPuzzleManagerError, match="already using"):
        await manager.set_language("Python3")
