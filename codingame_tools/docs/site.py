"""Working out *which* documentation this installation should show.

The published site keeps every release side by side -- `/2.0/`, `/latest/`, `/dev/` -- so the docs a
reader wants are the ones matching the `cg` they are actually running, not whatever is newest. That
is the whole job of this module: map the installed version onto the right published directory, and
notice the one case where there is something better to show than the published site at all.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..version import __version__

DOCS_SITE_ROOT = "https://mckelvie-org.github.io/codingame-tools/"
"""Root of the published site. Deploy layout lives in .github/workflows/docs.yml."""

DEV_ALIAS = "dev"
"""Where `main` is published -- and so where a pre-release build's docs live."""

LATEST_ALIAS = "latest"
"""Follows the newest release. The fallback when a version cannot be parsed at all."""

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?(.*)$")


def docs_alias_for_version(version: str) -> str:
    """The published directory whose docs describe `version`.

       Releases map to their minor series, matching how `mike` aliases them: `2.0.1` and `2.0.7`
       both document as `2.0`, because patch releases do not change the API surface by definition,
       and a directory per patch would make the version selector unusable within months.

       Anything with a suffix -- `2.1.0.dev1`, `2.1.0rc1`, a local `+` build -- is not a release. It
       was built from `main` after the last tag, so `dev` describes it and its own series does not
       exist yet. Getting this backwards would send a pre-release user to docs for a version that
       has not shipped.

       An unparseable version falls back to `latest`, which is wrong only in the mildest way: it
       shows current docs rather than none."""
    match = _VERSION_RE.match(version.strip())
    if match is None:
        return LATEST_ALIAS
    major, minor, _patch, suffix = match.groups()
    if suffix:
        return DEV_ALIAS
    return f"{major}.{minor}"


def published_docs_url(version: str | None = None) -> str:
    """The URL of the published docs for `version`, defaulting to the running installation."""
    alias = docs_alias_for_version(__version__ if version is None else version)
    return f"{DOCS_SITE_ROOT}{alias}/"


def find_source_checkout() -> Path | None:
    """The repository root, when `cg` is running from a source checkout rather than an install.

       An editable install leaves `codingame_tools/` inside the working tree, so the giveaway is
       simply what sits beside it. Both markers are required, and `pyproject.toml` is read to
       confirm it names *this* project: a checkout of something else that merely vendored the
       package would otherwise be mistaken for one, and we would try to serve its docs.

       Returns None for an ordinary install, which is the common case and not an error."""
    root = Path(__file__).resolve().parent.parent.parent
    pyproject = root / "pyproject.toml"
    if not (root / "mkdocs.yml").is_file() or not pyproject.is_file():
        return None
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return None
    if not re.search(r'^\s*name\s*=\s*"codingame-tools"', text, re.MULTILINE):
        return None
    return root
