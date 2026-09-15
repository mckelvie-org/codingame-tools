"""Render a puzzle's cached HTML statement (`.meta/statement.html`, see `codingame_tools.
   puzzle_manager.manager.CgPuzzleManager.statement_file`) as an ordered list of plain-text
   display blocks, for `cg puzzle description`.

   Purpose-built for CodinGame's specific puzzle-statement HTML structure (observed live,
   2026-08-01)--not a general HTML-to-text converter:

       <div class="statement-section statement-goal">
         <h2><span class="icon icon-goal">&nbsp;</span><span>Goal</span></h2>
         <span class="question-statement">paragraph text, <br><br> = paragraph break, inline
           <strong>/<var> emphasis ignored (just their text kept)</span>
       </div>
       <div class="statement-section statement-protocol">
         <div class="blk">
           <div class="title">Input</div>
           <div class="question-statement-input">description, <br> = single line break</div>
         </div>
         <div class="blk">...Output...</div>
         <div class="blk">...Constraints...</div>
         <div class="blk">
           <div class="title">Example</div>
           <div class="statement-inout">
             <div class="statement-inout-in">
               <div class="title">Input</div>
               <pre class="question-statement-example-in">literal test input</pre>
             </div>
             <div class="statement-inout-out">
               <div class="title">Output</div>
               <pre class="question-statement-example-out">literal expected output</pre>
             </div>
           </div>
         </div>
       </div>

   Every `<div class="title">` (at any nesting depth) becomes a "header" block--this handles the
   top-level Input/Output/Constraints/Example titles and the nested Example Input/Output titles
   uniformly, with no special-casing needed. `<br>` is rendered as a literal newline, letting the
   source's own single-vs-double-`<br>` choices (line break vs. paragraph break) carry through
   unchanged--confirmed live to reproduce the intended structure (e.g. "Wanted elements"/"Wanted
   needle" reading as isolated sub-headings purely because they're `<br><br>`-surrounded within
   the single Goal paragraph, not because `<strong>` is treated specially--it isn't)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

__all__ = ["CgStatementBlock", "parse_statement_html"]

_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})

_WHITESPACE_RUN_RE = re.compile(r"[ \t]+")


@dataclass(frozen=True)
class CgStatementBlock:
    """A single rendered block of a puzzle's HTML statement--see `parse_statement_html`."""

    kind: str
    """One of "header" (a section title, e.g. "Goal"/"Input"/"Output"/"Constraints"/"Example"),
       "text" (ordinary paragraph/description text--may itself contain internal blank lines,
       e.g. the Goal section's multiple paragraphs), "example_input", or "example_output" (the
       literal, whitespace-preserved example test-case text under an "Example" section)."""

    text: str
    """The block's rendered plain text (HTML tags stripped, entities unescaped)."""


def _normalize_header(raw: str) -> str:
    return " ".join(raw.replace("\xa0", " ").split())


def _normalize_text(raw: str) -> str:
    """Collapse horizontal whitespace runs and strip each line, collapse runs of multiple blank
       lines down to at most one (a paragraph break), and trim leading/trailing blank lines."""
    lines = [_WHITESPACE_RUN_RE.sub(" ", line.replace("\xa0", " ")).strip() for line in raw.split("\n")]
    normalized: list[str] = []
    blank_run = False
    for line in lines:
        if line:
            normalized.append(line)
            blank_run = False
        elif not blank_run:
            normalized.append("")
            blank_run = True
    return "\n".join(normalized).strip("\n")


def _classify(tag: str, classes: list[str]) -> str | None:
    """Which `CgStatementBlock.kind` (if any) `tag`/`classes` opens a new active block for--see
       the module docstring for the HTML shape each of these corresponds to."""
    if tag == "h2" or (tag == "div" and classes == ["title"]):
        return "header"
    if (tag == "span" and "question-statement" in classes) \
            or (tag == "div" and any(c.startswith("question-statement-") for c in classes)):
        return "text"
    # A second markup flavour, used by at least some official puzzles ("Onboarding"): sections are
    # `statement-<section>-content` rather than `question-statement-<section>`. Without this the
    # parser emitted the section headers and dropped every word of the body -- `cg puzzle
    # description` printed nothing but "The Goal" and "Rules".
    if tag == "div" and any(c.startswith("statement-")
                            and (c.endswith("-content") or c.endswith("-text")) for c in classes):
        return "text"
    # The protocol block: `<div class="blk"><div class="text">...`, which is where this flavour puts
    # the input/output description -- the part a solver most needs and the part that was missing.
    if tag == "div" and classes == ["text"]:
        return "text"
    if tag == "pre" and "question-statement-example-in" in classes:
        return "example_input"
    if tag == "pre" and "question-statement-example-out" in classes:
        return "example_output"
    return None


class _StatementHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[CgStatementBlock] = []
        self._depth = 0
        self._buffer: list[str] = []
        # (kind, depth the active element opened at)--matched back against self._depth on the
        # corresponding close tag to know exactly when to flush, regardless of what's nested
        # inside (arbitrary wrapper divs/inline tags never confuse this, since only the
        # depth-matching close tag triggers a flush).
        self._active: tuple[str, int] | None = None

    def _flush(self) -> None:
        if self._active is not None:
            kind, _ = self._active
            raw = "".join(self._buffer)
            if kind == "header":
                text = _normalize_header(raw)
            elif kind in ("example_input", "example_output"):
                text = raw.strip("\n")
            else:
                text = _normalize_text(raw)
            if text:
                self.blocks.append(CgStatementBlock(kind=kind, text=text))
        self._buffer = []
        self._active = None

    def _start(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            if self._active is not None:
                self._buffer.append("\n")
            return
        if self._active is not None:
            return  # nested inline tag inside an already-active block--just flows through
        classes = (dict(attrs).get("class") or "").split()
        kind = _classify(tag, classes)
        if kind is not None:
            self._active = (kind, self._depth)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs)
        if tag not in _VOID_TAGS:
            self._depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # self-closing form (e.g. "<br/>")--never adjusts depth, matching handle_starttag's
        # void-tag handling (a plain "<br>" never gets a matching handle_endtag call at all).
        self._start(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return
        self._depth -= 1
        if self._active is not None and self._active[1] == self._depth:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._active is not None:
            self._buffer.append(data)


def parse_statement_html(html: str) -> list[CgStatementBlock]:
    """Parse a puzzle's cached HTML statement into an ordered list of display blocks--see
       `CgStatementBlock` and the module docstring."""
    parser = _StatementHTMLParser()
    parser.feed(html)
    parser.close()
    return parser.blocks
