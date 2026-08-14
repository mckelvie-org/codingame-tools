"""
Async Contribution service endpoint.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast

from json_data_types import JsonDict

from ...common.protocol.contribution import (
    CgContribution,
    CgContributionData,
    CgContributionId,
    CgContributionModerator,
    CgDeleteContributionResult,
    CgModerationAction,
    CgPendingContribution,
    CgPersonalContribution,
    CgPuzzleType,
)
from ...common.raw_client import CgAuthenticationError, CgClientHttpError
from ..cg_service import CgService, CgServiceHelper

if TYPE_CHECKING:
    from ...client import CgClient

logger = logging.getLogger(__name__)


class CgContributionServiceHelper(CgServiceHelper["CgContributionService"]):
    """Helper methods for CgContributionService."""

    _POLL_INTERVAL_SECONDS = 30.0
    """How often to re-check `find_contribution` while waiting out a 524 in `update_contribution`."""

    async def _poll_until_committed(
                self,
                contribution_id: CgContributionId,
                prev_version: int,
                deadline: float | None,
                submitted_data: CgContributionData,
                on_poll: Callable[[CgContribution], Awaitable[None]] | None,
            ) -> CgContribution:
        """Poll `find_contribution` every `_POLL_INTERVAL_SECONDS` until its version increments
           past `prev_version` (indicating the server committed the update), or `deadline`
           (a `time.monotonic()`-relative deadline, or None to wait indefinitely) passes."""
        while True:
            if deadline is None:
                wait = self._POLL_INTERVAL_SECONDS
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Timed out waiting for contribution {contribution_id!r} version to "
                        f"increment past {prev_version} after an HTTP 524; the update may still "
                        "complete server-side."
                    )
                wait = min(self._POLL_INTERVAL_SECONDS, remaining)
            logger.info("update_contribution: polling find_contribution for %r again in %.0fs...",
                        contribution_id, wait)
            await asyncio.sleep(wait)
            result = await self.service.find_contribution(contribution_id)
            if result.last_version.version > prev_version:
                if result.last_version.data != submitted_data:
                    logger.warning(
                        "update_contribution: committed data for contribution %r does not "
                        "exactly match the submitted data after 524 recovery", contribution_id)
                return result
            if on_poll is not None:
                await on_poll(result)

    async def update_contribution(
                self,
                contribution_id: CgContributionId,
                puzzle_type: CgPuzzleType,
                contribution_data: CgContributionData,
                draft: bool,
                ready_for_moderation: bool,
                prev_version: int,
                codingamer_id: int | None = None,
                *,
                max_wait_seconds: float = 0.0,
                on_poll: Callable[[CgContribution], Awaitable[None]] | None = None,
            ) -> CgContribution:
        """Submit a new version of a contribution's content, adding 524 retry/polling on top of
           the plain `CgContributionService.update_contribution`.

           Deliberately does no normalization of `contribution_data`. Newline canonicalization lives
           in `codingame_tools.common.text_files`, applied by the puzzle/contribution managers as
           they convert between server values and local files--not here, where it would silently
           rewrite a caller's data at the transport layer and, worse, half of a round trip whose
           other half lives somewhere else entirely.

           The server re-validates a contribution's full test suite on every update, which for
           heavy contributions can take long enough that Cloudflare's edge disconnects the
           request even though the origin call eventually completes successfully server-side. If
           that happens (an `CgClientHttpError` with `status_code == 524`), this method
           assumes the update likely succeeded and polls `find_contribution` every 30 seconds
           until `last_version.version` increments past `prev_version`, instead of propagating
           the 524.

        `contribution_id`, `puzzle_type`, `contribution_data`, `draft`, `ready_for_moderation`,
           `prev_version` and `codingamer_id` are passed straight through--see
           `CgContributionService.update_contribution`.

        Args:
            max_wait_seconds:
                How long to keep polling after a 524 before giving up, in seconds. 0 (the
                default) means wait indefinitely. Ignored entirely if no 524 occurs.
            on_poll: If given, awaited with each `CgContribution` observed while polling after a
                     524 (i.e. `find_contribution` results still at `prev_version`, before the
                     final, committed one)--unlike `CgReportServiceHelper.
                     find_report_by_submission_when_ready`'s `on_poll`, this one always carries
                     real (if stale) data. Never called at all if no 524 occurs. Doubles as a
                     cancellation hook: raise from it (or from an `await` inside it) to abort the
                     wait immediately, instead of only being able to give up via
                     `max_wait_seconds`. Any exception it raises propagates out of this method
                     uncaught.

        Returns:
            The updated CgContribution.

        Raises:
            CgAuthenticationError:
                If the session is not authenticated and cannot implicitly login, or if
                `codingamer_id` is not provided and no codingamer ID can be resolved from the
                session's credentials.
            CgClientHttpError:
                If a transport error occurs, if the response content could not be decoded at all,
                if the status code is not 2xx and not 524, or if the decoded content is not a
                dict.
            TimeoutError:
                If a 524 occurred and `max_wait_seconds` elapsed before the contribution's
                version incremented. The update may still complete server-side.
        """
        try:
            return await self.service.update_contribution(
                    contribution_id, puzzle_type, contribution_data, draft, ready_for_moderation,
                    prev_version, codingamer_id)
        except CgClientHttpError as e:
            if e.status_code != 524:
                raise
        logger.warning(
            "update_contribution: got HTTP 524 for contribution %r; server likely committed "
            "the update anyway. Polling find_contribution for the version to increment "
            "(max_wait_seconds=%s)...",
            contribution_id, "infinite" if max_wait_seconds <= 0 else max_wait_seconds,
        )
        deadline = None if max_wait_seconds <= 0 else time.monotonic() + max_wait_seconds
        return await self._poll_until_committed(
                contribution_id, prev_version, deadline, contribution_data, on_poll)

    async def create_contribution(
                self,
                puzzle_type: CgPuzzleType,
                contribution_data: CgContributionData,
                draft: bool,
                ready_for_moderation: bool,
                codingamer_id: int | None = None,
            ) -> CgContributionId:
        """Create a brand new contribution. Deliberately adds no 524 retry (see below) and, like
           `update_contribution`, no normalization of `contribution_data`.

           Unlike `update_contribution`, there is no `prev_version`-style idempotency check the
           server can use to reject a duplicate resubmission, and no existing `contribution_id`
           to poll `find_contribution` against if a request times out at Cloudflare's edge (the
           same `status_code == 524` scenario `update_contribution` recovers from by polling)--so
           blindly retrying here could create a second, duplicate contribution instead of
           recovering from one. This method therefore does NOT catch/retry on 524; it just logs
           an error making that risk explicit and re-raises, leaving the decision (retry and risk
           a duplicate, or go check `cg api contribution get-all-pending-contributions`/the
           CodinGame site first) to the caller.

        `puzzle_type`, `contribution_data`, `draft`, `ready_for_moderation` and `codingamer_id` are
           passed straight through--see `CgContributionService.create_contribution`.

        Returns:
            The new contribution's opaque public handle (`CgContributionId`).

        Raises:
            CgAuthenticationError:
                If the session is not authenticated and cannot implicitly login, or if
                `codingamer_id` is not provided and no codingamer ID can be resolved from the
                session's credentials.
            CgClientHttpError:
                If a transport error occurs, if the response content could not be decoded at all,
                if the status code is not 2xx, or if the decoded content is not a str. In
                particular, a 524 is NOT retried--see above--and is raised like any other error.
        """
        try:
            return await self.service.create_contribution(
                    puzzle_type, contribution_data, draft, ready_for_moderation, codingamer_id)
        except CgClientHttpError as e:
            if e.status_code == 524:
                logger.error(
                    "create_contribution: got HTTP 524; the contribution may or may not have "
                    "actually been created server-side. NOT retrying automatically (unlike "
                    "update_contribution, there's no prev_version-style check to prevent a retry "
                    "from creating a *second*, duplicate contribution)--check "
                    "get_all_pending_contributions/the CodinGame site before deciding whether to "
                    "resubmit.",
                )
            raise


class CgContributionService(CgService):
    """Async Contribution service endpoint."""
    
    def __init__(self, client: CgClient) -> None:
        super().__init__(client, "Contribution")
        self.helper = CgContributionServiceHelper(self)

    async def find_contribution(
                self,
                contribution_id: str,
                arg2: bool = True,
            ) -> CgContribution:
        """Find a contribution by its opaque contribution ID.

        Args:
            contribution_id: The opaque contribution ID string (see `CgContributionId`).
            arg2:            Second positional argument to the underlying findContribution API
                              call. Purpose unknown; defaults to True.

        Returns:
            A CgContribution object.

        Raises:
            CgAuthenticationError:
                If the session is not authenticated and cannot implicitly login.
            CgClientHttpError:
                If a transport error occurs, if the response content could not be decoded at all,
                if the status code is not 2xx, or if the decoded content is not a dict.
        """
        raw_contribution = await self.service_request_to_dict(
                "findContribution", [contribution_id, arg2])
        return CgContribution.from_dict(raw_contribution)

    async def find_new_contribution_count(
                self,
                codingamer_id: int | None = None,
                since: datetime | None = None,
            ) -> int:
        """Count new contributions (e.g. community puzzles) published since a given point in
           time, for a given codingamer.

           `since` is sent to the server as a bare epoch-millis integer, like every other
           epoch-millis argument in this API (including FeaturedEvent/findNewFeaturedEventCount,
           where CodinGame's own web client sends a quoted string but a bare int was confirmed
           to work equally well--see `CgFeaturedEventService.find_new_featured_event_count`).

        Args:
            codingamer_id: The codingamer to count new contributions for. If not provided,
                           defaults to the logged-in codingamer's ID.
            since:         Count contributions published after this point in time. If not
                           provided, defaults to now (which will always yield 0--callers
                           interested in a nonzero count should track their own reference
                           point, e.g. the last time they called this). Naive datetimes are
                           interpreted as local time (matching Python's own
                           `datetime.timestamp()` behavior).

        Returns:
            The number of new contributions published since `since`.

        Raises:
            CgAuthenticationError:
                If the session is not authenticated and cannot implicitly login, or if
                `codingamer_id` is not provided and no codingamer ID can be resolved from the
                session's credentials.
            CgClientHttpError:
                If a transport error occurs, if the response content could not be decoded at all,
                if the status code is not 2xx, or if the decoded content is not an int.
        """
        if codingamer_id is None:
            await self.require_authenticate()
            codingamer_id = self.client.codingamer_id
            if codingamer_id is None:
                raise CgAuthenticationError()
        if since is None:
            since = datetime.now(timezone.utc)
        since_ms = int(since.timestamp() * 1000)
        result = await self.service_request("findNewContributionCount", [codingamer_id, since_ms])
        return cast(int, result)

    async def find_contribution_moderators(
                self,
                contribution_numeric_id: int,
                action: CgModerationAction,
            ) -> list[CgContributionModerator]:
        """List the moderators who have cast a given vote on a PENDING contribution's
           approve/reject moderation gate--the privileged (moderator/high-level-codingamer-only)
           mechanism that actually decides whether a contribution gets published or rejected,
           confirmed live to require 3 `"validate"` votes to approve or 3 `"deny"` votes to
           reject. Entirely distinct from the ungated community up/down vote (`CgContribution.
           up_votes`/`down_votes`, `Vote/findVotableValuesById`)--do not conflate the two.

           The required threshold (3, either way) is not itself part of this response--only the
           current list of moderators on the requested side. Call this twice (once per `action`)
           to get both sides' current tallies (`len(result)`) and named voters.

        Args:
            contribution_numeric_id: The contribution's *numeric* ID (`CgContribution.id`)--NOT
                                      the opaque `public_handle`/`CgContributionId` string used
                                      by every other method on this service (`find_contribution`/
                                      `update_contribution`/etc.). Confirmed live: passing the
                                      numeric `id` (e.g. `149373`) works; the opaque handle was
                                      not tried here and is not expected to.
            action:                  `"validate"` (approve) or `"deny"` (reject)--see
                                      `CgModerationAction`.

        Returns:
            A list of CgContributionModerator objects--one per moderator who has cast that vote.
            Empty if nobody has voted that way yet.

        Raises:
            CgAuthenticationError:
                If the session is not authenticated and cannot implicitly login.
            CgClientHttpError:
                If a transport error occurs, if the response content could not be decoded at all,
                if the status code is not 2xx, or if the decoded content is not a list.
        """
        raw_moderators = await self.service_request_to_list(
                "findContributionModerators", [contribution_numeric_id, action])
        return CgContributionModerator.from_list(cast(list[JsonDict], raw_moderators))

    async def get_all_pending_contributions(
                self,
                contribution_type_filter: str = "ALL",
                codingamer_id: int | None = None,
                page: int = 1,
            ) -> list[CgPendingContribution]:
        """Get pending (community-review-queue) contributions.

           Raw argument order is `[page, contribution_type_filter, codingamer_id]`; this method
           reorders them to put the more commonly-varied `contribution_type_filter` first.

           `codingamer_id` must equal the logged-in codingamer's own ID--the server rejects any
           other value with a 403 (`UserRequired: Only a logged user is authorized to perform
           this operation`), confirmed empirically. It does NOT filter results to contributions
           authored by that codingamer (a single call returned contributions from 30 different
           authors)--it's presumably used to compute per-item context (e.g.
           `CgPendingContribution.user_moderation_status`) relative to the viewer, similar to
           `current_codingamer_id` on `CgCodingamerService.find_followers`.

           `contribution_type_filter` accepts coarse category values, confirmed empirically:
           "ALL" (every type), "CLASHOFCODE" (only Clash of Code), "PUZZLE" (every puzzle
           subtype--"PUZZLE_INOUT", "PUZZLE_OPTI", "PUZZLE_SOLO", "PUZZLE_MULTI--but not
           "CLASHOFCODE"). An unrecognized value (e.g. one of the specific `type` values itself,
           like "PUZZLE_INOUT") does not filter or error--it behaves like "ALL".

           `page` is assumed to be a 1-indexed page number, but this is not fully confirmed:
           `page=1` returned all 57 currently-pending contributions in one call; `page=0` caused
           a 500 Internal Server Error; every `page >= 2` tried returned an empty list. That's
           consistent with simple pagination where all current matches fit on page 1, but true
           multi-page behavior (page size, etc.) has never been observed.

        Args:
            contribution_type_filter: Category filter; see above. Defaults to "ALL".
            codingamer_id: Must equal the logged-in codingamer's own ID (server-enforced; see
                           above). If not provided, defaults to the logged-in codingamer's ID.
            page:          Assumed 1-indexed page number; see above. Defaults to 1.

        Returns:
            A list of CgPendingContribution objects.

        Raises:
            CgAuthenticationError:
                If the session is not authenticated and cannot implicitly login, or if
                `codingamer_id` is not provided and no codingamer ID can be resolved from the
                session's credentials.
            CgClientHttpError:
                If a transport error occurs, if the response content could not be decoded at all,
                if the status code is not 2xx (e.g. 403 if `codingamer_id` is not your own, or
                500 if `page` is 0), or if the decoded content is not a list.
        """
        if codingamer_id is None:
            await self.require_authenticate()
            codingamer_id = self.client.codingamer_id
            if codingamer_id is None:
                raise CgAuthenticationError()
        raw_contributions = await self.service_request_to_list(
                "getAllPendingContributions", [page, contribution_type_filter, codingamer_id])
        return CgPendingContribution.from_list(cast(list[JsonDict], raw_contributions))

    async def get_personal_contributions(
                self,
                codingamer_id: int | None = None,
                page: int = 1,
            ) -> list[CgPersonalContribution]:
        """List every contribution (any status--draft, PENDING, APPROVED, REFUSED, etc.)
           authored by a codingamer, e.g. for a "my contributions" listing page. Unlike
           `get_all_pending_contributions`'s `codingamer_id`, this one genuinely filters to just
           that codingamer's own contributions.

           `codingamer_id` must equal the logged-in codingamer's own ID--confirmed live that both
           an arbitrary ID (`1`) and a real, different codingamer's ID are rejected with a 422
           (no distinguishing error detail between the two cases); `page` is a real 1-indexed
           page number--confirmed live via the server's own error detail (`page=0` -> 422
           `INVALID_PAGE: Page must be at least 1`, unlike `get_all_pending_contributions`'s
           `page`, which merely 500s on `0` with no detail)--`page` values beyond the last page
           return `[]` rather than erroring.

        Args:
            codingamer_id: Must equal the logged-in codingamer's own ID (server-enforced; see
                           above). If not provided, defaults to the logged-in codingamer's ID.
            page:          1-indexed page number; see above. Defaults to 1.

        Returns:
            A list of CgPersonalContribution objects.

        Raises:
            CgAuthenticationError:
                If the session is not authenticated and cannot implicitly login, or if
                `codingamer_id` is not provided and no codingamer ID can be resolved from the
                session's credentials.
            CgClientHttpError:
                If a transport error occurs, if the response content could not be decoded at all,
                if the status code is not 2xx (e.g. 422 if `codingamer_id` isn't your own, or if
                `page` is less than 1), or if the decoded content is not a list.
        """
        if codingamer_id is None:
            await self.require_authenticate()
            codingamer_id = self.client.codingamer_id
            if codingamer_id is None:
                raise CgAuthenticationError()
        raw_contributions = await self.service_request_to_list(
                "getPersonalContributions", [codingamer_id, page])
        return CgPersonalContribution.from_list(cast(list[JsonDict], raw_contributions))

    async def update_contribution(
                self,
                contribution_id: CgContributionId,
                puzzle_type: CgPuzzleType,
                contribution_data: CgContributionData,
                draft: bool,
                ready_for_moderation: bool,
                prev_version: int,
                codingamer_id: int | None = None,
            ) -> CgContribution:
        """Submit a new version of a contribution's content.

           A thin wrapper over the raw API--no retries and no normalization of
           `contribution_data` are performed here. The server re-validates the full contribution
           (running all local and server-side validator test cases) on every call, though it is
           reportedly smart enough to skip re-running test cases whose content hasn't changed.
           For a contribution with many/heavy test cases, this re-validation can take long enough
           that Cloudflare's edge disconnects the request (surfacing as an
           `CgClientHttpError` with `status_code == 524`) even though the origin request
           eventually completes successfully server-side. See
           `CgContributionServiceHelper.update_contribution` (`self.helper.update_contribution`)
           for a version that layers retry/polling on top of this method.

        Args:
            contribution_id:      The opaque contribution ID (see `CgContributionId`).
            puzzle_type:          The type of the contribution, e.g. "PUZZLE_INOUT".
            contribution_data:    The new contribution content, typically obtained by mutating
                                  the `CgContributionData` returned by `find_contribution`.
            draft:                Whether this version is a private, unpublished draft.
            ready_for_moderation: Whether the contribution is being formally submitted for
                                  moderation (requiring 3 moderator upvotes and fewer than 3
                                  downvotes before the moderation window expires).
            prev_version:         The version number of the contribution as last retrieved via
                                  `find_contribution` (`CgContribution.last_version.version`).
                                  Serves as an idempotency/concurrency check--the server rejects
                                  the update if this doesn't match its current version.
            codingamer_id:        The authoring codingamer's numeric ID. If not provided,
                                  defaults to the logged-in codingamer's ID.

        Returns:
            The updated CgContribution.

        Raises:
            CgAuthenticationError:
                If the session is not authenticated and cannot implicitly login, or if
                `codingamer_id` is not provided and no codingamer ID can be resolved from the
                session's credentials.
            CgClientHttpError:
                If a transport error occurs, if the response content could not be decoded at all,
                if the status code is not 2xx (e.g. if `prev_version` is stale, or 524 if
                Cloudflare's edge disconnects while the origin is still validating--see above), or
                if the decoded content is not a dict.
        """
        if codingamer_id is None:
            await self.require_authenticate()
            codingamer_id = self.client.codingamer_id
            if codingamer_id is None:
                raise CgAuthenticationError()
        raw_result = await self.service_request_to_dict(
                "updateContribution",
                [
                        codingamer_id,
                        contribution_id,
                        puzzle_type,
                        contribution_data.to_dict(),
                        draft,
                        ready_for_moderation,
                        prev_version,
                    ])
        return CgContribution.from_dict(raw_result)

    async def create_contribution(
                self,
                puzzle_type: CgPuzzleType,
                contribution_data: CgContributionData,
                draft: bool,
                ready_for_moderation: bool,
                codingamer_id: int | None = None,
            ) -> CgContributionId:
        """Create a brand new contribution.

           A thin wrapper over the raw API--no retries are performed here (see
           `CgContributionServiceHelper.create_contribution`, `self.helper.create_contribution`,
           which deliberately doesn't add 524 retry either; see that method's docstring for why).
           No layer here normalizes `contribution_data`.
           Argument order/shape mirrors `update_contribution`, minus `contribution_id`/
           `prev_version` (there's no existing contribution yet, and thus nothing to reference).

           The response is just the new contribution's opaque public handle (a bare JSON string),
           unlike `update_contribution`'s full `CgContribution`--call `find_contribution(handle)`
           afterward for the rest (e.g. `id`, `last_version`).

        Args:
            puzzle_type:          The type of the contribution, e.g. "PUZZLE_INOUT".
            contribution_data:    The new contribution's initial content.
            draft:                Whether this version is a private, unpublished draft.
            ready_for_moderation: Whether the contribution is being formally submitted for
                                  moderation (requiring 3 moderator upvotes and fewer than 3
                                  downvotes before the moderation window expires).
            codingamer_id:        The authoring codingamer's numeric ID. If not provided,
                                  defaults to the logged-in codingamer's ID.

        Returns:
            The new contribution's opaque public handle (`CgContributionId`).

        Raises:
            CgAuthenticationError:
                If the session is not authenticated and cannot implicitly login, or if
                `codingamer_id` is not provided and no codingamer ID can be resolved from the
                session's credentials.
            CgClientHttpError:
                If a transport error occurs, if the response content could not be decoded at all,
                if the status code is not 2xx, or if the decoded content is not a str.
        """
        if codingamer_id is None:
            await self.require_authenticate()
            codingamer_id = self.client.codingamer_id
            if codingamer_id is None:
                raise CgAuthenticationError()
        raw_result = await self.service_request(
                "createContribution",
                [codingamer_id, puzzle_type, contribution_data.to_dict(), draft, ready_for_moderation])
        return cast(str, raw_result)

    async def delete_contribution(
                self,
                contribution_id: CgContributionId,
                codingamer_id: int | None = None,
            ) -> CgDeleteContributionResult:
        """Delete a contribution.

           Argument shape (`[codingamerId, contributionId]`), by analogy with
           `update_contribution`/`create_contribution` (both `codingamerId`-first)--confirmed
           live (2026-07-29) against a disposable draft contribution created via
           `create_contribution` for the purpose.

        Args:
            contribution_id: The opaque contribution ID (see `CgContributionId`) to delete.
            codingamer_id:   The authoring codingamer's numeric ID. If not provided, defaults to
                             the logged-in codingamer's ID.

        Returns:
            A `CgDeleteContributionResult` (an action ID and a success flag).

        Raises:
            CgAuthenticationError:
                If the session is not authenticated and cannot implicitly login, or if
                `codingamer_id` is not provided and no codingamer ID can be resolved from the
                session's credentials.
            CgClientHttpError:
                If a transport error occurs, if the response content could not be decoded at all,
                if the status code is not 2xx, or if the decoded content is not a dict.
        """
        if codingamer_id is None:
            await self.require_authenticate()
            codingamer_id = self.client.codingamer_id
            if codingamer_id is None:
                raise CgAuthenticationError()
        raw_result = await self.service_request_to_dict(
                "deleteContribution", [codingamer_id, contribution_id])
        return CgDeleteContributionResult.from_dict(raw_result)
