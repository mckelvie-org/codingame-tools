"""
Async ProgrammingLanguage service endpoint.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ...common.protocol.typedefs import CgSolutionLanguage
from ..cg_service import CgService, CgServiceHelper

if TYPE_CHECKING:
    from ...client import CgClient


class CgProgrammingLanguageServiceHelper(CgServiceHelper["CgProgrammingLanguageService"]):
    """Helper methods for CgProgrammingLanguageService. Currently empty."""


class CgProgrammingLanguageService(CgService):
    """Async ProgrammingLanguage service endpoint."""
    
    def __init__(self, client: CgClient) -> None:
        super().__init__(client, "ProgrammingLanguage")
        self.helper = CgProgrammingLanguageServiceHelper(self)

    async def find_all_ids(self) -> list[CgSolutionLanguage]:
        """Find the IDs of all programming languages supported for contribution reference solutions.

        Returns:
            A list of `CgSolutionLanguage` strings, e.g. "Python3", "Java", "C++".

        Raises:
            CgAuthenticationError:
                If the session is not authenticated and cannot implicitly login.
            CgClientHttpError:
                If a transport error occurs, if the response content could not be decoded at all,
                if the status code is not 2xx, or if the decoded content is not a list.
        """
        raw_ids = await self.service_request_to_list("findAllIds", [])
        return cast(list[CgSolutionLanguage], raw_ids)
