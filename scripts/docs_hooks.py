"""Build-time hooks for the documentation site, wired in via `hooks:` in mkdocs.yml.

Puts the version into the site title, so a page carries its own version wherever it is read. That
matters more here than on a single-version site: the published docs keep every release side by side
under `/2.0/`, `/latest/` and `/dev/` (see .github/workflows/docs.yml), and a page torn out of that
context -- a bookmark, a search hit, a link someone pasted a year ago -- otherwise looks identical
whichever version it came from. Material's version selector already says which is current, but only
while you are looking at the chrome; the title travels with the tab and the browser history.
"""

from __future__ import annotations

import os
import re
from typing import Any

MIKE_VERSION_VAR = "MIKE_DOCS_VERSION"
"""Set by `mike` to the version being deployed -- `dev`, or a release series like `2.0`."""

RELEASE_SERIES = re.compile(r"\d+\.\d+")
"""A deploy identifier that is a minor series (`2.0`) rather than a name like `dev`."""

FINAL_RELEASE = re.compile(r"\d+\.\d+\.\d+")
"""A package version that is an actual release -- not `2.0.1.dev1` or an rc."""


def _package_version() -> str:
    try:
        from codingame_tools import __version__
    except ImportError:  # pragma: no cover -- the site is always built against the package
        return ""
    return __version__


def _version_label() -> str:
    """What to show as the version.

       Releases show the exact version they were built from (`2.0.3`) even though they are published
       under the minor series (`/2.0/`). The series is deliberately reused by every patch release --
       see .github/workflows/docs.yml for why -- so the URL cannot answer "which patch am I
       reading"; the title is the only place that can. Labelling both `2.0` would leave a reader
       unable to tell 2.0.0's docs from 2.0.3's, which is the question the version in the title
       exists to answer.

       `dev` keeps the alias instead. There the package version is a snapshot number
       (`2.0.1.dev1`) that names no published thing and that nobody can navigate to, which is worse
       than the name the version selector already lists.

       The exact version is used only when it is a real release *belonging to the series being
       deployed*. Anything else -- a mismatch, a dev or rc version -- means the build is not what
       this alias claims, so the alias stays: a title that disagrees with its own URL is worse than
       one that is merely imprecise. Everywhere else (a local build, the CI strict build) there is
       no alias at all, and the package version is both accurate and the most specific available."""
    alias = os.environ.get(MIKE_VERSION_VAR, "").strip()
    if not alias:
        return _package_version()
    package = _package_version()
    if (RELEASE_SERIES.fullmatch(alias) and FINAL_RELEASE.fullmatch(package)
            and package.startswith(f"{alias}.")):
        return package
    return alias


def on_config(config: Any) -> Any:
    """Append the version to `site_name`, which is what Material renders in the header and title."""
    label = _version_label()
    site_name = config["site_name"]
    # `mkdocs serve` re-runs this on every reload against a re-read config, but guard anyway: a
    # title that grows a version each time you save a file would be a memorable bug.
    if label and not site_name.endswith(label):
        config["site_name"] = f"{site_name} {label}"
    return config
