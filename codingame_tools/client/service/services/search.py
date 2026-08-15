"""
Async Search service endpoint.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from json_data_types import JsonDict

from ...common.protocol.search import CgSearchResult, CgSearchResultType
from ..cg_service import CgService, CgServiceHelper

if TYPE_CHECKING:
    from ...client import CgClient


class CgSearchServiceHelper(CgServiceHelper["CgSearchService"]):
    """Helper methods for CgSearchService. Currently empty."""


class CgSearchService(CgService):
    """Async Search service endpoint."""
    
    def __init__(self, client: CgClient) -> None:
        super().__init__(client, "Search")
        self.helper = CgSearchServiceHelper(self)

    async def search(
                self,
                query: str,
                locale: str = "en",
                type_filter: CgSearchResultType | None = None,
            ) -> list[CgSearchResult]:
        """Search for codingamers, puzzles, and other objects by name.

        Args:
            query:       The search query text, e.g. a codingamer's pseudo or part of a puzzle title.
            locale:      Locale code for localized result names, e.g. "en", "fr". Defaults to "en".
            type_filter: If provided, restricts results to a single `CgSearchResultType` (e.g.
                         "USER", "PUZZLE"). Passing a list/tuple of types instead of a single
                         string is rejected by the server with a 422 INVALID_PARAMETERS error.
                         If not provided, results of all types are returned.

        Returns:
            A list of CgSearchResult objects.

        Raises:
            CgAuthenticationError:
                If the session is not authenticated and cannot implicitly login.
            CgClientHttpError:
                If a transport error occurs, if the response content could not be decoded at all,
                if the status code is not 2xx, or if the decoded content is not a list.
        """
        raw_results = await self.service_request_to_list(
                "search", [query, locale, type_filter])
        return CgSearchResult.from_list(cast(list[JsonDict], raw_results))
