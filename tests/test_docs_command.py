"""Tests for what `cg doc` decides to show.

The decision is version-sensitive and mostly unobservable: pick the wrong published directory and
the command still opens a window full of plausible documentation, just for a different release than
the one installed. Nothing downstream would notice, so the mapping is pinned here directly.

The `mike` deploy layout these assertions encode lives in `.github/workflows/docs.yml`; if that
changes, these tests are the place it has to be changed too.
"""

from __future__ import annotations

import asyncio
import sys
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
from codingame_tools.docs.local import (
    base_path,
    docs_cache_dir,
    site_dir,
    start_local_docs,
)
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
        start_local_docs(tmp_path, mode="existing")
    assert "gen-docs" in str(excinfo.value)


def test_this_repository_matches_the_configured_site_url() -> None:
    """Ties the published root this command sends users to back to the site's own config."""
    from codingame_tools.docs.local import read_config

    config = read_config(REPO_ROOT)
    assert config["site_url"] == DOCS_SITE_ROOT


def _minimal_site(root: Path) -> None:
    """A one-page site, so the contract below can be tested without a 7-second real build."""
    (root / "doc").mkdir()
    (root / "doc" / "index.md").write_text("# Hello\n", encoding="utf-8")
    # `theme: material` because the default theme ships as a separate package this project does not
    # install; the theme is irrelevant to what these tests assert.
    (root / "mkdocs.yml").write_text(
        "site_name: probe\nsite_url: https://host/probe/\ndocs_dir: doc\nsite_dir: site\n"
        "theme:\n  name: material\n",
        encoding="utf-8")


def test_build_then_read_serves_the_same_bytes(tmp_path: Path) -> None:
    """`cg doc` then `cg doc --no-rebuild` must show the same thing.

       That holds because both are handed the same output directory. An earlier version ran the
       live-reloading dev server for the first, which serves from its own memory and writes nothing:
       `cg doc` worked, and `cg doc --no-rebuild` straight afterwards served a stale build, or failed
       outright because there was none. Nobody expects those to differ."""
    pytest.importorskip("properdocs", reason="the docs toolchain is in the `docs` dependency group")
    _minimal_site(tmp_path)

    out = docs_cache_dir(tmp_path)
    server = start_local_docs(tmp_path, mode="build", output=out)
    server.stop()
    built = out / "index.html"
    assert built.is_file(), "build mode did not populate the output directory"
    assert not (tmp_path / "site").exists(), "cg doc's build wrote into the checkout"

    # `existing` must now succeed against exactly what `build` left, byte for byte.
    first = built.read_bytes()
    server = start_local_docs(tmp_path, mode="existing", output=out)
    server.stop()
    assert built.read_bytes() == first


def test_watch_mode_leaves_site_dir_alone(tmp_path: Path) -> None:
    """The counterpart: `bin/docs` live-reloads and must not scribble a half-built site into the
       tree, which is why it is not what `cg doc` uses."""
    pytest.importorskip("properdocs", reason="the docs toolchain is in the `docs` dependency group")
    _minimal_site(tmp_path)

    server = start_local_docs(tmp_path, mode="watch")
    try:
        asyncio.run(server.wait_until_ready())
    finally:
        server.stop()
    assert not (tmp_path / "site").exists(), "watch mode wrote into site_dir"


@pytest.mark.parametrize(("argv", "expected_mode"), [
    (["doc"], "build"),
    (["doc", "--no-rebuild"], "existing"),
])
def test_cg_doc_picks_the_mode_that_leaves_a_reusable_build(
        argv: list[str], expected_mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """`cg doc` must use `build`, not `watch`.

       Asserted at the CLI level because that is where the choice lives: the mode is one word in
       `cmd_doc`, and changing it to `watch` breaks the guarantee that `cg doc` followed by
       `cg doc --no-rebuild` shows the same thing -- while every lower-level test still passes."""
    import codingame_tools.cli.main  # noqa: F401  (the package __init__ shadows the name)

    cli = sys.modules["codingame_tools.cli.main"]
    seen: dict[str, object] = {}

    class _StubServer:
        url = "http://127.0.0.1:1/probe/"
        using_cache = False
        output = None

        async def wait_until_ready(self) -> None:
            return None

        def stop(self) -> None:
            return None

    def fake_start(root: Path, **kwargs: object) -> _StubServer:
        seen["mode"] = kwargs.get("mode")
        seen["output"] = kwargs.get("output")
        return _StubServer()

    async def fake_window(url: str, **kwargs: object) -> None:
        seen["url"] = url

    monkeypatch.setattr(cli, "start_local_docs", fake_start)
    monkeypatch.setattr(cli, "open_window_and_wait", fake_window)
    monkeypatch.setattr(cli, "find_source_checkout", lambda: REPO_ROOT)

    assert cli.main(argv) == 0
    assert seen["mode"] == expected_mode
    # And into the cache, not the checkout -- for both modes, or --no-rebuild would read a
    # directory nothing writes.
    assert seen["output"] == docs_cache_dir(REPO_ROOT)


def test_cg_doc_never_writes_into_the_checkout(tmp_path: Path) -> None:
    """`cg doc` builds into the per-user cache even when the checkout is perfectly writable.

       The rule is about which *command* it is, not about permissions: `cg doc` is package
       functionality a user runs from anywhere, so it treats the checkout as read-only on principle.
       An earlier version keyed this on a permission bit, which meant the same command wrote into a
       source tree or not depending on the filesystem -- surprising, and untestable from the outside."""
    # A subdirectory, not tmp_path itself: the autouse fake-global-root fixture puts the redirected
    # cache under tmp_path too, so a checkout of tmp_path would contain it and make this vacuous.
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    resolved = docs_cache_dir(checkout)
    assert checkout not in resolved.parents
    assert resolved != site_dir(checkout, {})
    assert "docs" in resolved.parts


def test_contributor_tools_still_use_the_checkout(tmp_path: Path) -> None:
    """The other half of the split: `bin/gen-docs` and `bin/docs -q` share the checkout's site_dir,
       which is also what `mike` deploys from, so those three cannot disagree."""
    assert site_dir(tmp_path, {}) == tmp_path / "site"
    assert site_dir(tmp_path, {"site_dir": "elsewhere"}) == tmp_path / "elsewhere"


def test_two_checkouts_do_not_share_a_cache_entry(tmp_path: Path) -> None:
    """Otherwise two checkouts would overwrite each other's docs, and `cg doc` would serve whichever
       built last -- silently, and for the wrong tree.

       Deliberately two checkouts with the *same directory name* in different places, which is the
       case that actually happens (a scratch clone beside a working one) and the only one that
       proves the path is in the key. An earlier version of this test used differently-named
       directories and passed even with a constant key, since the readable name prefix alone kept
       them apart."""
    first = tmp_path / "a" / "codingame-tools"
    second = tmp_path / "b" / "codingame-tools"
    for root in (first, second):
        root.mkdir(parents=True)
    assert first.name == second.name
    assert docs_cache_dir(first) != docs_cache_dir(second)
