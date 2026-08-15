"""Tests for the API reference's navigation labels.

The sidebar is the only way to move between 101 generated module pages, and Material renders it
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


LANGUAGE = "codingame_tools.language"


@pytest.mark.parametrize(("module", "prefix", "expected"), [
    (f"{PROTOCOL}.achievement", PROTOCOL, "achievement"),
    (f"{PROTOCOL}.clash_of_code_description", PROTOCOL, "clash_of_code_description"),
    (f"{PROTOCOL}.typedefs", PROTOCOL, "typedefs"),
    (f"{SERVICES}.services.contribution", SERVICES, "services.contribution"),
    # The area's own package module has nothing left after the prefix; it keeps its last segment.
    (PROTOCOL, PROTOCOL, "protocol"),
])
def test_nav_label_is_relative_to_its_area(module: str, prefix: str, expected: str) -> None:
    assert nav_label(module, prefix) == expected


def test_labels_never_repeat_the_shared_prefix() -> None:
    """The actual defect: a label starting with the shared prefix is truncated to exactly that."""
    label = nav_label(f"{PROTOCOL}.achievement", PROTOCOL)
    assert not label.startswith("codingame_tools")
    assert len(label) < 30, "longer than the sidebar shows, so it will be cut off again"


def test_sibling_modules_stay_distinguishable() -> None:
    """Two modules in the language area are named `registry`, so the bare leaf name is not an
       option -- it would trade one column of identical labels for another.

       This used to be argued from the 18 protocol modules all named `schema`. Those were flattened
       into their parent packages (`protocol/vote/schema.py` -> `protocol/vote.py`), which is why
       the case is now made with the collision that actually remains: the *reason* for keeping a
       path-relative label has to be a live one, or the next person deletes it as needless."""
    labels = {
        nav_label(module, LANGUAGE)
        for module in (f"{LANGUAGE}.registry", f"{LANGUAGE}.toolchain.registry")
    }
    assert len(labels) == 2, "bare leaf names would render both of these as `registry`"
    assert all(not lab.startswith("codingame_tools") for lab in labels)


def test_labels_differ_within_the_first_visible_characters() -> None:
    """Distinguishable *as rendered*, not merely distinct as strings: the sidebar shows roughly the
       first 29 characters, so labels must diverge before then.

       `test_session` and `test_session_question_submission` are the pair that make this real: they
       share 12 characters, and before flattening carried a `.schema` suffix that pushed the second
       to 39 characters."""
    visible = 29
    labels = [
        nav_label(f"{PROTOCOL}.{module}", PROTOCOL)
        for module in ("achievement", "clash_of_code", "clash_of_code_description", "codingamer",
                       "codingamer_puzzle_topic", "contribution", "puzzle", "test_session",
                       "test_session_question_submission", "user", "vote")
    ]
    prefixes = [lab[:visible] for lab in labels]
    assert len(set(prefixes)) == len(prefixes), f"indistinguishable when truncated: {prefixes}"
