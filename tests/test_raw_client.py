"""Tests for codingame_tools.client.common.raw_client, backed by VCR cassettes (see conftest.py).

These exercise genuinely public, unauthenticated endpoints, so the cassettes here were
recorded from real live requests but require no login and carry no cookie data.
"""

from __future__ import annotations

import pytest

from codingame_tools.client.common.raw_client import CgClientHttpError, CgRawClient


@pytest.mark.usefixtures("vcr_cassette")
async def test_find_codingamer_public_informations() -> None:
    async with CgRawClient() as client:
        result = await client.service_request_to_dict(
            "CodinGamer", "findCodinGamerPublicInformations", [1486857],
            require_login=False,
        )
    assert result["userId"] == 1486857
    assert result["pseudo"] == "sammck"
    assert "publicHandle" in result


@pytest.mark.usefixtures("vcr_cassette")
async def test_service_request_body_must_be_json_array_error() -> None:
    """Regression test: the request body must be a bare JSON array, not {"args": [...]}."""
    async with CgRawClient() as client:
        with pytest.raises(CgClientHttpError) as exc_info:
            async with client.session.post(
                f"{client.CODINGAME_SERVICES_URL}CodinGamer/findCodinGamerPublicInformations",
                json={"args": [1486857]},
            ) as response:
                await client.get_json_data_response(response)
    error = exc_info.value
    assert error.status_code == 400
    assert error.api_error_response is not None
    assert error.api_error_response.code == "BODY_MUST_BE_JSON_ARRAY"


# --- error messages carry the server's explanation ----------------------------------------------


def test_an_unstructured_error_body_still_reaches_the_message() -> None:
    """The body is the only thing distinguishing one 422 from another.

       Regression: the body was surfaced *only* when it was a dict carrying a "code" key, which is
       the shape `CgClientErrorResponse` parses. Any other shape left `api_error_response` as None
       and produced a bare "422 Unprocessable Entity", with the explanation sitting unused in
       `content`. A real `cg contribution push` failure reported exactly that and nothing else."""
    error = CgClientHttpError(
            response=None, status_code=422,
            content={"id": 42, "message": "Validation failed: stubGenerator line 4"})

    assert error.api_error_response is None  # not the structured shape
    assert "stubGenerator line 4" in str(error)


@pytest.mark.parametrize("content,expected", [
    ([{"field": "statement", "error": "too long"}], "too long"),
    ("plain text explanation", "plain text explanation"),
    (b"bytes explanation", "bytes explanation"),
])
def test_every_body_shape_is_rendered(content: object, expected: str) -> None:
    """JSON arrays, bare strings and raw bytes all reach the user. Only the dict-with-code shape
       was ever handled, and the server is not obliged to use it."""
    assert expected in str(CgClientHttpError(response=None, status_code=422, content=content))  # type: ignore[arg-type]


def test_a_huge_body_is_truncated_rather_than_pasted_whole() -> None:
    """An HTML error page must not land a five-kilobyte blob in a traceback--but the length is
       reported, so a truncated body doesn't look like the whole story."""
    message = str(CgClientHttpError(
            response=None, status_code=422, content="<html>" + "x" * 5000 + "</html>"))

    assert len(message) < 800
    assert "5013 chars total" in message


def test_a_structured_error_still_reads_as_before() -> None:
    """The dict-with-code path is unchanged: it stays the nicest rendering, and gains nothing from
       having the raw body appended to it."""
    error = CgClientHttpError(
            response=None, status_code=400,
            content={"code": "INVALID_PARAMETERS", "message": "statement too long"})

    assert error.api_error_response is not None
    assert str(error).endswith("INVALID_PARAMETERS: statement too long")
    assert "{" not in str(error)  # the raw body is not also pasted in


def test_no_body_leaves_a_clean_message() -> None:
    assert str(CgClientHttpError(response=None, status_code=500)).endswith("500 Internal Server Error")
