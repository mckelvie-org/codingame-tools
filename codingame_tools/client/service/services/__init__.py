"""Async per-service endpoint implementations."""

from __future__ import annotations

from .achievement import CgAchievementService, CgAchievementServiceHelper
from .clash_of_code import CgClashOfCodeService, CgClashOfCodeServiceHelper
from .clash_of_code_description import CgClashOfCodeDescriptionService, CgClashOfCodeDescriptionServiceHelper
from .codingamer import CgCodingamerService, CgCodingamerServiceHelper
from .codingamer_puzzle_topic import CgCodingamerPuzzleTopicService, CgCodingamerPuzzleTopicServiceHelper
from .contribution import CgContributionService, CgContributionServiceHelper
from .featured_event import CgFeaturedEventService, CgFeaturedEventServiceHelper
from .intercom import CgIntercomService, CgIntercomServiceHelper
from .last_activities import CgLastActivitiesService, CgLastActivitiesServiceHelper
from .notification import CgNotificationService, CgNotificationServiceHelper
from .programming_language import CgProgrammingLanguageService, CgProgrammingLanguageServiceHelper
from .puzzle import CgPuzzleService, CgPuzzleServiceHelper
from .quest import CgQuestService, CgQuestServiceHelper
from .report import CgReportService, CgReportServiceHelper
from .search import CgSearchService, CgSearchServiceHelper
from .survey import CgSurveyService, CgSurveyServiceHelper
from .test_session import CgTestSessionService, CgTestSessionServiceHelper
from .test_session_question_submission import CgTestSessionQuestionSubmissionService, CgTestSessionQuestionSubmissionServiceHelper
from .topic import CgTopicService, CgTopicServiceHelper
from .user import CgUserService, CgUserServiceHelper
from .vote import CgVoteService, CgVoteServiceHelper

__all__ = [
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
