"""The puzzle working directory's three manifest files--see
   `codingame_tools.puzzle_manager.manager`'s module docstring for the full rationale behind
   the three-way split (git-tracked stable identity vs. gitignored cache vs. git-tracked
   user-editable content):

   - `puzzle.json` (`CgPuzzleIdentity`, at the working directory root): stable identity, never
     changes for the life of the directory, safe to commit.
   - `.meta/puzzle-server-data.json` (`CgPuzzleServerData`): cache, gitignored--lost whenever the
     working directory is committed to git and cloned elsewhere (see
     `codingame_tools.puzzle_manager.layout.META_SUBDIR_NAME`), reconstructed by `repair()`.
   - `data/puzzle-data.json` (`CgPuzzleData`): the one piece of user-editable metadata that
     travels with a solution submission (currently just `solution_language`)--safe to commit,
     alongside `data/solution.src` itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..client.common.protocol.typedefs import CgSolutionLanguage
from ..common.dataclass_wizard_x import CatchAll, JSONWizardX

__all__ = [
    "PUZZLE_IDENTITY_FILE_NAME",
    "PUZZLE_SCHEMA_VERSION",
    "CgPuzzleIdentity",
    "CgPuzzleServerData",
    "CgPuzzleData",
]

PUZZLE_IDENTITY_FILE_NAME = "puzzle.json"
"""Name of the puzzle working directory's identity/manifest file, at its root (a sibling of
   `data/`/`.meta/`)--its presence is what identifies a directory as a puzzle working directory
   at all."""

PUZZLE_SCHEMA_VERSION = 1
"""Current on-disk format version for a puzzle working directory, recorded in
   `CgPuzzleIdentity.schema_version` so a future format change can detect and offer to migrate an
   older working directory."""


@dataclass
class CgPuzzleIdentity(JSONWizardX):
    """The `puzzle.json` manifest: this working directory's stable identity, written once by
       `import_()` and never changed afterward. Deliberately the *only* thing this package
       considers safe to treat as permanent, git-trackable truth about which puzzle this is--see
       the module docstring, and `codingame_tools.puzzle_manager.manager`'s, for why
       `test_session_handle`/`title`/`puzzle_pretty_id` are cache (`.meta/`) instead, not
       identity."""

    schema_version: int
    """The on-disk format version this working directory was written in--see
       `PUZZLE_SCHEMA_VERSION`."""

    puzzle_id: int
    """Numeric ID of the puzzle (`CgTestSessionPuzzle.id`)--the actual repair root key: the only
       confirmed API that can regenerate everything else from scratch,
       `Puzzle/findProgressByIds`, takes this, not `puzzle_handle` (no known API accepts the
       opaque handle as a lookup key) or `puzzle_pretty_id` (not trusted as stable--see
       `CgPuzzleServerData.puzzle_pretty_id`)."""

    puzzle_handle: str
    """Opaque handle for the puzzle (`CgTestSessionPuzzle.handle`). Recorded here as part of this
       working directory's permanent identity even though nothing can look a puzzle up *by* it
       today--`puzzle_id` is what `repair()` actually queries with."""

    extra_data: CatchAll = field(default_factory=dict)


@dataclass
class CgPuzzleServerData(JSONWizardX):
    """The `.meta/puzzle-server-data.json` manifest: cached, gitignored, re-derivable-from-
       `puzzle_id` server state. Rebuilt by `repair()` whenever missing (e.g. after a fresh clone
       into a different repo, or manual deletion/corruption)."""

    test_session_handle: str
    """This codingamer's test session handle for the puzzle (see
       `CgPuzzleService.generate_session_from_puzzle_pretty_id`). Freely cached and reused
       indefinitely, unlike `puzzle_pretty_id`/`title` below--confirmed (2026-07-30, per repeated
       identical results from `generateSessionFromPuzzlePrettyId`) to be a per-user singleton with
       affinity to the *puzzle*, not to whichever `pretty_id` happened to be used to generate it;
       there is no known scenario where a cached handle here would need re-verification the way a
       cached `puzzle_pretty_id` does."""

    title: str
    """Display title of the puzzle. Purely informational (e.g. for `cg puzzle where` output)."""

    puzzle_pretty_id: str
    """The puzzle's pretty ID/slug at the time this was last (re)written--**informational only,
       never trusted as ground truth.** Unlike `test_session_handle`, a pretty ID is *not*
       confirmed stable (it plausibly changes if the puzzle's title changes, and even a
       structurally-valid pretty ID string could in principle end up reassigned to a different
       puzzle over time)--so this cached copy is never fed back into an API call (e.g.
       `generateSessionFromPuzzlePrettyId`) by this package. Whenever a pretty ID is actually
       needed operationally (only `repair()` ever needs one, and only if `findProgressByIds`
       didn't already hand back a reusable `test_session_handle` directly), it's re-derived fresh
       from `Puzzle/findProgressByIds(puzzle_id)` and cross-checked against `puzzle_id` first--see
       `CgPuzzleManager.repair`."""

    extra_data: CatchAll = field(default_factory=dict)

    puzzle_type: str | None = None
    """The puzzle's contribution type (e.g. "PUZZLE_INOUT"--currently the only type this package
       supports at all, so always that value in practice today), as of when this was last (re)
       written. Purely informational, same caching spirit as `title`/`puzzle_pretty_id`. `None`
       for a cache file written before this field existed--not re-backfilled automatically; run
       `cg puzzle repair` (after deleting `.meta/`) to populate it."""

    difficulty: str | None = None
    """The puzzle's difficulty level (`CgTestSessionPuzzle.level`/`CgLastActivityPuzzle.level`,
       e.g. "easy", "medium", "hard"), as of when this was last (re)written. Purely informational,
       same caching spirit as `title`/`puzzle_pretty_id`. `None` for a cache file written before
       this field existed--same backfill note as `puzzle_type`."""


@dataclass
class CgPuzzleSelectedTest(JSONWizardX):
    """`.meta/selected-test.json`: which single test case the debugger should run against.

       Debugging needs exactly one test, because a debug session gets one stdin. This used to be a
       `pickString` dropdown baked into `launch.json`, which meant `launch.json` had to be
       regenerated for every working directory and every re-import--the list of options *is*
       per-directory state. Recording the choice here instead makes the launch configuration static:
       one per language, workspace-wide.

       Absent means "no explicit choice", and callers fall back to the first downloaded test rather
       than failing--so debugging works immediately after an import, with no selection step.

       Gitignored and disposable like the rest of `.meta/`: "which test am I focused on right now"
       is exactly the sort of thing that shouldn't survive a fresh clone. `repair()` leaves it
       alone (it only ever rewrites specific files and wipes `.meta/tests/`), so a repair doesn't
       silently reset your focus."""

    test_index: int
    """1-based index of the selected test case, matching `CgPuzzleDownloadedTestCase.index`."""

    extra_data: CatchAll = field(default_factory=dict)


@dataclass
class CgPuzzleSolutionSnapshot(JSONWizardX):
    """`.meta/solution-snapshot.json`: exactly what this client last wrote into
       `data/solution.src`, and in which language.

       Exists to answer one question without guessing: *has the user edited the solution since we
       wrote it?* The alternative--regenerating the placeholder and comparing--would silently break
       the moment placeholder generation stopped being byte-identical, which it is not guaranteed to
       be across releases (a template tweak, or a generated timestamp, would be enough). An
       untouched working directory would then start claiming it had unsaved changes.

       Gitignored cache like the rest of `.meta/`, and deliberately fail-safe: if it's missing (a
       fresh clone, or a directory imported by a version that predates it) the caller falls back to
       comparing against the server, which errs toward *refusing* to discard rather than toward
       discarding silently."""

    solution_language: CgSolutionLanguage
    """The language `code` was written for. A snapshot whose language no longer matches
       `CgPuzzleData.solution_language` describes a previous state and must not be trusted."""

    code: str
    """The exact text written to `data/solution.src`."""

    extra_data: CatchAll = field(default_factory=dict)


@dataclass
class CgPuzzleData(JSONWizardX):
    """The `data/puzzle-data.json` manifest: the one piece of metadata that genuinely travels
       with a solution submission (alongside `data/solution.src` itself), as opposed to read-only
       puzzle content (statement, stub generator--see `.meta/`) or server-assigned identity/cache.
       Safe to commit to git, same as `solution.src`."""

    solution_language: CgSolutionLanguage
    """The language `data/solution.src` is currently written in--submitted alongside the code on
       `submit()`/`play()`, and so genuinely part of the user-managed submission state, not read-only
       reference material or server-derived cache."""

    extra_data: CatchAll = field(default_factory=dict)
