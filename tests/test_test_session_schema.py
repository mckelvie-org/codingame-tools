"""Schema tests for `codingame_tools.client.common.protocol.test_session`.

Pure/local--no network. These pin down shapes confirmed against the live API, especially the ones
where a field is *absent* rather than null, which is what breaks a required-field dataclass.
"""

from __future__ import annotations

from typing import Any

from codingame_tools.client.common.protocol.test_session import CgTestSessionQuestionDetails


def _question_payload(**overrides: Any) -> dict[str, Any]:
    """A `TestSession/startTestSession` question payload, in the shape the live API returns."""
    payload: dict[str, Any] = {
            "id": 30498,
            "initialId": 30498,
            "title": "Temperatures - GE",
            "statement": "<div>the goal</div>",
            "stubGenerator": "read n:int\nloopline n t:int\nwrite result",
            "duration": 559873,
            "index": 0,
            "userId": -2,
            "type": "MULTIPLE_LANGUAGES",
            "availableLanguages": [{"id": "C++", "name": "C++"}, {"id": "Python3", "name": "Python 3"}],
            "testCases": [
                    {"index": 1, "inputBinaryId": 14654744848642,
                     "outputBinaryId": 14654758982243, "label": "Simple test case"},
                ],
        }
    payload.update(overrides)
    return payload


def test_an_official_puzzle_has_no_contributor_or_contribution() -> None:
    """Confirmed live (2026-08-02) importing "Temperatures": a puzzle CodinGame provides itself
       omits `contributor`/`contribution` **entirely** (not null), because it was never a community
       contribution. Both were required fields, so every official puzzle failed to parse at all.
       The sentinel `userId: -2` travels with this shape."""
    question = CgTestSessionQuestionDetails.from_dict(_question_payload())

    assert question.title == "Temperatures - GE"
    assert question.contributor is None
    assert question.contribution is None
    assert question.user_id == -2


def test_a_community_puzzle_still_carries_both() -> None:
    question = CgTestSessionQuestionDetails.from_dict(_question_payload(
            userId=42,
            contributor={"userId": 42, "pseudo": "someone", "publicHandle": "h"},
            contribution={"id": 7, "publicHandle": "ch", "status": "ACCEPTED",
                          "moderators": [], "type": "PUZZLE_INOUT"},
        ))

    assert question.contributor is not None
    assert question.contributor.pseudo == "someone"
    assert question.contribution is not None
    assert question.contribution.contribution_type == "PUZZLE_INOUT"


def test_unknown_keys_still_land_in_extra_data() -> None:
    """`contributor`/`contribution` becoming optional moved them after the `CatchAll` field, and
       field order relative to `CatchAll` is exactly the kind of thing that breaks silently--neither
       mypy nor a type check catches it, only a real `from_dict()` round-trip does."""
    question = CgTestSessionQuestionDetails.from_dict(
            _question_payload(someFutureUnknownKey={"a": 1}))

    assert question.extra_data == {"someFutureUnknownKey": {"a": 1}}
    assert question.question_type == "MULTIPLE_LANGUAGES"
