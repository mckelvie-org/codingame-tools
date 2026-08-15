"""Tests that keep the *hand-written* docs honest about the CLI.

Every `cg ...` invocation appearing in a hand-written page is resolved against the real parser, and
every relative link between pages is checked to point at a file that exists. Renames are the drift
that actually happens here--`cg puzzle push` became `cg puzzle submit`, `revert` became
`discard-local`, `status --remote` became `--refresh`--and each would have silently invalidated
every guide mentioning it. This can't check that the surrounding advice is still *correct*, but a
command that no longer exists makes a whole page look abandoned.

There is no staleness test for the command reference, and no longer any need for one: it is
generated during the docs build (scripts/gen_cli_pages.py) and never committed, so it cannot lag
behind the parser it describes. It used to be committed, which is exactly what made a staleness
test tempting and its absence dangerous.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_ROOT = REPO_ROOT / "doc"
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gen_cli_docs import iter_pages  # noqa: E402  (needs the path fix above)

REFERENCE_DIR = DOC_ROOT / "cli" / "reference"
"""Where the generated pages appear *in the built site*. Nothing exists here on disk."""


def test_generated_pages_contain_no_terminal_escapes() -> None:
    """Generated pages must be plain text, whatever terminal the generator was run from.

       Python 3.14's argparse colourises help when `sys.stdout` is a terminal, which is right for a
       human running `cg --help` and wrong when `format_help()` is being captured into pages.
       Generating from an interactive shell wrote raw ANSI escapes into every page; generating
       through a pipe produced clean text. The same source, different output, decided by how the
       command happened to be invoked -- and it reached a commit before anyone noticed, because
       every automated run here is piped.

       Run in a subprocess under `FORCE_COLOR`, which is the loudest thing an environment can say,
       so this fails if the generator ever stops pinning colour off. In-process would prove nothing:
       pytest captures stdout, so argparse would decline to colourise regardless."""
    env = dict(os.environ, FORCE_COLOR="1")
    env.pop("PYTHON_COLORS", None)
    env.pop("NO_COLOR", None)
    probe = (
        "import sys; sys.path.insert(0, 'scripts')\n"
        "from gen_cli_docs import iter_pages\n"
        "bad = [name for name, text in iter_pages() if '\\x1b' in text]\n"
        "print('|'.join(bad))\n"
    )
    result = subprocess.run(
            [sys.executable, "-c", probe],
            check=True, capture_output=True, text=True, env=env, cwd=REPO_ROOT,
        )
    offenders = [name for name in result.stdout.strip().split("|") if name]
    assert not offenders, f"ANSI escapes leaked into generated pages: {offenders[:5]}"


# `cg ...` inside a fenced block or inline code. Stops at anything that ends a command: a pipe,
# redirect, comment, or the end of the line.
_INVOCATION_RE = re.compile(r"(?<![\w`])cg((?: +[a-z0-9][a-z0-9-]*)+)")

# Words that follow `cg` in prose but aren't commands (placeholders, or a group named mid-sentence).
_PLACEHOLDERS = {"command", "options", "subcommand"}


def _hand_written_pages() -> list[Path]:
    """Every page under doc/ -- all of which are hand-written now that the reference is virtual."""
    return sorted(DOC_ROOT.rglob("*.md"))


def _command_paths(parser: argparse.ArgumentParser, path: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    paths = {path}
    for action in parser._actions:  # noqa: SLF001  (argparse exposes no public traversal API)
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            for name, sub in action.choices.items():
                paths |= _command_paths(sub, (*path, name))
    return paths


@pytest.fixture(scope="module")
def known_commands() -> set[tuple[str, ...]]:
    from codingame_tools.cli.main import CgCli

    original_argv0 = sys.argv[0]
    sys.argv[0] = "cg"
    try:
        cli = CgCli(["--help"])
        asyncio.run(cli.init_parser())
        return _command_paths(cli.parser)
    finally:
        sys.argv[0] = original_argv0


def test_documented_commands_all_exist(known_commands: set[tuple[str, ...]]) -> None:
    """Every `cg ...` written by hand in `doc/` resolves to a real command."""
    unknown: list[str] = []
    for page in _hand_written_pages():
        for match in _INVOCATION_RE.finditer(page.read_text(encoding="utf-8")):
            words = tuple(shlex.split(match.group(1)))
            # Trim trailing words until we reach a real command: everything after it is arguments
            # (`cg puzzle import temperatures`), which we deliberately don't validate.
            candidate = words
            while candidate and candidate not in known_commands:
                candidate = candidate[:-1]
            if not candidate and words and words[0] not in _PLACEHOLDERS:
                unknown.append(f"{page.relative_to(REPO_ROOT)}: cg {' '.join(words)}")
    assert not unknown, "documented commands that don't exist:\n  " + "\n  ".join(unknown)


def test_the_linter_would_catch_a_rename(known_commands: set[tuple[str, ...]]) -> None:
    """Guard the guard: a real past rename must not resolve.

       `cg puzzle push` was renamed to `cg puzzle submit`. If this ever passes, the matcher has gone
       slack (e.g. by trimming down to a bare group) and the check above is no longer protecting
       anything."""
    assert ("puzzle", "submit") in known_commands
    assert ("puzzle", "push") not in known_commands


_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def _markdown_pages() -> list[Path]:
    """Every markdown file that's part of the docs, including the root-level ones."""
    pages = list(DOC_ROOT.rglob("*.md"))
    pages += [REPO_ROOT / name for name in ("README.md", "CONTRIBUTING.md")]
    return sorted(p for p in pages if p.is_file())


@pytest.fixture(scope="module")
def generated_reference_pages() -> set[str]:
    """The page paths the docs build will create under `cli/reference/`, e.g. `api/vote.md`."""
    return {name for name, _content in iter_pages()}


def test_relative_doc_links_resolve(generated_reference_pages: set[str]) -> None:
    """Every relative link between docs points at a page that will exist.

       Cheap, and it's the other half of the rename problem: the command linter catches a `cg ...`
       that no longer exists, this catches a page that no longer exists. Both are the kind of rot
       that makes documentation look unmaintained long before anyone notices it's wrong.

       Links into `cli/reference/` are resolved against what the generator *will* produce rather
       than against the filesystem, because nothing is written there until the site is built. The
       alternative -- skipping them -- would leave the most-linked area of the docs unchecked here,
       and those links outnumber the rest."""
    broken: list[str] = []
    for page in _markdown_pages():
        for target in _LINK_RE.findall(page.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path, _, _anchor = target.partition("#")
            if not path:
                continue  # a bare anchor, into this same page
            resolved = (page.parent / path).resolve()
            if resolved.is_relative_to(REFERENCE_DIR):
                if resolved.relative_to(REFERENCE_DIR).as_posix() not in generated_reference_pages:
                    broken.append(f"{page.relative_to(REPO_ROOT)} -> {target} (not generated)")
            elif not resolved.exists():
                broken.append(f"{page.relative_to(REPO_ROOT)} -> {target}")
    assert not broken, "broken relative links:\n  " + "\n  ".join(broken)


def test_the_generated_reference_is_not_committed() -> None:
    """The whole point of the change: no copy on disk that can drift from the parser.

       A regenerated file reappearing in the working tree means something still writes there, and a
       stale copy would then shadow the generated pages in the build."""
    assert not REFERENCE_DIR.exists(), \
        f"{REFERENCE_DIR.relative_to(REPO_ROOT)} exists on disk; it is generated at build time"
