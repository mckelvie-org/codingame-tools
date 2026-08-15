"""Tests for the release-time README link rewriting.

`README.md` has to serve two audiences from one file. GitHub resolves relative links; PyPI, which
renders the same file as the project's front page, resolves them against `pypi.org` and 404s. So
`bin/cut-rc`/`bin/cut-prod` rewrite them to absolute, tag-pinned URLs in the throwaway worktree they
build the release commit in -- leaving `main` with ordinary relative links.

That means the rewriting is only ever exercised during a release, when getting it wrong produces a
published page full of dead links and no way to fix it without cutting another version. Hence
testing it directly.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from rewrite_readme_links import (  # noqa: E402  (needs the path fix above)
    pin_docs_version,
    pin_docs_version_in_file,
    rewrite_links,
)

REPO = "mckelvie-org/codingame-tools"
REF = "v1.2.3"


def _rewrite(text: str, root: Path | None = None) -> str:
    return rewrite_links(text, REPO, REF, root if root is not None else REPO_ROOT)


def test_relative_file_link_becomes_a_pinned_blob_url() -> None:
    assert _rewrite("[docs](doc/index.md)") == (
        f"[docs](https://github.com/{REPO}/blob/{REF}/doc/index.md)")


def test_anchor_is_preserved() -> None:
    """Losing the fragment would silently land the reader at the top of a long page."""
    assert _rewrite("[cmp](doc/design/final-newlines.md#output-comparison)") == (
        f"[cmp](https://github.com/{REPO}/blob/{REF}/doc/design/final-newlines.md#output-comparison)")


def test_directories_use_tree_not_blob(tmp_path: Path) -> None:
    """GitHub doesn't redirect between the two, so a directory linked as `blob` is a 404."""
    (tmp_path / "doc").mkdir()
    assert _rewrite("[dir](doc)", root=tmp_path) == f"[dir](https://github.com/{REPO}/tree/{REF}/doc)"


def test_images_use_raw_urls() -> None:
    """A blob URL serves GitHub's HTML chrome, not the image bytes--PyPI would render a broken
       image."""
    assert _rewrite("![logo](doc/logo.png)") == (
        f"![logo](https://raw.githubusercontent.com/{REPO}/{REF}/doc/logo.png)")


@pytest.mark.parametrize("link", [
    "[pypi](https://pypi.org/project/codingame-tools/)",
    "[mail](mailto:dev@mckelvie.org)",
    "[section](#highlights)",
    "[proto](//example.com/x)",
])
def test_already_resolvable_targets_are_untouched(link: str) -> None:
    assert _rewrite(link) == link


def test_leading_dot_slash_is_normalized() -> None:
    assert _rewrite("[x](./doc/index.md)") == (
        f"[x](https://github.com/{REPO}/blob/{REF}/doc/index.md)")


def test_link_titles_survive() -> None:
    assert _rewrite('[x](doc/index.md "The docs")') == (
        f'[x](https://github.com/{REPO}/blob/{REF}/doc/index.md "The docs")')


def test_reference_style_definitions_are_rewritten() -> None:
    assert _rewrite("[docs]: doc/index.md") == (
        f"[docs]: https://github.com/{REPO}/blob/{REF}/doc/index.md")


def test_fenced_code_is_left_alone() -> None:
    """A fence can legitimately contain something that looks like a link--rewriting an example
       would corrupt it, and the reader can't tell it was us."""
    text = "```markdown\n[docs](doc/index.md)\n```\n[real](doc/index.md)\n"
    result = _rewrite(text)
    assert "```markdown\n[docs](doc/index.md)\n```" in result
    assert f"[real](https://github.com/{REPO}/blob/{REF}/doc/index.md)" in result


def test_badge_links_are_rewritten() -> None:
    """A badge is an image nested in a link, `[![alt](img)](target)`, and needs its own handling.

       The plain inline pattern matches the inner image first and leaves the outer `](target)` with
       no `[...]` in front of it, so it doesn't match at all -- a License badge pointing at a
       relative `LICENSE` would sail through the rewrite untouched and 404 on PyPI. Every badge in
       this project's own README happens to link somewhere absolute, so nothing here would have
       caught it; a stock template README ships exactly that dead link."""
    assert _rewrite("[![License: MIT](https://img.shields.io/badge/x.svg)](LICENSE)") == (
        f"[![License: MIT](https://img.shields.io/badge/x.svg)]"
        f"(https://github.com/{REPO}/blob/{REF}/LICENSE)")

    # A locally-hosted badge image needs the raw URL, and the link still needs the blob URL.
    assert _rewrite("[![build](docs/badge.svg)](CONTRIBUTING.md)") == (
        f"[![build](https://raw.githubusercontent.com/{REPO}/{REF}/docs/badge.svg)]"
        f"(https://github.com/{REPO}/blob/{REF}/CONTRIBUTING.md)")


