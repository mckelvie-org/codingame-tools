"""
Async TestSession service endpoint.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from json_data_types import JsonData

from ...common.protocol.test_session import CgPlayRequest, CgPlayResult, CgSubmitRequest, CgTestSession
from ...common.protocol.typedefs import CgSolutionLanguage
from ..cg_service import CgService, CgServiceHelper

if TYPE_CHECKING:
    from ...client import CgClient


class CgTestSessionServiceHelper(CgServiceHelper["CgTestSessionService"]):
    """Helper methods for CgTestSessionService. Currently empty."""


class CgTestSessionService(CgService):
    """Async TestSession service endpoint."""

    def __init__(self, client: CgClient) -> None:
        super().__init__(client, "TestSession")
        self.helper = CgTestSessionServiceHelper(self)

    async def start_test_session(self, test_session_handle: str) -> CgTestSession:
        """Start (or resume) an interactive IDE test session for a puzzle.

           This is the API called by the web client when a codingamer clicks "Solve in IDE" on
           a puzzle. `test_session_handle` is a puzzle-specific handle (e.g.
           `CgLastActivityPuzzle.test_session_handle`, as returned by
           Puzzle/findProgressByIds/findProgressByPrettyId or embedded in a "PUZZLE"-type
           `CgLastActivity`)--not a codingamer or contribution handle.

        Args:
            test_session_handle: The puzzle's test session handle.

        Returns:
            A CgTestSession object.

        Raises:
            CgAuthenticationError:
                If the session is not authenticated and cannot implicitly login.
            CgClientHttpError:
                If a transport error occurs, if the response content could not be decoded at all,
                if the status code is not 2xx, or if the decoded content is not a dict.
        """
        raw_session = await self.service_request_to_dict("startTestSession", [test_session_handle])
        return CgTestSession.from_dict(raw_session)

    async def play(
                self,
                test_session_handle: str,
                request: CgPlayRequest,
            ) -> CgPlayResult:
        """Run a codingamer's code against a single test case within a test session.

           This is the API invoked by the IDE's "Test"/"Run" button (as opposed to a full
           "Submit"). Confirmed empirically: `result.comparison` is always present; when the
           code fails to compile/parse or raises an uncaught exception, `result.error` is also
           populated (with a stack trace) and `result.output` is empty. See `CgPlayResult` for
           the full breakdown of what's present in each case.

        Args:
            test_session_handle: The puzzle's test session handle (see
                                  `start_test_session`).
            request:              The code/language/test-case-selection payload to run.

        Returns:
            A CgPlayResult object.

        Raises:
            CgAuthenticationError:
                If the session is not authenticated and cannot implicitly login.
            CgClientHttpError:
                If a transport error occurs, if the response content could not be decoded at all,
                if the status code is not 2xx, or if the decoded content is not a dict.
        """
        raw_result = await self.service_request_to_dict("play", [test_session_handle, request.to_dict()])
        return CgPlayResult.from_dict(raw_result)

    async def generate_lsp_token(self, test_session_id: int) -> str:
        """Generate a Language Server Protocol (LSP) auth token for a test session.

           Used by the IDE to authenticate to a separate language-server backend for
           syntax highlighting, autocomplete, etc.--not useful for a code-driven client, and not
           explored further than confirming its shape. `test_session_id` is the numeric
           `CgTestSession.test_session_id` (distinct from the string `test_session_handle` used
           by `start_test_session`/`play`).

           The returned JWT (RS256-signed, confirmed by decoding one) has payload claims
           `aud: "LanguageServer"`, `application: "CodinGame IDE"`, a human-readable `context`
           (e.g. 'Puzzle literary-alfabet-soupe (id: 10075)'), and `sub`/`userId` identifying the
           codingamer. Observed validity: 1 hour.

        Args:
            test_session_id: The test session's numeric ID (`CgTestSession.test_session_id`).

        Returns:
            The signed JWT string.

        Raises:
            CgAuthenticationError:
                If the session is not authenticated and cannot implicitly login.
            CgClientHttpError:
                If a transport error occurs, if the response content could not be decoded at all,
                if the status code is not 2xx, or if the decoded content is not a str.
        """
        result = await self.service_request("generateLspToken", [test_session_id])
        return cast(str, result)

    async def get_previous_code_by_language_id(
                self,
                test_session_handle: str,
                programming_language_id: CgSolutionLanguage,
            ) -> str | None:
        """Fetch the codingamer's most recently saved code for one language in a test session.

           CodinGame keeps your latest source *per language* for a puzzle, not just one. A test
           session hands back whichever language you last used; this reaches the others, and is how
           the IDE's language dropdown restores your previous work when you switch.

           Two semantics confirmed live (2026-08-02) against "Temperatures", both easy to assume
           wrongly:

           - **This is a pure read.** It does *not* make `programming_language_id` the session's
             current language--after fetching Python3 from a session whose current language was
             C++, the session still reported C++. The current language only moves when you actually
             run a test against it or submit it (see `play`/`submit`).
           - **A language you have never attempted returns `None`**, not a generated starter stub
             (verified with Haskell). There is nothing saved to return, and this API does not
             render a stub from the puzzle's `stub_generator`.

        Args:
            test_session_handle:     The puzzle's test session handle.
            programming_language_id: CodinGame's language ID, e.g. "Python3", "C++" (see
                                      `CgSolutionLanguage`).

        Returns:
            The saved source for that language, or `None` if the codingamer has never attempted
            this puzzle in it.

        Raises:
            CgAuthenticationError:
                If the session is not authenticated and cannot implicitly login.
            CgClientHttpError:
                If a transport error occurs, if the response content could not be decoded at all,
                or if the status code is not 2xx.
        """
        result = await self.service_request(
                "getPreviousCodeByLanguageId", [test_session_handle, programming_language_id])
        return None if result is None else cast(str, result)

    async def submit(
                self,
                test_session_handle: str,
                request: CgSubmitRequest,
                arg3: JsonData | None = None,
            ) -> int:
        """Submit a final solution to a puzzle for credit.

           This is the API invoked by the IDE's "Submit" button--unlike `play`, it validates
           against all of the puzzle's private validator test cases rather than a single local
           one. Confirmed empirically: returns quickly with a new submission ID, and (at least
           for a small/fast puzzle) full results were already available via
           Report/findReportBySubmission by the time the response came back--grading appears to
           happen before the response is returned, not asynchronously, in that case.

           CAUTION: for a puzzle with many/heavy validator test cases, the server needs to
           instantiate containers and run the code once per validator, which can take a long
           time. It may eventually become necessary to handle Cloudflare-level
           timeouts/disconnects here and poll for a result instead of assuming a synchronous
           response--a similar open concern exists for contribution submission, not yet
           implemented in this client.

           `arg3`'s purpose is unknown; only observed as None.

        Args:
            test_session_handle: The puzzle's test session handle.
            request:              The code/language payload to submit.
            arg3:                 Third positional argument to the underlying submit API call.
                                  Purpose unknown; defaults to None.

        Returns:
            The new submission's numeric ID (see Report/findReportBySubmission).

        Raises:
            CgAuthenticationError:
                If the session is not authenticated and cannot implicitly login.
            CgClientHttpError:
                If a transport error occurs, if the response content could not be decoded at all,
                if the status code is not 2xx, or if the decoded content is not an int.
        """
        result = await self.service_request(
                "submit", [test_session_handle, request.to_dict(), arg3])
        return cast(int, result)
