"""Tests for what `cg doc` decides to show.

The decision is version-sensitive and mostly unobservable: pick the wrong published directory and
the command still opens a window full of plausible documentation, just for a different release than
the one installed. Nothing downstream would notice, so the mapping is pinned here directly.

The `mike` deploy layout these assertions encode lives in `.github/workflows/docs.yml`; if that
changes, these tests are the place it has to be changed too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codingame_tools.docs import (
    DEV_ALIAS,
    DOCS_SITE_ROOT,
    LATEST_ALIAS,
    LocalDocsError,
    docs_alias_for_version,
    find_source_checkout,
    published_docs_url,
)
from codingame_tools.docs.local import base_path, site_dir, start_local_docs
from codingame_tools.version import __version__

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(("version", "expected"), [
    # Releases document as their minor series, the way mike aliases them.
    ("2.0.0", "2.0"),
    ("2.0.1", "2.0"),
    ("2.0.17", "2.0"),
    ("1.4.7", "1.4"),
    ("10.11.12", "10.11"),
    # A two-component version is still a release series.
    ("2.0", "2.0"),
    # Anything with a suffix was built from main after the last tag, so `dev` describes it. Its own
    # series does not exist on the site yet -- sending a pre-release user to /2.1/ would 404.
    ("2.1.0.dev1", DEV_ALIAS),
    ("2.1.0rc1", DEV_ALIAS),
    ("2.1.0-rc.1", DEV_ALIAS),
    ("2.0.1.dev1", DEV_ALIAS),
    ("2.0.1+local.build", DEV_ALIAS),
    # Unparseable falls back to current docs rather than to nothing.
    ("", LATEST_ALIAS),
    ("garbage", LATEST_ALIAS),
])
def test_docs_alias_for_version(version: str, expected: str) -> None:
    assert docs_alias_for_version(version) == expected


def test_published_url_defaults_to_the_installed_version() -> None:
    """The default target must track this build, not a hardcoded series."""
    assert published_docs_url() == f"{DOCS_SITE_ROOT}{docs_alias_for_version(__version__)}/"
    assert published_docs_url().startswith(DOCS_SITE_ROOT)
    assert published_docs_url().endswith("/")


def test_published_url_honours_an_explicit_version() -> None:
    assert published_docs_url("1.4.7") == f"{DOCS_SITE_ROOT}1.4/"


def test_dev_release_of_this_checkout_maps_to_dev() -> None:
    """Guards the case that actually bit: a `.devN` version is not a release of its own series."""
    assert docs_alias_for_version("2.0.1.dev1") == DEV_ALIAS
    assert docs_alias_for_version("2.0.1") == "2.0"


def test_find_source_checkout_finds_this_repository() -> None:
    """The tests import the package from the working tree, which is exactly the dev-install case."""
    assert find_source_checkout() == REPO_ROOT


def test_find_source_checkout_requires_the_right_project(tmp_path: Path) -> None:
    """A checkout of some *other* project that happens to have an mkdocs.yml must not qualify.

       Verified through the same code path rather than by inspection: the guard is a `name =` match
       in pyproject.toml, so a tree with both marker files but a different name has to be rejected."""
    (tmp_path / "mkdocs.yml").write_text("site_name: something else\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "not-codingame-tools"\n',
                                             encoding="utf-8")
    import codingame_tools.docs.site as site_module

    original = site_module.__file__
    try:
        # find_source_checkout() walks up from its own module file, so pointing that at the fake
        # tree is what exercises the rejection.
        site_module.__file__ = str(tmp_path / "codingame_tools" / "docs" / "site.py")
        assert site_module.find_source_checkout() is None
    finally:
        site_module.__file__ = original


def test_base_path_comes_from_site_url() -> None:
    """A published sub-path must be reproduced locally, or every internal link 404s."""
    assert base_path({"site_url": "https://host/codingame-tools/"}) == "/codingame-tools/"
    assert base_path({"site_url": "https://host/codingame-tools"}) == "/codingame-tools/"
    assert base_path({"site_url": "https://host/"}) == "/"
    assert base_path({}) == "/"


def test_site_dir_defaults_and_honours_config(tmp_path: Path) -> None:
    assert site_dir(tmp_path, {}) == tmp_path / "site"
    assert site_dir(tmp_path, {"site_dir": "elsewhere"}) == tmp_path / "elsewhere"


def test_no_rebuild_without_a_build_is_an_error_not_an_empty_window(tmp_path: Path) -> None:
    """Opening a browser onto a site that was never built is the worst available outcome."""
    (tmp_path / "mkdocs.yml").write_text("site_name: x\nsite_url: https://host/x/\n",
                                         encoding="utf-8")
    with pytest.raises(LocalDocsError) as excinfo:
        start_local_docs(tmp_path, rebuild=False)
    assert "gen-docs" in str(excinfo.value)


def test_this_repository_matches_the_configured_site_url() -> None:
    """Ties the published root this command sends users to back to the site's own config."""
    from codingame_tools.docs.local import read_config

    config = read_config(REPO_ROOT)
    assert config["site_url"] == DOCS_SITE_ROOT