def test_already_pinned_links_are_not_repointed() -> None:
    """An absolute URL is left alone even when it points at *this* repo at a different ref.

       That's correct in isolation, and it's the reason `bin/cut-prod` restores README.md from the
       commit the rc was cut from before rewriting. Promotion builds on the rc commit, whose README
       is already rewritten and pinned to the rc tag; rewriting it again does nothing, and 1.0.0
       shipped with every "docs for this version" link pointing at v1.0.0-rc.2.

       Deliberately not "solved" by re-pointing any URL that mentions this repo: the README also
       carries a link to the moving `prod-latest` tag that must *not* be pinned. Regenerating from
       the pristine source is unambiguous; pattern-matching URLs would not be."""
    for pinned in (
                f"[docs](https://github.com/{REPO}/blob/v0.9.0/doc/index.md)",
                f"[latest](https://github.com/{REPO}/blob/prod-latest/doc/index.md)",
                f"[dev](https://github.com/{REPO}/blob/main/doc/index.md)",
            ):
        assert _rewrite(pinned) == pinned


def test_the_readme_documents_this_version_not_a_moving_one() -> None:
    """Every documentation link in the README must point at the docs for the copy being read.

       On `main` that is the `dev` site; frozen into a release it must become that release's own
       `X.Y`. Getting this wrong is invisible in review and only shows up on a published page: a
       2.0.0 PyPI page linking at `dev` documents unreleased work, and one linking at `latest`
       documents whatever shipped after it.

       **Must pass from a release commit as well as from `main`,** which is the subtlety that
       matters here: CI checks out the *tag*, and `bin/cut-rc` has already rewritten that README, so
       its links are already pinned. Rewriting them again is correctly a no-op, so asserting on this
       test's own fake `REF` holds only on `main`. An earlier version of this test asserted exactly
       that and failed every release candidate's publish -- after tagging and pushing, which is the
       expensive place to find out. Hence accepting either state."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    site_links = re.findall(r"https://[^/\s)]+/codingame-tools/([^/\s)]+)/", readme)
    assert site_links, "README links at no documentation site at all"

    # On main every one of them is `dev`; on an already-rewritten release commit, every one is the
    # series. Either way they must all agree -- a mix means one link was missed by the rewriter.
    assert len(set(site_links)) == 1, f"documentation links disagree about version: {set(site_links)}"
    assert site_links[0] == "dev" or re.fullmatch(r"\d+\.\d+", site_links[0]), \
        f"documentation links point at an unexpected alias: {site_links[0]!r}"


def test_a_release_pins_every_documentation_link() -> None:
    """Cutting a release must leave no moving alias behind, `dev` or `latest`."""
    pinned = pin_docs_version((REPO_ROOT / "README.md").read_text(encoding="utf-8"), "v2.0.1")
    assert "/codingame-tools/dev/" not in pinned
    assert "/codingame-tools/latest/" not in pinned
    assert "/codingame-tools/2.0/" in pinned


def test_the_guides_are_linked_as_rendered_pages_not_raw_markdown() -> None:
    """The site renders these same files; linking the raw Markdown instead sends readers to a worse
       copy of the page they wanted -- no search, and no links into the API reference."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    raw = re.findall(r"\]\((doc/[^)]+\.md[^)]*)\)", readme)
    assert raw == [], f"README links raw Markdown guides instead of the site: {raw}"


def test_rewriting_is_idempotent() -> None:
    """A re-run (a retried `cut-rc --force`, say) must not double-rewrite into a URL containing
       another URL."""
    once = _rewrite("[docs](doc/index.md)")
    assert _rewrite(once) == once


def test_the_real_readme_has_no_relative_links_left_after_rewriting() -> None:
    """End to end on the actual file: after a release rewrite, nothing relative may survive, or
       PyPI gets a dead link."""
    rewritten = _rewrite((REPO_ROOT / "README.md").read_text(encoding="utf-8"))

    fence = None
    for line in rewritten.splitlines():
        marker = re.match(r"^\s*(```|~~~)", line)
        if marker:
            fence = None if fence == marker.group(1) else (fence or marker.group(1))
            continue
        if fence is not None:
            continue
        for target in re.findall(r"!?\[[^\]]*\]\(([^)\s]+)", line):
            assert target.startswith(("https://", "http://", "#")), (
                f"relative link survived rewriting, would 404 on PyPI: {target}")


