"""How API reference modules are labelled in the navigation sidebar.

Its own module so it can be tested: scripts/gen_api_pages.py runs inside the docs build and writes
files on import, so it cannot be imported from a test.
"""

from __future__ import annotations


def nav_label(module: str, prefix: str) -> str:
    """The sidebar label for `module`, given the dotted package prefix of the area it belongs to.

       Material's sidebar does not wrap, scroll horizontally, or resize by dragging, so a label
       wider than it is simply lost from that point on. Every module in an area shares a long
       prefix -- `codingame_tools.client.common.protocol.` is 38 characters before anything
       distinguishing -- so full dotted paths rendered as a column of identical entries, cut off
       exactly where they start to differ.

       Stripping the area prefix puts the distinguishing part first, where it survives truncation.
       The bare leaf name would be shorter still and is wrong: `language.registry` and
       `language.toolchain.registry` would both render as `registry`, which is no better than a
       column of identical prefixes. The area's own package module keeps its last segment, having
       nothing left after the prefix is removed."""
    if module.startswith(f"{prefix}."):
        return module[len(prefix) + 1:]
    return module.rsplit(".", 1)[-1]
