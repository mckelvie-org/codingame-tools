"""Tests for the API reference's navigation labels.

The sidebar is the only way to move between 126 generated module pages, and Material renders it
without wrapping, horizontal scrolling, or a drag handle. A label wider than the sidebar is not
merely awkward -- everything past the cutoff is invisible, so entries sharing a long prefix become
literally indistinguishable. Measured before the fix: every protocol page showed
`codingame_tools.client.common` and nothing more.

That failure is invisible to every other check. The build succeeds, the links resolve, the pages are
correct; only a human looking at the rendered sidebar can see it, which is exactly why it is pinned
here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from api_nav import nav_label  # noqa: E402  (needs the path fix above)

PROTOCOL = "codingame_tools.client.common.protocol"
SERVICES = "codingame_tools.client.service"


@pytest.mark.parametrize(("module", "prefix", "expected"), [
    (f"{PROTOCOL}.achievement.schema", PROTOCOL, "achievement.schema"),
    (f"{PROTOCOL}.clash_of_code_description.schema", PROTOCOL, "clash_of_code_description.schema"),
    (f"{PROTOCOL}.schema", PROTOCOL, "schema"),
    (f"{SERVICES}.services.contribution", SERVICES, "services.contribution"),
    # The area's own package module has nothing left after the prefix; it keeps its last segment.
    (PROTOCOL, PROTOCOL, "protocol"),
])
def test_nav_label_is_relative_to_its_area(module: str, prefix: str, expected: str) -> None:
    assert nav_label(module, prefix) == expected


def test_labels_never_repeat_the_shared_prefix() -> None:
    """The actual defect: a label starting with the shared prefix is truncated to exactly that."""
    label = nav_label(f"{PROTOCOL}.achievement.schema", PROTOCOL)
    assert not label.startswith("codingame_tools")
    assert len(label) < 30, "longer than the sidebar shows, so it will be cut off again"


def test_sibling_modules_stay_distinguishable() -> None:
    """18 modules in this package are named `schema`, so the bare leaf name is not an option --
       it would trade one column of identical labels for another."""
    labels = {
        nav_label(f"{PROTOCOL}.{pkg}.schema", PROTOCOL)
        for pkg in ("achievement", "clash_of_code", "clash_of_code_description", "codingamer")
    }
    assert len(labels) == 4
    assert all(not lab.startswith("codingame_tools") for lab in labels)


def test_labels_differ_within_the_first_visible_characters() -> None:
    """Distinguishable *as rendered*, not merely distinct as strings: the sidebar shows roughly the
       first 29 characters, so labels must diverge before then."""
    visible = 29
    labels = [
        nav_label(f"{PROTOCOL}.{pkg}.schema", PROTOCOL)
        for pkg in ("achievement", "clash_of_code", "clash_of_code_description", "codingamer",
                    "contribution", "puzzle", "test_session", "user", "vote")
    ]
    prefixes = [lab[:visible] for lab in labels]
    assert len(set(prefixes)) == len(prefixes), f"indistinguishable when truncated: {prefixes}"
