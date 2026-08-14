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
from typing import Any

MIKE_VERSION_VAR = "MIKE_DOCS_VERSION"
"""Set by `mike` to the version being deployed -- `dev`, or a release series like `2.0`."""


def _version_label() -> str:
    """What to show as the version, preferring the identity the site is published under.

       During a `mike` deploy that is the alias being written (`dev`, `2.0`), which is what a reader
       navigates by and what the version selector lists -- not the exact package version, which for
       a `dev` build is a snapshot number nobody can navigate to. Everywhere else -- a local build,
       the CI strict build -- there is no alias, so the package version is both accurate and the
       most specific thing available."""
    alias = os.environ.get(MIKE_VERSION_VAR, "").strip()
    if alias:
        return alias
    try:
        from codingame_tools import __version__
    except ImportError:  # pragma: no cover -- the site is always built against the package
        return ""
    return __version__


def on_config(config: Any) -> Any:
    """Append the version to `site_name`, which is what Material renders in the header and title."""
    label = _version_label()
    site_name = config["site_name"]
    # `mkdocs serve` re-runs this on every reload against a re-read config, but guard anyway: a
    # title that grows a version each time you save a file would be a memorable bug.
    if label and not site_name.endswith(label):
        config["site_name"] = f"{site_name} {label}"
    return config
