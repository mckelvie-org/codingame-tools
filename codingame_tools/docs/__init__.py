"""Finding and displaying this installation's documentation.

Backs `cg doc`, which opens the docs matching the `cg` you are running -- the published site's
directory for your version, or, in a source checkout, that tree's own docs instead.
"""

from __future__ import annotations

from .browser import open_window_and_wait
from .local import (
    LocalDocsError,
    LocalDocsMode,
    LocalDocsServer,
    docs_cache_dir,
    start_local_docs,
    wait_until_serving,
)
from .site import (
    DEV_ALIAS,
    DOCS_SITE_ROOT,
    LATEST_ALIAS,
    docs_alias_for_version,
    find_source_checkout,
    published_docs_url,
)

__all__ = [
    "DEV_ALIAS",
    "DOCS_SITE_ROOT",
    "LATEST_ALIAS",
    "LocalDocsError",
    "LocalDocsMode",
    "LocalDocsServer",
    "docs_alias_for_version",
    "docs_cache_dir",
    "find_source_checkout",
    "open_window_and_wait",
    "published_docs_url",
    "start_local_docs",
    "wait_until_serving",
]
