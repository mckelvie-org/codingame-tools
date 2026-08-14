#!/usr/bin/env python
"""Rewrite a README's relative links into absolute GitHub URLs pinned to a release ref.

    python scripts/rewrite_readme_links.py README.md mckelvie-org/codingame-tools v1.2.3 [ROOT]

PyPI renders `README.md` as the project's front page but does **not** resolve relative links --
they're resolved against `pypi.org`, so `[docs](doc/index.md)` 404s for anyone who clicks it there.
GitHub does resolve them, so the same file can't satisfy both audiences unchanged.

`bin/cut-rc` and `bin/cut-prod` already build their release commit in a throwaway worktree and patch
`pyproject.toml`/`README.md`/`CHANGELOG.md` there before committing and tagging. This runs in that
same worktree, which is what makes the whole approach work: `main` keeps ordinary relative links
that render correctly on GitHub and can be checked by `tests/test_doc_cli_reference.py`, while the
*tagged release commit* -- the one the sdist and wheel are built from, and therefore the one PyPI
displays -- gets absolute URLs pinned to that exact tag.

Pinned to the tag rather than to a branch on purpose: the README PyPI shows for version 1.2.3 should
link to the docs as they were at 1.2.3, not to whatever `main` says today. A separate, deliberately
unpinned "latest release" link belongs in the README itself, pointing at the moving `prod-latest`
tag (see `.github/workflows/publish.yml`, which force-updates it after a successful publish).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

__all__ = ["rewrite_links", "rewrite_readme_file"]

# `[text](target)` and `![alt](target)`. Targets containing `)` aren't supported and don't occur;
# markdown itself requires them to be escaped or angle-bracketed.
_INLINE_LINK_RE = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+)(\s+\"[^\"]*\")?\)")

# A badge: an image wrapped in a link, `[![alt](img)](target)`. Needs its own pattern and must run
# first, because `_INLINE_LINK_RE` matches the inner image and leaves the outer `](target)` with no
# preceding `[...]` to match against--so a badge pointing at a relative path (a License badge
# linking to `LICENSE`, say) would silently survive the rewrite and 404 on PyPI.
_IMAGE_LINK_RE = re.compile(r"\[!\[([^\]]*)\]\(([^)\s]+)\)\]\(([^)\s]+)\)")

# Reference-style definitions: `[label]: target` at the start of a line.
_REFERENCE_DEF_RE = re.compile(r"^(\[[^\]]+\]:\s*)(\S+)(.*)$")

_FENCE_RE = re.compile(r"^\s*(```|~~~)")

# Targets that are already resolvable from anywhere, or that aren't paths at all.
_ABSOLUTE_PREFIXES = ("http://", "https://", "//", "mailto:", "tel:", "ftp://")


def _is_relative_path(target: str) -> bool:
    if not target or target.startswith("#"):
        return False  # a bare anchor into this same page
    if target.startswith("<"):
        return False  # angle-bracketed autolink
    return not target.lower().startswith(_ABSOLUTE_PREFIXES)


def _absolute_url(target: str, repo: str, ref: str, root: Path, *, is_image: bool) -> str:
    """One relative target -> its absolute GitHub URL, preserving any `#anchor`."""
    path, sep, anchor = target.partition("#")
    clean = path.lstrip("./")
    if is_image:
        # Blob URLs render images inside GitHub's chrome; PyPI needs the bytes themselves.
        return f"https://raw.githubusercontent.com/{repo}/{ref}/{clean}"
    # A directory needs `tree`, a file needs `blob`; GitHub does not redirect between them.
    kind = "tree" if (root / clean).is_dir() else "blob"
    return f"https://github.com/{repo}/{kind}/{ref}/{clean}{sep}{anchor}"


def rewrite_links(text: str, repo: str, ref: str, root: Path) -> str:
    """Return `text` with every relative link and image made absolute against `repo` at `ref`.

       Content inside fenced code blocks is left alone--a fence can legitimately contain something
       that looks like a link, and rewriting an example would corrupt it."""
    out: list[str] = []
    fence: str | None = None
    for line in text.splitlines(keepends=True):
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
            out.append(line)
            continue
        if fence is not None:
            out.append(line)
            continue

        def replace_image_link(match: re.Match[str]) -> str:
            alt, image_target, link_target = match.groups()
            if _is_relative_path(image_target):
                image_target = _absolute_url(image_target, repo, ref, root, is_image=True)
            if _is_relative_path(link_target):
                link_target = _absolute_url(link_target, repo, ref, root, is_image=False)
            return f"[![{alt}]({image_target})]({link_target})"

        line = _IMAGE_LINK_RE.sub(replace_image_link, line)

        def replace_inline(match: re.Match[str]) -> str:
            bang, text_part, target, title = match.groups()
            if not _is_relative_path(target):
                return match.group(0)
            url = _absolute_url(target, repo, ref, root, is_image=bang == "!")
            return f"{bang}[{text_part}]({url}{title or ''})"

        line = _INLINE_LINK_RE.sub(replace_inline, line)

        def replace_reference(match: re.Match[str]) -> str:
            label, target, rest = match.groups()
            if not _is_relative_path(target):
                return match.group(0)
            return f"{label}{_absolute_url(target, repo, ref, root, is_image=False)}{rest}"

        line = _REFERENCE_DEF_RE.sub(replace_reference, line)
        out.append(line)
    return "".join(out)



_DOCS_LATEST_RE = re.compile(r"(https://[^/\s)]+/[^/\s)]+/)latest/")
"""A link into the published documentation site's `latest` alias.

   The site is versioned (see `.github/workflows/docs.yml`): `latest` follows the newest release,
   which is right for the README you browse on GitHub but wrong for the copy PyPI freezes with a
   release. A 2.0.0 project page linking at `latest` sends readers to whatever shipped since."""


def pin_docs_version(text: str, ref: str) -> str:
    """Repoint documentation-site links from `latest` to the series this release belongs to.

       `v2.0.1` -> `2.0`, matching the alias `mike` publishes. Left alone for a ref that isn't a
       release tag (an rc, a branch), since there is no published series to pin to."""
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.\d+", ref)
    if match is None:
        return text
    series = f"{match.group(1)}.{match.group(2)}"
    return _DOCS_LATEST_RE.sub(rf"\g<1>{series}/", text)


def rewrite_readme_file(readme_path: Path, repo: str, ref: str, root: Path | None = None) -> int:
    """Rewrite `readme_path` in place. Returns how many links changed."""
    root = root if root is not None else readme_path.parent
    original = readme_path.read_text(encoding="utf-8")
    updated = pin_docs_version(rewrite_links(original, repo, ref, root), ref)
    if updated != original:
        readme_path.write_text(updated, encoding="utf-8")
    # Rewriting is line-wise, so the line count is invariant; strict= asserts that rather than
    # silently truncating the count if it ever stops being true.
    changed = sum(1 for a, b in zip(original.splitlines(), updated.splitlines(), strict=True) if a != b)
    return changed


def main() -> None:
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    readme_path = Path(sys.argv[1])
    repo, ref = sys.argv[2], sys.argv[3]
    root = Path(sys.argv[4]) if len(sys.argv) > 4 else readme_path.parent
    changed = rewrite_readme_file(readme_path, repo, ref, root)
    print(f"rewrote relative links in {readme_path} on {changed} line(s) -> {repo}@{ref}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
