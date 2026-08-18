"""Async service endpoints for the async CodinGame client."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .cg_service import CgService, CgServiceHelper
from .services.achievement import CgAchievementService, CgAchievementServiceHelper
from .services.clash_of_code import CgClashOfCodeService, CgClashOfCodeServiceHelper
from .services.clash_of_code_description import (
    CgClashOfCodeDescriptionService,
    CgClashOfCodeDescriptionServiceHelper,
)
from .services.codingamer import CgCodingamerService, CgCodingamerServiceHelper
from .services.codingamer_puzzle_topic import (
    CgCodingamerPuzzleTopicService,
    CgCodingamerPuzzleTopicServiceHelper,
)
from .services.contribution import CgContributionService, CgContributionServiceHelper
from .services.featured_event import CgFeaturedEventService, CgFeaturedEventServiceHelper
from .services.intercom import CgIntercomService, CgIntercomServiceHelper
from .services.last_activities import CgLastActivitiesService, CgLastActivitiesServiceHelper
from .services.notification import CgNotificationService, CgNotificationServiceHelper
from .services.programming_language import CgProgrammingLanguageService, CgProgrammingLanguageServiceHelper
from .services.puzzle import CgPuzzleService, CgPuzzleServiceHelper
from .services.quest import CgQuestService, CgQuestServiceHelper
from .services.report import CgReportService, CgReportServiceHelper
from .services.search import CgSearchService, CgSearchServiceHelper
from .services.survey import CgSurveyService, CgSurveyServiceHelper
from .services.test_session import CgTestSessionService, CgTestSessionServiceHelper
from .services.test_session_question_submission import (
    CgTestSessionQuestionSubmissionService,
    CgTestSessionQuestionSubmissionServiceHelper,
)
from .services.topic import CgTopicService, CgTopicServiceHelper
from .services.user import CgUserService, CgUserServiceHelper
from .services.vote import CgVoteService, CgVoteServiceHelper

if TYPE_CHECKING:
    from ..client import CgClient

__all__ = [
    "CgService",
    "CgServiceHelper",
    "CgClient",
    "CgServices",
    "CgAchievementService",
    "CgAchievementServiceHelper",
    "CgClashOfCodeService",
    "CgClashOfCodeServiceHelper",
    "CgClashOfCodeDescriptionService",
    "CgClashOfCodeDescriptionServiceHelper",
    "CgCodingamerService",
    "CgCodingamerServiceHelper",
    "CgCodingamerPuzzleTopicService",
    "CgCodingamerPuzzleTopicServiceHelper",
    "CgContributionService",
    "CgContributionServiceHelper",
    "CgFeaturedEventService",
    "CgFeaturedEventServiceHelper",
    "CgIntercomService",
    "CgIntercomServiceHelper",
    "CgLastActivitiesService",
    "CgLastActivitiesServiceHelper",
    "CgNotificationService",
    "CgNotificationServiceHelper",
    "CgProgrammingLanguageService",
    "CgProgrammingLanguageServiceHelper",
    "CgPuzzleService",
    "CgPuzzleServiceHelper",
    "CgQuestService",
    "CgQuestServiceHelper",
    "CgReportService",
    "CgReportServiceHelper",
    "CgSearchService",
    "CgSearchServiceHelper",
    "CgSurveyService",
    "CgSurveyServiceHelper",
    "CgTopicService",
    "CgTopicServiceHelper",
    "CgTestSessionService",
    "CgTestSessionServiceHelper",
    "CgTestSessionQuestionSubmissionService",
    "CgTestSessionQuestionSubmissionServiceHelper",
    "CgUserService",
    "CgUserServiceHelper",
    "CgVoteService",
    "CgVoteServiceHelper",
]

class CgServices:
    """
    Service endpoints for the async CodinGame client.

    An instance of this class is created on CgClient, giving users well-typed access to all service endpoints.
    For example, to find a codingamer's points stats by their handle:

        async with CgClient() as client:
            stats = await client.services.codingamer.find_codingame_points_stats_by_handle("some_handle")
    """

    client: CgClient
    """The client through which endpoint requests are made."""

    # well-typed service endpoints
    achievement: CgAchievementService
    clash_of_code: CgClashOfCodeService
    clash_of_code_description: CgClashOfCodeDescriptionService
    codingamer: CgCodingamerService
    codingamer_puzzle_topic: CgCodingamerPuzzleTopicService
    contribution: CgContributionService
    featured_event: CgFeaturedEventService
    intercom: CgIntercomService
    last_activities: CgLastActivitiesService
    notification: CgNotificationService
    programming_language: CgProgrammingLanguageService
    puzzle: CgPuzzleService
    quest: CgQuestService
    report: CgReportService
    search: CgSearchService
    survey: CgSurveyService
    topic: CgTopicService
    test_session: CgTestSessionService
    test_session_question_submission: CgTestSessionQuestionSubmissionService
    user: CgUserService
    vote: CgVoteService

    def __init__(self, client: CgClient) -> None:
        self.client = client
        self.achievement = CgAchievementService(client)
        self.clash_of_code = CgClashOfCodeService(client)
        self.clash_of_code_description = CgClashOfCodeDescriptionService(client)
        self.codingamer = CgCodingamerService(client)
        self.codingamer_puzzle_topic = CgCodingamerPuzzleTopicService(client)
        self.contribution = CgContributionService(client)
        self.featured_event = CgFeaturedEventService(client)
        self.intercom = CgIntercomService(client)
        self.last_activities = CgLastActivitiesService(client)
        self.notification = CgNotificationService(client)
        self.programming_language = CgProgrammingLanguageService(client)
        self.puzzle = CgPuzzleService(client)
        self.quest = CgQuestService(client)
        self.report = CgReportService(client)
        self.search = CgSearchService(client)
        self.survey = CgSurveyService(client)
        self.topic = CgTopicService(client)
        self.test_session = CgTestSessionService(client)
        self.test_session_question_submission = CgTestSessionQuestionSubmissionService(client)
        self.user = CgUserService(client)
        self.vote = CgVoteService(client)