def test_the_real_readme_links_resolve_before_rewriting() -> None:
    """The other side of the same coin: the relative links have to be correct in the repo, or the
       rewrite faithfully produces absolute URLs to files that don't exist."""
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    missing = [
        target for target in re.findall(r"!?\[[^\]]*\]\(([^)\s]+)\)", text)
        if not target.startswith(("https://", "http://", "#", "mailto:"))
        and not (REPO_ROOT / target.partition("#")[0]).exists()
    ]
    assert not missing, f"README links to files that don't exist: {missing}"


# --- documentation-site version pinning ----------------------------------------------------------


def test_docs_site_links_are_pinned_to_the_release_series() -> None:
    """The site's `latest` alias follows the newest release, which is right for the README you browse
       on GitHub and wrong for the copy PyPI freezes with a release: a 2.0.0 project page linking at
       `latest` would send readers to whatever shipped afterwards."""
    text = "[docs](https://mckelvie-org.github.io/codingame-tools/latest/api/)"

    assert pin_docs_version(text, "v2.0.1") == \
        "[docs](https://mckelvie-org.github.io/codingame-tools/2.0/api/)"


def test_a_non_release_ref_leaves_latest_alone() -> None:
    """An rc publishes no `X.Y` alias (see .github/workflows/docs.yml), so there is nothing to pin
       to--and a branch name is not a version at all."""
    text = "[docs](https://mckelvie-org.github.io/codingame-tools/latest/)"

    for ref in ("v2.0.0-rc.1", "main", "prod-latest"):
        assert pin_docs_version(text, ref) == text, ref


def test_pinning_leaves_unrelated_urls_untouched() -> None:
    """`latest` appears in plenty of URLs that are not the docs site."""
    text = ("[a](https://github.com/o/r/releases/latest) "
            "[b](https://mckelvie-org.github.io/codingame-tools/latest/)")

    out = pin_docs_version(text, "v3.1.4")

    assert "github.com/o/r/releases/latest" in out
    assert "codingame-tools/3.1/" in out


def test_pypi_sidebar_documents_this_version_too() -> None:
    """`[project.urls] Documentation` is the "Documentation" link in PyPI's sidebar.

       It has the same job as the README's links and the same failure mode -- it used to point at
       `blob/main/doc/index.md`, i.e. raw Markdown on GitHub for whatever main says today, from the
       page of a version released months earlier."""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^Documentation = "([^"]+)"', pyproject, re.MULTILINE)
    assert match, "pyproject.toml declares no Documentation URL"
    url = match.group(1)
    assert "github.io" in url, f"Documentation URL is not the rendered site: {url}"
    assert not url.endswith(".md"), f"Documentation URL points at raw Markdown: {url}"

    # Same alias the README uses: both describe the copy of the project they ship with.
    readme_aliases = set(re.findall(r"github\.io/codingame-tools/([^/\s)]+)/",
                                    (REPO_ROOT / "README.md").read_text(encoding="utf-8")))
    assert re.search(r"github\.io/codingame-tools/([^/\s\"]+)/", url).group(1) in readme_aliases


def test_a_release_pins_the_pypi_sidebar_link(tmp_path: Path) -> None:
    """The release scripts pin pyproject.toml as well as README.md; verify the mechanism they call.

       An rc is deliberately left alone: it ships the `dev` code, so `dev` docs are the honest
       target, and its own series is not published yet."""
    target = tmp_path / "pyproject.toml"
    target.write_text((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"), encoding="utf-8")

    assert pin_docs_version_in_file(target, "v2.0.1") is True
    assert "/codingame-tools/2.0/" in target.read_text(encoding="utf-8")
    assert "/codingame-tools/dev/" not in target.read_text(encoding="utf-8")

    target.write_text((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"), encoding="utf-8")
    assert pin_docs_version_in_file(target, "v2.0.1-rc.1") is False
    assert "/codingame-tools/dev/" in target.read_text(encoding="utf-8")


def test_both_release_scripts_pin_the_project_urls() -> None:
    """Guard against the two release paths drifting apart -- cut-prod is the one that reaches PyPI,
       and it would be easy to add this to cut-rc alone and never notice."""
    for script in ("bin/cut-rc", "bin/cut-prod"):
        text = (REPO_ROOT / script).read_text(encoding="utf-8")
        assert "--pin-docs" in text, f"{script} does not pin pyproject.toml's documentation URL"
        assert "pyproject.toml" in text
