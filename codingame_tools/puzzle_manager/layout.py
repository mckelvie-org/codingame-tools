"""Filename/directory-name constants for a puzzle working directory's on-disk layout--shared
   across `codingame_tools.puzzle_manager` submodules. Deliberately not shared with
   `codingame_tools.contribution_manager.layout`, even though a couple of names coincide (e.g.
   `SOLUTION_FILE_NAME`)--the two packages solve unrelated problems (authoring a contribution vs.
   solving an existing puzzle) and are kept fully independent rather than cross-coupled just to
   save duplicating a few string constants.

   Layout:

       puzzle/
           puzzle.json                 # CgPuzzleIdentity--stable, git-tracked
           solution.<ext>              # convenience symlink -> data/solution.src
           .gitignore                  # contains ".meta/"
           .meta/                      # gitignored--see META_SUBDIR_NAME
               puzzle-server-data.json # CgPuzzleServerData--cache, rebuilt by repair()
               statement.html          # read-only reference, regenerated each import_()/repair()
               stub_generator.cgstub   # read-only reference, regenerated each import_()/repair()
               tests/                  # downloaded test case input/output--see
                                        # codingame_tools.puzzle_manager.test_cases_dir
           data/
               solution.src            # the one real, editable/submittable file
               puzzle-data.json        # CgPuzzleData--user-editable, git-tracked
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "DATA_SUBDIR_NAME",
    "META_SUBDIR_NAME",
    "GITIGNORE_FILE_NAME",
    "SOLUTION_FILE_STEM",
    "SOLUTION_FALLBACK_EXTENSION",
    "solution_file_name",
    "find_solution_file",
    "STATEMENT_FILE_NAME",
    "STUB_GENERATOR_FILE_NAME",
]

DATA_SUBDIR_NAME = "data"
"""The puzzle's user-editable content (`solution.src`, `puzzle-data.json`) lives under a `data/`
   subdirectory of the working directory root."""

META_SUBDIR_NAME = ".meta"
"""Container for gitignored, server-derived cache (`puzzle-server-data.json`) and read-only
   reference files (`statement.html`, `stub_generator.cgstub`)--none of it is user-managed state,
   and none of it is expected to survive a fresh git clone into a different repo (see
   `CgPuzzleManager.repair`, which reconstructs it from `puzzle.json`'s stable `puzzle_id`).
   Always paired with a `.gitignore` (see `GITIGNORE_FILE_NAME`) at the working directory root, so
   it's never accidentally tracked by whatever project ends up tracking the rest of the working
   directory."""

GITIGNORE_FILE_NAME = ".gitignore"
"""Written (containing just `.meta/`) at the working directory root by `import_()`/`repair()`, so
   `.meta/`'s contents can never end up tracked by whatever project comes to track the rest of the
   working directory, now or later."""

SOLUTION_FILE_STEM = "solution"
"""Stem of the one real, editable/submittable solution file, which lives in `data/`."""

SOLUTION_FALLBACK_EXTENSION = "src"
"""Extension used when the solution language isn't known, or maps to no extension cg recognizes.

   Deliberately not `.txt`: editors that infer syntax highlighting from a shebang line (VS Code
   among them) only bother for extensions they don't already recognize as plain text, so `.txt`
   would force no highlighting where `.src` lets the shebang win."""


def solution_file_name(extension: str | None) -> str:
    """`solution.<ext>` for a known language extension, else `solution.src`.

       The file carries the language's real extension rather than a fixed one because every tool
       that reads it--language servers, debuggers, the compiler--dispatches on the extension. cg
       previously kept `data/solution.src` fixed and maintained a `solution.<ext>` symlink beside
       it, which cost a day of debugging: the debug info named one path, the editor resolved the
       other, and breakpoints silently failed to bind. One real file with the right name has no
       such gap, and needs no symlink support from the filesystem (which Windows only grants with
       developer mode enabled)."""
    return f"{SOLUTION_FILE_STEM}.{extension or SOLUTION_FALLBACK_EXTENSION}"


def find_solution_file(data_dir: Path, extension: str | None = None) -> Path | None:
    """The existing solution file in `data_dir`, whatever extension it currently carries.

       Callers generally know the language and so know the name, but not always: a working
       directory whose language changed out from under it, or one written by an older cg that used
       a fixed `solution.src`, still has to be found. The expected name wins when present, so a
       stray leftover can never shadow the real file; otherwise a lone `solution.*` is accepted.

       Returns None if there is no solution file, or if several exist with no way to choose--the
       caller decides whether that's an error or a thing to repair."""
    if extension is not None:
        expected = data_dir / solution_file_name(extension)
        if expected.is_file():
            return expected
    candidates = sorted(p for p in data_dir.glob(f"{SOLUTION_FILE_STEM}.*") if p.is_file())
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return None
    # Ambiguous: prefer the fallback name if it is one of them, since that is what an older cg
    # wrote and what a migration is most likely looking at.
    fallback = data_dir / solution_file_name(None)
    return fallback if fallback in candidates else None

STATEMENT_FILE_NAME = "statement.html"
"""Read-only reference copy of the puzzle's rendered problem statement (see
   `CgTestSessionQuestionDetails.statement`), under `.meta/`--not user-managed state, so it
   doesn't belong in `data/`; regenerated on every `import_()`/`repair()`, never read back or
   diffed; purely for the solver's own convenience (e.g. to reread the problem without a network
   round trip)."""

STUB_GENERATOR_FILE_NAME = "stub_generator.cgstub"
"""Read-only reference copy of the puzzle's stub-generation script (see
   `CgTestSessionQuestionDetails.stub_generator`), under `.meta/`--informational only; this
   package doesn't interpret the stub-generator DSL to produce a real starter `solution.src`,
   unlike `codingame_tools.contribution_manager`'s Python-only trivial stub for *authoring* a new
   contribution (see `CgPuzzleManager.import_`'s docstring)."""
