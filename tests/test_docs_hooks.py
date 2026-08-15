"""Tests for the version shown in the documentation site's title.

Nothing else can catch a regression here. The title is cosmetic: a wrong version builds cleanly,
renders cleanly, and breaks no link -- it just tells every reader the wrong thing, on a page that
outlives the context it was read in.

The specific hazard is that patch releases share one published directory. 2.0.0 and 2.0.3 are both
served from `/2.0/`, so the URL cannot say which one you are reading and the title is the only place
that can.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import docs_hooks  # noqa: E402  (needs the path fix above)


@pytest.fixture
def deploy(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Simulate a build: the alias `mike` is deploying, and the version of the package built."""
    def _deploy(alias: str | None, package_version: str) -> str:
        if alias is None:
            monkeypatch.delenv(docs_hooks.MIKE_VERSION_VAR, raising=False)
        else:
            monkeypatch.setenv(docs_hooks.MIKE_VERSION_VAR, alias)
        monkeypatch.setattr(docs_hooks, "_package_version", lambda: package_version)
        return docs_hooks._version_label()
    return _deploy


def test_a_release_shows_the_exact_version_not_the_series(deploy: Any) -> None:
    """The point of the whole exercise: `/2.0/` built from 2.0.3 must say 2.0.3.

       Every patch release overwrites the previous one at the same URL, so a reader who cannot see
       the patch version has no way to find it out."""
    assert deploy("2.0", "2.0.3") == "2.0.3"


def test_patch_releases_in_a_series_are_distinguishable(deploy: Any) -> None:
    """The regression that matters is subtler than a wrong string: it is two different builds
       carrying the same label, which is what showing the series did."""
    labels = {deploy("2.0", version) for version in ("2.0.0", "2.0.1", "2.0.3")}
    assert len(labels) == 3, "two patch releases would be indistinguishable in the title"


def test_dev_keeps_its_alias(deploy: Any) -> None:
    """`dev`'s package version is a snapshot (`2.0.1.dev1`) naming nothing anyone can navigate to,
       so the alias -- which the version selector also lists -- stays."""
    assert deploy("dev", "2.0.1.dev1") == "dev"


def test_a_local_build_shows_the_package_version(deploy: Any) -> None:
    """No alias outside a `mike` deploy, so the package version is the most specific thing there
       is -- including a dev version, which is accurate for a working tree."""
    assert deploy(None, "2.0.1.dev1") == "2.0.1.dev1"


@pytest.mark.parametrize(("alias", "package"), [
    # A version from a different series than the alias claims: the build is not what it says.
    ("2.0", "2.1.0"),
    ("2.1", "2.0.3"),
    # Not a final release, so it names no published version.
    ("2.0", "2.0.4.dev1"),
    ("2.0", "2.0.4rc1"),
    # No package version at all (the import guard).
    ("2.0", ""),
])
def test_a_version_that_contradicts_the_alias_is_not_used(deploy: Any, alias: str,
                                                          package: str) -> None:
    """A title disagreeing with its own URL is worse than one that is merely imprecise, so the
       alias wins whenever the package version is not a release belonging to it."""
    assert deploy(alias, package) == alias


def test_on_config_appends_the_version_to_the_site_name(deploy: Any,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(docs_hooks.MIKE_VERSION_VAR, "2.0")
    monkeypatch.setattr(docs_hooks, "_package_version", lambda: "2.0.3")
    config = docs_hooks.on_config({"site_name": "codingame-tools"})
    assert config["site_name"] == "codingame-tools 2.0.3"


def test_on_config_is_idempotent(deploy: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """`mkdocs serve` re-runs hooks on every reload; a title growing a version per keystroke would
       be a memorable bug."""
    monkeypatch.setenv(docs_hooks.MIKE_VERSION_VAR, "2.0")
    monkeypatch.setattr(docs_hooks, "_package_version", lambda: "2.0.3")
    config = {"site_name": "codingame-tools"}
    for _ in range(3):
        config = docs_hooks.on_config(config)
    assert config["site_name"] == "codingame-tools 2.0.3"
