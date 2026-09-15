"""Seeding a new puzzle's solution from a template of your own.

A puzzle you have never attempted starts from a placeholder: one `TODO` comment. Most people begin
every solution the same way regardless -- the same imports, the same input-reading scaffold, the
same helpers -- and retype or paste it each time. A template file replaces that placeholder.

The template is ordinary source in your own language, with one substitution: `${PUZZLE_DETAILS}`
expands to a plain-text rendering of the puzzle -- its goal, input and output descriptions,
constraints, and the first test case as a worked example -- so the problem is in front of you in
the file you are editing rather than in a browser tab.

That rendering is meant to be pasted inside a block comment, so text that would *end* one is
defused first: a statement containing `*/` would otherwise close a C comment in the middle of the
puzzle description and leave the rest as code.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .statement_render import CgStatementBlock

__all__ = [
    "PUZZLE_DETAILS_TOKEN",
    "CG_URL_TOKEN",
    "puzzle_ide_url",
    "CgTemplateError",
    "comment_safe",
    "render_puzzle_details",
    "expand_template",
    "resolve_template",
    "parse_search_path",
]

PUZZLE_DETAILS_TOKEN = "${PUZZLE_DETAILS}"
"""What a template writes where the rendered puzzle description should go."""

CG_URL_TOKEN = "${CG_URL}"
"""What a template writes where the puzzle's CodinGame IDE URL should go.

   `${...}`, matching `${PUZZLE_DETAILS}` -- one spelling for both, so there is nothing to
   remember."""

_EXAMPLE_HEADERS = frozenset({"example", "examples"})
"""Statement headers whose content is replaced by the real first test case."""

_EXAMPLE_BLOCK_KINDS = frozenset({"example_input", "example_output"})

_QUOTE_RUN_RE = re.compile(r"(\"{3,}|'{3,})")


def comment_safe(text: str) -> str:
    """Defuse sequences that would terminate a block comment, leaving the text otherwise intact.

       The rendering is pasted into a comment whose syntax this code does not know, so the fix is
       to make the text harmless in all of them rather than to escape for one:

         - a run of three or more quotes is shortened to two, which no language treats as a
           delimiter (`\"\"\"` ends a Python docstring; `'''` ends the other kind);
         - `*/` becomes `* /`, which does not close a C-family block comment.

       Idempotent, and applied to the rendered description only -- never to the template itself,
       which is your code and may legitimately contain any of this."""
    return _QUOTE_RUN_RE.sub(lambda m: m.group(0)[0] * 2, text).replace("*/", "* /")


def _example_section(example_input: str | None, example_output: str | None, *,
                     interactive: bool) -> list[str]:
    """The Example section, from the puzzle's first real test case.

       Preferred over the statement's own example: the statement's is prose that may be
       abbreviated or illustrative, while this is the exact input the puzzle will feed you.

       **Except for an interactive puzzle**, where it is not. There the downloaded test case is the
       referee's world configuration, in a format the solution never reads -- the solution is fed a
       turn's state, computes a move, and the referee derives the next turn from it. Printing that
       file under "Input:" would put a worked example in front of the author that their program
       will never see, which is worse than printing nothing."""
    if not example_input:
        return []
    if interactive:
        return ["Example", "",
                "This puzzle is interactive: your program trades moves with a referee turn by "
                "turn, so there is no fixed input to show. The stored test case is the referee's "
                "starting configuration, not what your program reads. See the Game Input section "
                "above for the per-turn format."]
    lines = ["Example", "", "Input:", example_input.rstrip("\n")]
    if example_output and example_output.strip():
        lines += ["", "Output:", example_output.rstrip("\n")]
    else:
        # An optimization puzzle is scored by a referee rather than compared against an answer, so
        # its stored output file is empty. Saying so beats an "Output:" heading with nothing after.
        lines += ["", "Output: scored by a referee; there is no fixed expected output."]
    return lines


def render_puzzle_details(blocks: list[CgStatementBlock], *, title: str | None = None,
                          example_input: str | None = None,
                          example_output: str | None = None,
                          interactive: bool = False) -> str:
    """Render a puzzle as plain text suitable for pasting into a block comment.

       Sections come from the parsed statement in the order the puzzle states them -- typically
       Goal, Input, Output, Constraints -- followed by an Example built from the first test case.
       The statement's own Example section is dropped in favour of that one, so the description
       does not carry two conflicting examples.

    Args:
        blocks:         Parsed statement, from `parse_statement_html`.
        title:          Puzzle title, used as the first line when given.
        example_input:  First test case's input.
        example_output: Its expected output. Empty for an optimization puzzle.
        interactive:    Whether the puzzle is played turn by turn against a referee, in which case
                        the stored test case is not the input the solution reads.

    Returns:
        Plain text with no trailing newline, safe to place inside a block comment.
    """
    lines: list[str] = []
    if title:
        lines += [title, "=" * len(title), ""]

    skipping_example = False
    for block in blocks:
        if block.kind == "header":
            skipping_example = block.text.strip().casefold() in _EXAMPLE_HEADERS
            if skipping_example:
                continue
            if lines and lines[-1] != "":
                lines.append("")
            lines += [block.text, ""]
        elif block.kind in _EXAMPLE_BLOCK_KINDS or skipping_example:
            continue
        elif block.text.strip():
            lines.append(block.text)

    example = _example_section(example_input, example_output, interactive=interactive)
    if example:
        if lines and lines[-1] != "":
            lines.append("")
        lines += example

    rendered = "\n".join(lines).strip("\n")
    # Collapse the blank-line runs the section joins can leave behind.
    rendered = re.sub(r"\n{3,}", "\n\n", rendered)
    return comment_safe(rendered)


def puzzle_ide_url(puzzle_pretty_id: str) -> str:
    """The CodinGame IDE URL for a puzzle, for `${CG_URL}`.

       Built from the client's own base URL rather than a second copy of the host, so the two
       cannot drift."""
    from ..client.common.raw_client import CgRawClient

    return f"{CgRawClient.CODINGAME_BASE_URL}/ide/puzzle/{puzzle_pretty_id}"


def expand_template(template_text: str, details: str, *,
                    variables: dict[str, str] | None = None) -> str:
    """Substitute the rendered puzzle description, and any single-line variables, into a template.

       Only the tokens named here are substituted -- deliberately not `string.Template` or
       `format`, which would also try to expand `$` and `{}` that are part of your code.

       `variables` holds one-line values such as `${CG_URL}`; they are substituted first, on the
       template alone, so a `${...}` that happens to occur inside the rendered puzzle text is left
       exactly as the statement wrote it.

       Each occurrence is indented to match where it appears, so a token sitting inside an indented
       block comment produces an indented description rather than text jammed against the margin.
       A template with no token is used verbatim, which is a legitimate way to seed boilerplate
       without the puzzle text.
    """
    for token, value in (variables or {}).items():
        template_text = template_text.replace(token, value)
    if PUZZLE_DETAILS_TOKEN not in template_text:
        return template_text
    out: list[str] = []
    for line in template_text.split("\n"):
        if PUZZLE_DETAILS_TOKEN not in line:
            out.append(line)
            continue
        before = line[:line.index(PUZZLE_DETAILS_TOKEN)]
        indent = before if not before.strip() else ""
        replacement = details.split("\n")
        if indent:
            replacement = [indent + r if r else r for r in replacement]
            out.extend(replacement)
        else:
            # The token shares its line with real content: substitute in place and let the first
            # line follow it, rather than moving the caller's text around.
            rest = line[line.index(PUZZLE_DETAILS_TOKEN) + len(PUZZLE_DETAILS_TOKEN):]
            joined = details.split("\n")
            out.append(before + (joined[0] if joined else "") + (rest if len(joined) == 1 else ""))
            if len(joined) > 1:
                out.extend(joined[1:-1])
                out.append(joined[-1] + rest)
    return "\n".join(out)


class CgTemplateError(Exception):
    """A template was asked for by name and could not be found."""


def parse_search_path(values: list[str] | None) -> list[Path]:
    """Turn `--template-path` values into directories, in search order.

       Each value may itself hold several directories separated by the platform's path separator,
       the way `PATH` does, and the option may also be repeated; both are accepted so neither
       habit is wrong."""
    directories: list[Path] = []
    for value in values or []:
        for part in value.split(os.pathsep):
            if part.strip():
                directories.append(Path(part).expanduser())
    return directories


def _has_path_separator(name: str) -> bool:
    return "/" in name or (os.altsep is not None and os.altsep in name) or os.sep in name


def resolve_template(template: str | None, search_path: list[Path], *,
                     extension: str | None) -> Path | None:
    """Find the template file to seed a solution from, or None if there is none to use.

       Three ways in, in order:

       1. `--template` holding a path separator -- used as given, relative to the current
          directory. Not searched for: a path is a path.
       2. `--template` holding a bare filename -- looked up in `search_path`, first match winning.
       3. no `--template` -- `solution.<extension>` looked up in `search_path`, so a directory of
          per-language templates works with no flag at all.

       The two are treated differently when nothing is found, on purpose: naming a template that
       does not exist is a mistake worth stopping for, while the default lookup finding nothing
       just means this language has no template yet.

    Args:
        template:    `--template`, if given.
        search_path: Directories from `--template-path`, in order.
        extension:   The solution language's file extension, for the default lookup.

    Returns:
        The template file, or None to fall back to the built-in placeholder.

    Raises:
        CgTemplateError: if `template` was given and no such file exists.
    """
    if template is not None:
        if _has_path_separator(template):
            candidate = Path(template).expanduser()
            if candidate.is_file():
                return candidate
            raise CgTemplateError(f"template file {template!r} does not exist.")
        for directory in search_path:
            candidate = directory / template
            if candidate.is_file():
                return candidate
        if not search_path:
            raise CgTemplateError(
                    f"template {template!r} is a bare filename and no --template-path was given, "
                    "so there is nowhere to look for it. Pass a path to the file, or a search "
                    "path to find it in.")
        searched = ", ".join(str(d) for d in search_path)
        raise CgTemplateError(f"template {template!r} was not found in the search path: {searched}")

    if extension is None:
        return None
    for directory in search_path:
        candidate = directory / f"solution.{extension}"
        if candidate.is_file():
            return candidate
    return None
