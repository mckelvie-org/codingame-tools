"""Shared pytest fixtures for the test suite.

Provides a VCR-backed fixture for recording/replaying real HTTP interactions with the
CodinGame API as "cassette" files under tests/cassettes/, so that most of the test suite
can run in CI with no live network access and no credentials.

Cassette recording strips all cookies--both the Cookie request header and any Set-Cookie
response headers--before anything is written to disk. This is deliberate and unconditional:
CodinGame's servers (and the AWS load balancer in front of them) set cookies, including
session-identifying ones like AWSALB/AWSALBCORS, on virtually every response, and none of
that should ever end up in a file that gets committed to the repo. See
test_cassette_hygiene.py for an automated check that no cassette violates this.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import vcr

CASSETTE_DIR = Path(__file__).parent / "cassettes"

_SENSITIVE_RESPONSE_HEADERS = {"set-cookie", "set-cookie2"}


def _scrub_response_cookies(response: dict[str, Any]) -> dict[str, Any]:
    """VCR before_record_response hook: strips all Set-Cookie/Set-Cookie2 headers."""
    headers = response.get("headers") or {}
    for key in list(headers.keys()):
        if key.lower() in _SENSITIVE_RESPONSE_HEADERS:
            del headers[key]
    return response


cg_vcr = vcr.VCR(
    cassette_library_dir=str(CASSETTE_DIR),
    record_mode="once",
    filter_headers=[("cookie", None), ("authorization", None)],
    before_record_response=_scrub_response_cookies,
    # Deliberately excludes "body": vcrpy's default body matcher chokes on request bodies that
    # its own YAML cassette serializer already decoded into a list/dict (rather than a raw JSON
    # string), raising TypeError on replay. Since each cassette here holds exactly one interaction
    # (one test == one cassette file), matching on method/URL alone is unambiguous.
    match_on=["method", "scheme", "host", "port", "path", "query"],
)
"""Shared VCR instance. record_mode="once" means: if a test's cassette file doesn't exist yet,
   record it from a real (live) request; if it does exist, replay it strictly and never touch
   the network. Delete a cassette file locally and re-run its test to re-record it from a
   fresh real API response.
"""


@pytest.fixture
def vcr_cassette(request: pytest.FixtureRequest) -> Iterator[None]:
    """Wraps a test in a cassette named after the test function.

       Usage:
           @pytest.mark.usefixtures("vcr_cassette")
           async def test_something():
               ...
    """
    cassette_name = f"{request.node.name}.yaml"
    with cg_vcr.use_cassette(cassette_name):
        yield


class _FakeGlobalPlatformDirs:
    """Stand-in for the object `codingame_tools.config.resolver._global_platformdirs()`
       returns, pointed at a tmp_path subtree."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def user_config_dir(self) -> str:
        return str(self._root / "config")

    @property
    def user_data_dir(self) -> str:
        return str(self._root / "data")

    @property
    def user_cache_dir(self) -> str:
        """Redirected for the same reason as the others: `cg doc` builds here when the checkout it
           was installed from is not writable, and a test suite must not write into the real
           ~/.cache -- nor read a build left there by a previous run."""
        return str(self._root / "cache")


@pytest.fixture(autouse=True)
def fake_global_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirects the global (per-user) fallback config/data location into an isolated tmp_path
       subtree, so tests never touch the real machine's actual global config.

       autouse: since CgConfig.settings (added for the global<->project config.yaml settings
       merge) reads the global config file location on every access, ANY test touching
       CgConfig/CgSettings--not just config-discovery tests--could otherwise silently pick up
       whatever really lives at e.g. ~/.config/codingame/cg/config.yaml on the machine running
       the suite. Tests that also want the returned Path (to write a fake global config.yaml into
       it) can still take it as a normal fixture parameter, same as before."""
    root = tmp_path / "global_root"
    monkeypatch.setattr(
        "codingame_tools.config.resolver._global_platformdirs",
        lambda: _FakeGlobalPlatformDirs(root),
    )
    return root


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirects Path.home() into an isolated tmp_path subtree, so config-discovery tests
       involving $HOME (e.g. the upward-search stop-at-$HOME rule) run deterministically."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    return home
