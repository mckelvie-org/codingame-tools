"""
Async Topic service endpoint.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from json_data_types import JsonDict

from ...common.protocol.contribution import CgTopic
from ..cg_service import CgService, CgServiceHelper

if TYPE_CHECKING:
    from ...client import CgClient


class CgTopicServiceHelper(CgServiceHelper["CgTopicService"]):
    """Helper methods for CgTopicService. Currently empty."""


class CgTopicService(CgService):
    """Async Topic service endpoint."""

    def __init__(self, client: CgClient) -> None:
        super().__init__(client, "Topic")
        self.helper = CgTopicServiceHelper(self)

    async def get_all_children_topics_with_puzzle_count(self) -> list[CgTopic]:
        """The full catalogue of puzzle topics a contribution can be tagged with, each with the
           number of published puzzles currently carrying it.

           "Children" topics are the leaves an author actually picks--`parent_topic_id` names the
           grouping they hang off, and no parent appears in the list itself.

           Returns the same `CgTopic` shape that `findContribution` reports under a contribution's
           own `topics`, which is what makes tagging a contribution a copy rather than a
           translation: 135 topics as of 2026-08, every one carrying `id`, `handle`, `category`,
           `label_map` and `puzzle_count`. Only `puzzle_count` drifts between this catalogue and a
           topic already stored on a contribution, since it counts the live puzzle population.

           Takes no arguments and needs no authentication.

        Returns:
            Every selectable topic, in the server's own order.

        Raises:
            CgClientHttpError:
                If a transport error occurs, if the response content could not be decoded at all,
                if the status code is not 2xx, or if the decoded content is not a list.
        """
        raw_topics = await self.service_request_to_list(
                "getAllChildrenTopicsWithPuzzleCount", [])
        return CgTopic.from_list(cast(list[JsonDict], raw_topics))
