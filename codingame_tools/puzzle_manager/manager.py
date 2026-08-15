"""`CgPuzzleManager`: builds a puzzle working directory from an existing server-side puzzle
   (`import_`), runs the working directory's current solution against a single test case
   (`play`), submits it for credit (`submit`), and reconstructs cached/reference state that was
   deliberately never committed to git (`repair`).

   Deliberately much simpler than `codingame_tools.contribution_manager`: exactly one file is
   ever editable--`data/solution.src`--so there is no git repository backing this working
   directory, no branches, no multi-file merge machinery. "Merge reconciliation" here is just a
   two-way choice between the local file and the server's last-submitted version:

   - `diff()` shows a unified text diff between them.
   - `discard_local()` overwrites the local file with the server's version.
   - `submit()` overwrites the server's version with the local file (a normal `TestSession/submit`).
     Note `play()` *also* durably updates the server's copy of the code as a side effect (see its
     docstring)--unlike a contribution, a puzzle working directory has two independent
     server-side persistence phases (the test session's current answer, and a graded
     submission), not one; `submit()` is named for CodinGame's own vocabulary (matching the
     underlying `TestSession/submit` API method) rather than `push()`'s git vocabulary, precisely
     to avoid implying it's the only thing that persists anything server-side.

   There is no third "merge tool" option in this first cut--flagged as a possible follow-up, not
   built, since a single-file external diff/merge tool is easy to add later if actually wanted.

   Unlike a contribution, nothing here is ever newly *created*: a puzzle already exists on the
   server before you can solve it, so `import_()` is the only way a working directory comes into
   being, and `CgPuzzleIdentity` has no `create()`-then-later-linked state to track.

   **Three-way state split (see `codingame_tools.puzzle_manager.schema`/`.layout` for the exact
   files), and why:** a puzzle working directory is expected to be put under the user's own git
   (unlike a contribution working directory, which has its *own*, separate, internal git repo).
   That means anything not explicitly committed is lost the moment the directory is cloned into a
   different repo/machine--so state here is split by how it behaves under that constraint:

   - `puzzle.json` (`CgPuzzleIdentity`, root): the *only* facts treated as permanent identity,
     safe to commit--`puzzle_id` and `puzzle_handle`. Deliberately minimal: `puzzle_id` is the
     real repair root key (the only confirmed API that can regenerate everything else,
     `Puzzle/findProgressByIds`, takes a numeric ID, not a pretty ID or the opaque handle).
   - `.meta/` (`CgPuzzleServerData` + read-only `statement.html`/`stub_generator.cgstub`/`tests/`):
     gitignored cache, reconstructed by `repair()` whenever missing. `test_session_handle` is
     cached and reused freely (confirmed stable, with affinity to the *puzzle*, not to whichever
     pretty ID happened to generate it). `title`/`puzzle_pretty_id` are cached too, but purely for
     display--never trusted as ground truth or fed back into an API call, since (unlike the
     handle) a pretty ID isn't confirmed stable across e.g. a puzzle title change. See
     `CgPuzzleServerData`'s own docstring. `tests/` (see
     `codingame_tools.puzzle_manager.test_cases_dir`) holds each test case's downloaded
     input/output, one directory per server-assigned test index--reference material for running
     the solution locally (e.g. in a debugger), not something this package interprets itself.
   - `data/puzzle-data.json` (`CgPuzzleData`) + `data/solution.src`: genuinely user-managed,
     git-trackable content--the solution itself, and the one piece of metadata that travels with
     a submission (`solution_language`).
"""

from __future__ import annotations

import dataclasses
import difflib
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..client.client import CgClient
from ..client.common.protocol.last_activities import CgLastActivityPuzzle
from ..client.common.protocol.report import CgSubmissionReport
from ..client.common.protocol.test_session import (
    CgMultipleLanguagesTestParams,
    CgPlayRequest,
    CgPlayResult,
    CgSubmitRequest,
)
from ..client.common.protocol.typedefs import CgSolutionLanguage
from ..client.common.raw_client import CgClientHttpError
from ..common.text_files import file_to_server_text, server_text_to_file
from ..config.resolver import default_global_data_dir
from ..language import (
    DEFAULT_BUILD_TIMEOUT_SECONDS,
    DEFAULT_RUN_TIMEOUT_SECONDS,
    TOOLCHAIN_SUBDIR_NAME,
    CgBuildProfile,
    CgBuildResult,
    CgDebugSession,
    CgLanguageContext,
    CgVsCodeRequest,
    find_workspace_root,
    get_language,
    list_language_cg_ids,
    remove_containers_for_root,
    write_provisioning,
)
from ..test_runner import outputs_match
from .layout import (
    DATA_SUBDIR_NAME,
    GITIGNORE_FILE_NAME,
    META_SUBDIR_NAME,
    SOLUTION_FILE_STEM,
    STATEMENT_FILE_NAME,
    STUB_GENERATOR_FILE_NAME,
    find_solution_file,
    solution_file_name,
)
from .schema import (
    PUZZLE_IDENTITY_FILE_NAME,
    PUZZLE_SCHEMA_VERSION,
    CgPuzzleData,
    CgPuzzleIdentity,
    CgPuzzleSelectedTest,
    CgPuzzleServerData,
    CgPuzzleSolutionSnapshot,
)
from .test_cases_dir import (
    TESTS_SUBDIR_NAME,
    CgPuzzleDownloadedTestCase,
    download_test_cases,
    list_downloaded_test_cases,
)

__all__ = [
    "DATA_SUBDIR_NAME",
    "META_SUBDIR_NAME",
    "SOLUTION_FILE_STEM",
    "find_solution_file",
    "solution_file_name",
    "STATEMENT_FILE_NAME",
    "STUB_GENERATOR_FILE_NAME",
    "TESTS_SUBDIR_NAME",
    "CgPuzzleManagerError",
    "CgPuzzleDiscardResult",
    "CgPuzzleSetLanguageResult",
    "CgPuzzleLocalTestResult",
    "CgPuzzleLocalTestFailedError",
    "CgPuzzleRemoteTestResult",
    "CgPuzzleStatus",
    "CgPuzzleManager",
]

_SUPPORTED_CONTRIBUTION_TYPE = "PUZZLE_INOUT"

_DEFAULT_IMPORT_LANGUAGE: CgSolutionLanguage = "Python3"
"""Language for a placeholder solution when a puzzle has never been attempted and the
   caller didn't ask for a particular one."""

_PUZZLE_DATA_FILE_NAME = "puzzle-data.json"
_PUZZLE_SERVER_DATA_FILE_NAME = "puzzle-server-data.json"
_SOLUTION_SNAPSHOT_FILE_NAME = "solution-snapshot.json"
_SELECTED_TEST_FILE_NAME = "selected-test.json"


class CgPuzzleManagerError(Exception):
    """Raised for puzzle-manager-level errors not better represented by a more specific
       exception (e.g. importing an unsupported puzzle type, discarding local edits when nothing
       has ever been submitted to discard to, or a `repair()` whose fresh lookup didn't actually
       match the puzzle it was supposed to repair)."""


@dataclass(frozen=True)
class CgPuzzleDiscardResult:
    """The outcome of `CgPuzzleManager.discard_local()`."""

    code: str
    """The server's last-submitted code, now also written to `data/solution.src`."""

    solution_language: CgSolutionLanguage
    """The language `code` is written in (the server's last submission may be in a different
       language than `data/puzzle-data.json`'s previously-recorded `solution_language`--this is
       the fresh, now-authoritative value; `discard_local()` updates `puzzle-data.json` to
       match)."""


@dataclass(frozen=True)
class CgPuzzleSetLanguageResult:
    """The outcome of `CgPuzzleManager.set_language()`."""

    language: CgSolutionLanguage
    """The language now recorded in `data/puzzle-data.json`."""

    previous_language: CgSolutionLanguage
    """What it was before."""

    code: str
    """The new contents of `data/solution.src`."""

    from_server: bool
    """True when `code` is the codingamer's real saved work for `language`, restored from the
       server; False when they had never attempted this puzzle in that language and `code` is just
       a generated placeholder. Worth surfacing--the difference is invisible in the file itself,
       and "your old solution is back" and "here's an empty starting point" are very different
       things to be told."""


@dataclass(frozen=True)
class CgPuzzleLocalTestResult:
    """The outcome of running `data/solution.src` against one downloaded `.meta/tests/` test
       case--see `CgPuzzleManager.play_local`."""

    index: int
    """The test case's server-assigned index (see `CgPuzzleDownloadedTestCase.index`)."""

    label: str
    """The test case's real label."""

    passed: bool
    """Whether the run completed without crashing/timing out and its stdout matched the test
       case's expected output (see `codingame_tools.test_runner.outputs_match`)."""

    input: str
    """The test case's input, exactly as fed to the solution's stdin."""

    expected_output: str
    """The test case's expected output (`output.txt`)."""

    actual_output: str
    """What the solution actually wrote to stdout."""

    stderr: str
    """What the solution wrote to stderr (not itself a failure condition, but useful context when
       a test does fail)."""

    timed_out: bool
    """Whether the run was killed for exceeding its timeout rather than running to completion."""


@dataclass(frozen=True)
class CgPuzzleRemoteTestResult:
    """The outcome of playing one of a puzzle's test cases against the server
       (`TestSession/play`)--see `CgPuzzleManager.play`."""

    index: int
    """The test case's 1-based index (see `CgTestSessionTestCase.index`)."""

    label: str
    """The test case's real label, from `.meta/tests/<index>/` if it's been downloaded--a
       generic `f"test {index}"` placeholder otherwise (`play()` doesn't require an index to be
       locally downloaded; the server doesn't need that to run it)."""

    result: CgPlayResult
    """The raw `TestSession/play` response for this test case."""


class CgPuzzleLocalTestFailedError(CgPuzzleManagerError):
    """Raised by `CgPuzzleManager.play_local` if any test case failed. Carries every result (not
       just the failing ones) via `.results`, so a caller can report the full picture."""

    def __init__(self, results: list[CgPuzzleLocalTestResult]) -> None:
        self.results = results
        failed = [r for r in results if not r.passed]
        summary = ", ".join(f"#{r.index} ({r.label})" for r in failed)
        super().__init__(f"{len(failed)}/{len(results)} local test case(s) failed: {summary}")


class CgPuzzleBuildFailedError(CgPuzzleManagerError):
    """Raised by `CgPuzzleManager.play_local` when `build_solution()` failed, so no test case was
       run at all. Carries the full `CgBuildResult` (compiler diagnostics in `.result.output`) via
       `.result`.

       Note `build_solution()` itself does *not* raise this--it returns the result, so a caller
       driving the build directly can display diagnostics however it likes. This exists for the
       batch wrapper, which has no other way to say "nothing ran"."""

    def __init__(self, result: CgBuildResult) -> None:
        self.result = result
        super().__init__(f"solution failed to build:\n{result.output}")


@dataclass(frozen=True)
class CgPuzzleStatus:
    """A point-in-time summary of a puzzle working directory--see `CgPuzzleManager.status()`.
       Much simpler than `codingame_tools.contribution_manager.CgContributionStatus`--no
       versioning, no draft/moderation gate, no sync-state machine--matching this whole package's
       "much simpler than contribution_manager" design (see the module docstring)."""

    puzzle_dir: Path
    """The working directory this status describes."""

    puzzle_id: int
    """Numeric ID of the puzzle (`CgPuzzleIdentity.puzzle_id`)."""

    puzzle_handle: str
    """Opaque handle for the puzzle (`CgPuzzleIdentity.puzzle_handle`)."""

    title: str
    """`.meta/puzzle-server-data.json`'s cached title--informational only, may be stale (see
       `CgPuzzleServerData.title`'s docstring)."""

    puzzle_pretty_id: str
    """`.meta/puzzle-server-data.json`'s cached pretty ID/slug--informational only, may be stale
       (see `CgPuzzleServerData.puzzle_pretty_id`'s docstring--never trusted as ground truth by
       this package itself either)."""

    puzzle_type: str | None
    """`.meta/puzzle-server-data.json`'s cached contribution type (e.g. "PUZZLE_INOUT"), or
       `None` for a cache file written before this field existed (see `CgPuzzleServerData.
       puzzle_type`)--run `cg puzzle repair` (after deleting `.meta/`) to populate it."""

    difficulty: str | None
    """`.meta/puzzle-server-data.json`'s cached difficulty level (e.g. "easy"), or `None` for a
       cache file written before this field existed (see `CgPuzzleServerData.difficulty`)--same
       backfill note as `puzzle_type`."""

    solution_language: CgSolutionLanguage
    """`data/puzzle-data.json`'s `solution_language`--the language `data/solution.src` is
       currently written in."""

    local_dirty: bool | None
    """Whether `data/solution.src` currently differs from the server's last-submitted answer for
       this puzzle (`bool(diff())`)--`None` unless `status(refresh=True)` checked (a live
       `TestSession/startTestSession` call; there is no local cache of the server's answer to
       compare against, unlike `codingame_tools.contribution_manager`)."""

    progress: CgLastActivityPuzzle | None
    """This codingamer's live progress/score summary for the puzzle (`Puzzle/findProgressByIds`--
       `level`/`validator_score`/`solved_count`/`attempt_count`/`xp_points`/`last_activity`), or
       `None` unless `status(refresh=True)` fetched it."""


def _align_solution_file_name(puzzle_dir: Path, solution_language: str | None) -> None:
    """Make `data/solution.*` carry the extension for `solution_language`, renaming it if it
       doesn't already, and sweep away the `solution.<ext>` symlink older versions left at the
       working directory root.

       The file carries its language's real extension because every tool that reads it--language
       server, debugger, compiler--dispatches on that. cg used to get there with a fixed
       `data/solution.src` plus a symlink beside it, which cost a day of debugging when the debug
       info named one path and the editor resolved the other and breakpoints stopped binding. One
       real file has no such gap.

       Unlike a contribution's, a puzzle's rename has no merge consequences: there is no git repo
       here, and CodinGame stores a puzzle's code *per language*, so switching language moves to a
       different server-side slot rather than conflicting with the old one. Same logic otherwise as
       `contribution_manager.manager._align_solution_file_name` (kept as an independent copy--see
       this module's docstring for why the two packages aren't cross-coupled)."""
    data_dir = puzzle_dir / DATA_SUBDIR_NAME
    for stale in puzzle_dir.glob(f"{SOLUTION_FILE_STEM}.*"):
        if stale.is_symlink():
            stale.unlink()

    extension = get_language(solution_language).extension if solution_language else None
    wanted = data_dir / solution_file_name(extension)
    existing = find_solution_file(data_dir, extension)
    if existing is None or existing == wanted:
        return
    if wanted.exists():
        return
    existing.rename(wanted)


def _placeholder_solution(language: CgSolutionLanguage, title: str, puzzle_pretty_id: str) -> str:
    """Starter `data/solution.src` for a puzzle the codingamer has never attempted in `language`.

       Confirmed live (2026-08-02): an unconditional `# TODO: ...` was invalid syntax for any
       language whose single-line comments aren't "#"-prefixed, so this leaves the file empty
       rather than guessing when the comment syntax isn't known (`format_comment` returns None).
       Shared by `import_()` and `set_language()` so the two can't drift.

       Note CodinGame's own IDE would show a real generated stub here, rendered from the puzzle's
       `stub_generator`; this client has no renderer for that."""
    placeholder = get_language(language).format_comment(
            f"TODO: solve {title!r} ({puzzle_pretty_id})")
    return f"{placeholder}\n" if placeholder is not None else ""


def _normalize_solution(code: str) -> str:
    """Solution text for equality checks, ignoring trailing-newline differences.

       The server's stored code and a locally-written file routinely differ by exactly one trailing
       newline, which would otherwise read as "you have unsaved changes" on an untouched working
       directory."""
    return code.rstrip("\n")


def _write_meta_gitignore(puzzle_dir: Path) -> None:
    """Write `puzzle_dir/.gitignore` containing `.meta/`, so `.meta/`'s contents (gitignored
       cache--see the module docstring) can never end up tracked by whatever project comes to
       track the rest of `puzzle_dir`, now or later."""
    (puzzle_dir / GITIGNORE_FILE_NAME).write_text(f"{META_SUBDIR_NAME}/\n")


class CgPuzzleManager:
    """Builds/updates a puzzle working directory (`puzzle_dir`) against the server, via an
       already-authenticated `CgClient`. See the module docstring for the (deliberately much
       simpler than `codingame_tools.contribution_manager`) design this is backed by."""

    puzzle_dir: Path
    client: CgClient

    toolchain_dir: Path
    """Per-user global directory holding user-tweakable per-language toolchain (container image)
       definitions--see `codingame_tools.language.CgLanguageContext.toolchain_dir`. Global rather
       than per-working-directory so one tweak applies everywhere. The CLI passes the value resolved
       from config; the default keeps library/test use working with no config at all."""

    mount_root: Path | None
    """Editor workspace root to bind-mount for containerized languages, or `None` to derive it (see
       `language_context`). Normally VS Code's `${workspaceFolder}`, passed through by the CLI: cg's
       own `find_workspace_root` is a heuristic, and the editor knows the real answer."""

    def __init__(
                self,
                puzzle_dir: Path | str,
                client: CgClient,
                *,
                toolchain_dir: Path | None = None,
                mount_root: Path | None = None,
                toolchain_languages: list[str] | None = None,
                toolchain_image: str | None = None,
            ) -> None:
        self.puzzle_dir = Path(puzzle_dir).resolve()
        self.client = client
        self.mount_root = Path(mount_root).resolve() if mount_root is not None else None
        self.toolchain_languages = toolchain_languages
        self.toolchain_image = toolchain_image
        self.toolchain_dir = (
                Path(toolchain_dir) if toolchain_dir is not None
                else default_global_data_dir() / TOOLCHAIN_SUBDIR_NAME
            )

    # --- paths -------------------------------------------------------------------------------

    @property
    def identity_file(self) -> Path:
        """Path to this working directory's `puzzle.json` (stable identity) manifest."""
        return self.puzzle_dir / PUZZLE_IDENTITY_FILE_NAME

    @property
    def meta_dir(self) -> Path:
        return self.puzzle_dir / META_SUBDIR_NAME

    @property
    def server_data_file(self) -> Path:
        """Path to this working directory's `.meta/puzzle-server-data.json` (gitignored cache)."""
        return self.meta_dir / _PUZZLE_SERVER_DATA_FILE_NAME

    @property
    def tests_dir(self) -> Path:
        """Path to this working directory's `.meta/tests/` (downloaded test case input/output--see
           `codingame_tools.puzzle_manager.test_cases_dir`)."""
        return self.meta_dir / TESTS_SUBDIR_NAME

    @property
    def data_dir(self) -> Path:
        """Path to this working directory's `data/` subdirectory."""
        return self.puzzle_dir / DATA_SUBDIR_NAME

    @property
    def solution_file(self) -> Path:
        """The one real solution file, `data/solution.<ext>`.

           Resolved by looking for whatever is actually there rather than by deriving the name from
           the recorded language: a working directory written by an older cg still has
           `solution.src`, and the file that exists is the one the user has been editing."""
        found = find_solution_file(self.data_dir)
        return found if found is not None else self.data_dir / solution_file_name(None)

    @property
    def solution_snapshot_file(self) -> Path:
        """Path to `.meta/solution-snapshot.json`--see `CgPuzzleSolutionSnapshot`."""
        return self.meta_dir / _SOLUTION_SNAPSHOT_FILE_NAME

    @property
    def selected_test_file(self) -> Path:
        """Path to `.meta/selected-test.json`--see `CgPuzzleSelectedTest`."""
        return self.meta_dir / _SELECTED_TEST_FILE_NAME

    def load_selected_test(self) -> CgPuzzleSelectedTest | None:
        """The explicitly selected test case, or None if none has been chosen."""
        if not self.selected_test_file.is_file():
            return None
        return CgPuzzleSelectedTest.load(self.selected_test_file)

    def select_test(self, test_index: int) -> None:
        """Choose which test case the debugger runs against.

        Raises:
            CgPuzzleManagerError: if no downloaded test case has that index--catching a typo now
                                   rather than at the moment a debug session fails to start.
        """
        available = [tc.index for tc in list_downloaded_test_cases(self.tests_dir)]
        if test_index not in available:
            raise CgPuzzleManagerError(
                    f"No downloaded test case with index {test_index}. "
                    f"Available: {', '.join(str(i) for i in available) or '(none--run `cg puzzle repair`)'}.")
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        CgPuzzleSelectedTest(test_index=test_index).save(self.selected_test_file)

    def clear_selected_test(self) -> None:
        """Forget the explicit selection, falling back to the default (the first test case)."""
        self.selected_test_file.unlink(missing_ok=True)

    def resolve_debug_test_index(self) -> int:
        """Which single test a debug session should use: the selection, else the first test case.

           Defaulting rather than refusing is deliberate--debugging works immediately after an
           import, with no selection step, which is the common case.

        Raises:
            CgPuzzleManagerError: if there are no downloaded test cases at all.
        """
        downloaded = list_downloaded_test_cases(self.tests_dir)
        if not downloaded:
            raise CgPuzzleManagerError(
                    f"No downloaded test cases in {self.tests_dir}--run `cg puzzle repair` first.")
        selected = self.load_selected_test()
        if selected is not None and any(tc.index == selected.test_index for tc in downloaded):
            return selected.test_index
        return downloaded[0].index

    @property
    def puzzle_data_file(self) -> Path:
        """Path to this working directory's `data/puzzle-data.json` (user-editable metadata)."""
        return self.data_dir / _PUZZLE_DATA_FILE_NAME

    @property
    def statement_file(self) -> Path:
        """Path to this working directory's `.meta/statement.html` (read-only reference copy of
           the puzzle's rendered problem statement)."""
        return self.meta_dir / STATEMENT_FILE_NAME

    # --- identity / server-data / puzzle-data load ----------------------------------------------

    def load_identity(self) -> CgPuzzleIdentity | None:
        """Load `puzzle.json`, or None if this directory has never been imported."""
        if not self.identity_file.is_file():
            return None
        return CgPuzzleIdentity.load(self.identity_file)

    def load_statement_html(self) -> str | None:
        """Read `.meta/statement.html`, or None if it doesn't exist (never imported, or `.meta/`
           needs `repair()`)."""
        if not self.statement_file.is_file():
            return None
        return file_to_server_text(self.statement_file.read_text(encoding="utf-8"))

    def load_server_data(self) -> CgPuzzleServerData | None:
        """Load `.meta/puzzle-server-data.json`, or None if it's missing (needs `repair()`--e.g.
           a fresh clone that (correctly) didn't bring gitignored `.meta/` along)."""
        if not self.server_data_file.is_file():
            return None
        return CgPuzzleServerData.load(self.server_data_file)

    def load_puzzle_data(self) -> CgPuzzleData | None:
        """Load `data/puzzle-data.json`, or None if this directory has never been imported."""
        if not self.puzzle_data_file.is_file():
            return None
        return CgPuzzleData.load(self.puzzle_data_file)

    def _require_state(self) -> tuple[CgPuzzleIdentity, CgPuzzleServerData, CgPuzzleData]:
        """All three manifests, for operations that need the full picture (`diff`/
           `discard_local`/`submit`/`play`).

        Raises:
            FileNotFoundError: if this working directory has never been imported at all.
            CgPuzzleManagerError: if `.meta/` is missing (needs `repair()`).
        """
        identity = self.load_identity()
        if identity is None:
            raise FileNotFoundError(
                    f"{self.identity_file} does not exist--this working directory has never "
                    "been imported (see `cg puzzle import`)."
                )
        server_data = self.load_server_data()
        if server_data is None:
            raise CgPuzzleManagerError(
                    f"{self.server_data_file} does not exist (likely gitignored and not carried "
                    "along by a fresh clone)--run `cg puzzle repair` first."
                )
        puzzle_data = self.load_puzzle_data()
        if puzzle_data is None:
            raise FileNotFoundError(f"{self.puzzle_data_file} does not exist--this working directory is in an inconsistent state.")
        return identity, server_data, puzzle_data

    def load_solution(self) -> str:
        """Read `data/solution.src`.

        Raises:
            FileNotFoundError: if `solution.src` doesn't exist.
        """
        return file_to_server_text(self.solution_file.read_text(encoding="utf-8"))

    # --- puzzle reference resolution -------------------------------------------------------

    async def _resolve_puzzle_ref(self, puzzle_ref: str) -> str:
        """Resolve a general puzzle reference to a real pretty ID, trying each of four
           strategies in order and returning the first that matches:

           1. A numeric puzzle ID (e.g. "10075")--looked up via `Puzzle/findProgressByIds`. If
              `puzzle_ref` parses as an integer but doesn't match a real puzzle, this raises
              immediately rather than falling through to the remaining strategies--a bare number
              is almost certainly meant as an ID, and searching for a puzzle literally *titled*
              that number would just produce a more confusing error.
           2. Already a valid pretty ID (e.g. "literary-alfabet-soupe")--validated (and,
              incidentally, resolved to the server's own canonical copy) via
              `Puzzle/findProgressByPrettyId`. Confirmed live: an unrecognized pretty ID responds
              200 with a JSON `null` body, which `service_request_to_dict` rejects with a
              `CgClientHttpError` ("expected a JSON dictionary, got NoneType")--that specific
              case (and only that case, identified by `status_code == 200`) is treated as "not a
              valid pretty ID," not a real error, and falls through to the next strategy.
           3. An exact-matching puzzle title (e.g. "Literary Alfabet Soupe")--via `Search/search`
              (`type_filter="PUZZLE"`). Confirmed live: for `type == "PUZZLE"`, `CgSearchResult.
              id` *is* the pretty ID directly (not a numeric ID, despite that field's own
              docstring's general claim for "other types"--puzzles are the documented exception).
           4. A case-insensitive-matching puzzle title, from that same search result set.

        Raises:
            CgPuzzleManagerError: if `puzzle_ref` parses as an integer with no matching puzzle,
                                   or if none of the four strategies resolve to a real puzzle.
        """
        stripped = puzzle_ref.strip()
        if stripped.isdigit():
            puzzle_id = int(stripped)
            progress_results = await self.client.services.puzzle.find_progress_by_ids([puzzle_id])
            match = next((p for p in progress_results if p.id == puzzle_id), None)
            if match is not None:
                return match.pretty_id
            raise CgPuzzleManagerError(f"No puzzle found with numeric ID {puzzle_id}.")

        try:
            progress = await self.client.services.puzzle.find_progress_by_pretty_id(puzzle_ref)
            return progress.pretty_id
        except CgClientHttpError as e:
            if e.status_code != 200:
                raise

        search_results = await self.client.services.search.search(puzzle_ref, type_filter="PUZZLE")
        exact = next((r for r in search_results if r.name == puzzle_ref), None)
        if exact is not None:
            return exact.id
        lowered = puzzle_ref.lower()
        case_insensitive = next((r for r in search_results if r.name.lower() == lowered), None)
        if case_insensitive is not None:
            return case_insensitive.id

        raise CgPuzzleManagerError(
                f"Could not resolve {puzzle_ref!r} to a puzzle (tried: numeric ID, pretty ID, "
                "exact title match, case-insensitive title match)."
            )

    # --- import_ -------------------------------------------------------------------------------

    async def import_(
                self,
                puzzle_ref: str,
                *,
                language: CgSolutionLanguage | None = None,
            ) -> CgPuzzleData:
        """Build this working directory from an existing puzzle: resolves `puzzle_ref` to a real
           pretty ID (see `_resolve_puzzle_ref`--a numeric ID, a pretty ID, an exact title match,
           or a case-insensitive title match, tried in that order), then resolves this
           codingamer's test session for it (`Puzzle/generateSessionFromPuzzlePrettyId`), then
           `TestSession/startTestSession` to fetch its current state.

           What lands in `data/solution.src` depends on `language`:

           - **`language=None`** (the default): the codingamer's existing saved answer, in whatever
             language they last used (`CgTestSessionQuestion.answer`), or a placeholder in
             `_DEFAULT_IMPORT_LANGUAGE` if this puzzle has never been attempted at all.
           - **`language` given**: that language, seeded with the codingamer's most recent saved
             code *for it* (CodinGame keeps one per language--see
             `CgTestSessionService.get_previous_code_by_language_id`), or a placeholder if they've
             never attempted this puzzle in it. Equivalent to importing and then calling
             `set_language()`, and it shares that code path.

           A placeholder is a bare comment: this package does not interpret the puzzle's
           stub-generator DSL to produce a real starter solution the way an IDE would;
           `.meta/stub_generator.cgstub` (see below) is written as a read-only reference instead,
           for the solver to consult by hand.

           Also writes `.meta/statement.html`, `.meta/stub_generator.cgstub`, and `.meta/tests/`
           (each test case's downloaded input/output--see
           `codingame_tools.puzzle_manager.test_cases_dir`)--all read-only reference copies,
           regenerated here, never read back or diffed--and refreshes the `solution.<ext>`
           convenience symlink at the working directory root--see the module docstring for why
           these live under `.meta/` rather than `data/`.

        Args:
            puzzle_ref: A general puzzle reference--numeric ID, pretty ID, exact title, or
                        case-insensitive title (see `_resolve_puzzle_ref`).
            language:   Language to start in. Defaults to `None`, meaning "whichever language the
                        codingamer last used for this puzzle". When given, switches to it and
                        restores any code already saved in it--see above.

        Raises:
            CgPuzzleManagerError: if this directory already tracks a puzzle, if `puzzle_ref`
                                   couldn't be resolved to a real puzzle, or if the puzzle isn't a
                                   supported type (currently, only classic "PUZZLE_INOUT"
                                   puzzles).
        """
        if self.load_identity() is not None:
            raise CgPuzzleManagerError(
                    f"{self.identity_file} already exists--this working directory has already "
                    "been imported."
                )

        puzzle_pretty_id = await self._resolve_puzzle_ref(puzzle_ref)
        test_session_handle = await self.client.services.puzzle.generate_session_from_puzzle_pretty_id(
                puzzle_pretty_id)
        session = await self.client.services.test_session.start_test_session(test_session_handle)
        question = session.current_question.question
        # `contribution` is absent for a puzzle CodinGame itself provides (confirmed live
        # 2026-08-02 with "Temperatures"), since an official puzzle was never a community
        # contribution--so its contribution type is simply unknowable. Treat that as a standard
        # in/out puzzle rather than refusing: this check exists to reject *known* unsupported kinds,
        # and failing closed here would block importing every official puzzle on the site.
        contribution_type = (
                question.contribution.contribution_type if question.contribution is not None else None)
        if contribution_type is not None and contribution_type != _SUPPORTED_CONTRIBUTION_TYPE:
            raise CgPuzzleManagerError(
                    f"Puzzle {puzzle_pretty_id!r} is a {contribution_type!r} puzzle--only "
                    f"{_SUPPORTED_CONTRIBUTION_TYPE!r} puzzles are supported so far."
                )

        answer = session.current_question.answer
        # `answer` itself can be non-None (an empty placeholder object) even with no solution
        # ever submitted--`code`/`programming_language_id` are the actual "has a real answer"
        # signal; see CgTestSessionAnswer's docstring.
        if language is not None:
            # An explicit language means "start in this one", not merely "use it if there's nothing
            # saved"--so fetch the codingamer's own most recent code for it, exactly as
            # `set_language()` would. Without this, asking for a language you'd previously written
            # a solution in would silently discard that solution in favor of a placeholder.
            solution_language = language
            saved = await self.client.services.test_session.get_previous_code_by_language_id(
                    test_session_handle, language)
            solution_code = saved if saved is not None else _placeholder_solution(
                    language, question.title, puzzle_pretty_id)
        elif answer is not None and answer.code is not None and answer.programming_language_id is not None:
            solution_language = answer.programming_language_id
            solution_code = answer.code
        else:
            solution_language = _DEFAULT_IMPORT_LANGUAGE
            solution_code = _placeholder_solution(
                    solution_language, question.title, puzzle_pretty_id)

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        self._write_solution(solution_code, solution_language)
        (self.meta_dir / STATEMENT_FILE_NAME).write_text(
                server_text_to_file(question.statement), encoding="utf-8")
        (self.meta_dir / STUB_GENERATOR_FILE_NAME).write_text(
                server_text_to_file(question.stub_generator), encoding="utf-8")
        await download_test_cases(self.client, question.test_cases, self.tests_dir)
        _write_meta_gitignore(self.puzzle_dir)

        CgPuzzleIdentity(
                schema_version=PUZZLE_SCHEMA_VERSION, puzzle_id=session.puzzle.id,
                puzzle_handle=session.puzzle.handle,
            ).save(self.identity_file)
        CgPuzzleServerData(
                test_session_handle=test_session_handle, title=question.title,
                puzzle_pretty_id=puzzle_pretty_id, puzzle_type=contribution_type,
                difficulty=session.puzzle.level,
            ).save(self.server_data_file)
        puzzle_data = CgPuzzleData(solution_language=solution_language)
        puzzle_data.save(self.puzzle_data_file)

        _align_solution_file_name(self.puzzle_dir, solution_language)
        return puzzle_data

    # --- repair ----------------------------------------------------------------------------------

    async def repair(self) -> CgPuzzleServerData:
        """Reconstruct `.meta/` (the test session handle, plus the read-only `statement.html`/
           `stub_generator.cgstub`/`tests/` reference copies) from `puzzle.json`'s stable
           `puzzle_id`--for recovering from `.meta/` being missing, e.g. after a fresh clone into
           a different repo (it's gitignored on purpose--see the module docstring) or manual
           deletion/corruption. `data/` (`solution.src`, `puzzle-data.json`) is never touched--
           there's nothing to preserve *from*, since it's exactly the git-tracked content a clone
           would have brought along.

           Looks up `Puzzle/findProgressByIds([puzzle_id])` for a fresh `pretty_id`/`title`, and
           (if already available there) a reusable `test_session_handle` directly--otherwise
           falls back to `Puzzle/generateSessionFromPuzzlePrettyId` using that fresh `pretty_id`.
           Either way, cross-checks the resulting session's own reported puzzle ID against
           `puzzle_id` before trusting anything else about it (see `CgPuzzleServerData`'s
           docstring for why a looked-up `pretty_id` specifically is never trusted un-verified).

        Raises:
            FileNotFoundError: if this working directory has never been imported (no
                                `puzzle.json`), or `data/solution.src` itself is missing (nothing
                                on disk to refresh the solution symlink for/repair alongside).
            CgPuzzleManagerError: if `.meta/` already exists (nothing to repair), or if a fresh
                                   lookup's own reported puzzle ID doesn't match `puzzle_id`
                                   (refuses rather than risk repairing with mismatched data).
        """
        identity = self.load_identity()
        if identity is None:
            raise FileNotFoundError(
                    f"{self.identity_file} does not exist--this working directory has never "
                    "been imported (nothing to repair)."
                )
        if self.server_data_file.is_file():
            raise CgPuzzleManagerError(f"{self.server_data_file} already exists--nothing to repair.")
        if not self.solution_file.is_file():
            raise FileNotFoundError(f"{self.solution_file} does not exist--nothing on disk to repair alongside.")

        progress_results = await self.client.services.puzzle.find_progress_by_ids([identity.puzzle_id])
        if not progress_results or progress_results[0].id != identity.puzzle_id:
            raise CgPuzzleManagerError(
                    f"Puzzle/findProgressByIds([{identity.puzzle_id}]) did not return a matching "
                    "result--refusing to repair with mismatched data."
                )
        progress = progress_results[0]

        test_session_handle = progress.test_session_handle
        if test_session_handle is None:
            test_session_handle = await self.client.services.puzzle.generate_session_from_puzzle_pretty_id(
                    progress.pretty_id)

        session = await self.client.services.test_session.start_test_session(test_session_handle)
        if session.puzzle.id != identity.puzzle_id:
            raise CgPuzzleManagerError(
                    f"TestSession/startTestSession({test_session_handle!r}) returned puzzle "
                    f"{session.puzzle.id}, expected {identity.puzzle_id}--refusing to repair "
                    "with mismatched data."
                )
        question = session.current_question.question

        self.meta_dir.mkdir(parents=True, exist_ok=True)
        (self.meta_dir / STATEMENT_FILE_NAME).write_text(
                server_text_to_file(question.statement), encoding="utf-8")
        (self.meta_dir / STUB_GENERATOR_FILE_NAME).write_text(
                server_text_to_file(question.stub_generator), encoding="utf-8")
        await download_test_cases(self.client, question.test_cases, self.tests_dir)
        _write_meta_gitignore(self.puzzle_dir)

        server_data = CgPuzzleServerData(
                test_session_handle=test_session_handle, title=progress.title,
                puzzle_pretty_id=progress.pretty_id,
                # None for an official CodinGame puzzle, which has no contribution to read a
                # type from--see import_(). CgPuzzleServerData.puzzle_type is already
                # optional, so this stores cleanly.
                puzzle_type=(
                        question.contribution.contribution_type
                        if question.contribution is not None else None),
                difficulty=session.puzzle.level,
            )
        server_data.save(self.server_data_file)

        puzzle_data = self.load_puzzle_data()
        if puzzle_data is not None:
            _align_solution_file_name(self.puzzle_dir, puzzle_data.solution_language)
        return server_data

    # --- diff / discard_local / submit ----------------------------------------------------------

    async def _fetch_current_answer_code(self) -> tuple[str, CgSolutionLanguage] | None:
        """A fresh `TestSession/startTestSession` call (using the cached `test_session_handle`),
           returning the codingamer's current server-side saved answer (code, language), or None
           if this puzzle has never been submitted at all."""
        _, server_data, _ = self._require_state()
        session = await self.client.services.test_session.start_test_session(server_data.test_session_handle)
        answer = session.current_question.answer
        # see the note in import_()--`answer` itself can be non-None with no real answer inside.
        if answer is None or answer.code is None or answer.programming_language_id is None:
            return None
        return answer.code, answer.programming_language_id

    async def _fetch_saved_code_for_language(
                self, language: CgSolutionLanguage) -> str | None:
        """The codingamer's most recently saved server-side code *for one language*, or None if
           they have never written anything in it for this puzzle.

           CodinGame stores a puzzle's code per language, so this is the honest counterpart to a
           local file: it answers "what does the server hold in the language I am working in?"
           rather than "what language was I last using on the website?". A pure read--unlike
           `TestSession/play`, it saves nothing."""
        _, server_data, _ = self._require_state()
        return await self.client.services.test_session.get_previous_code_by_language_id(
                server_data.test_session_handle, language)

    async def diff(self) -> str:
        """A unified text diff between the local `data/solution.src` and the server's current
           last-submitted answer for this puzzle--empty if they're identical, or if there's no
           local file/no server answer at all yet (nothing meaningful to diff in that case).

        Raises:
            FileNotFoundError: if this working directory has never been imported.
            CgPuzzleManagerError: if `.meta/` is missing (run `repair()` first).
        """
        _, _, puzzle_data = self._require_state()
        local_lines = file_to_server_text(
                self.solution_file.read_text(encoding="utf-8")).splitlines(keepends=True) \
            if self.solution_file.is_file() else []
        # Compared against the server's code *in the local language*, not against whatever language
        # the test session happens to be sitting in. CodinGame stores a puzzle's code per language,
        # so the session's answer can easily be a different language entirely -- diffing a local C++
        # file against a saved Python one produced a whole-file diff that meant nothing.
        language = puzzle_data.solution_language
        saved = await self._fetch_saved_code_for_language(language) if language else None
        server_lines = saved.splitlines(keepends=True) if saved is not None else []
        return "".join(difflib.unified_diff(server_lines, local_lines, fromfile="server", tofile="local"))

    def _write_solution(self, code: str, language: CgSolutionLanguage) -> None:
        """Write `data/solution.<ext>` and record exactly what was written.

           Every writer of the solution goes through here so the snapshot can never drift from
           the file--that snapshot is what lets `set_language()` tell "the user edited this" from
           "this is still what we generated", without re-deriving anything.

           `code` is a server-side value throughout: rendered to the file's on-disk form (see
           `common.text_files.server_text_to_file`) and stored in the snapshot as the value, so
           the snapshot compares directly against what `load_solution()` reads back out."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # Named from the language being written, not from whatever is on disk: this is the one
        # place a language change actually takes effect, so the new name has to win. Any solution
        # file under the previous extension is removed rather than left to be found later.
        target = self.data_dir / solution_file_name(get_language(language).extension)
        existing = find_solution_file(self.data_dir)
        if existing is not None and existing != target:
            existing.unlink()
        target.write_text(server_text_to_file(code), encoding="utf-8")
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        CgPuzzleSolutionSnapshot(solution_language=language, code=code).save(
                self.solution_snapshot_file)

    def load_solution_snapshot(self) -> CgPuzzleSolutionSnapshot | None:
        """What this client last wrote to `data/solution.src`, or `None` if unknown (never
           written, or `.meta/` predates the snapshot)."""
        if not self.solution_snapshot_file.is_file():
            return None
        return CgPuzzleSolutionSnapshot.load(self.solution_snapshot_file)

    async def _solution_is_safe_to_replace(
                self, server_data: CgPuzzleServerData, language: CgSolutionLanguage,
            ) -> bool:
        """Whether `data/solution.src` can be overwritten without losing anything.

           Safe in exactly two cases:

           - It still matches what this client last wrote (`.meta/solution-snapshot.json`), so the
             user never touched it. Checked first, and needs no network. Deliberately a recorded
             snapshot rather than a regenerated one: re-deriving a placeholder and comparing would
             break silently the moment generation stopped being byte-identical across releases, and
             an untouched directory would start claiming unsaved changes.
           - It matches the server's saved code for `language`--the user did edit it, but has since
             submitted those edits, so nothing local is unique.

           A missing snapshot falls through to the server comparison, which errs toward refusing.
        """
        local = _normalize_solution(
                file_to_server_text(self.solution_file.read_text(encoding="utf-8"))
                if self.solution_file.is_file() else "")
        snapshot = self.load_solution_snapshot()
        if snapshot is not None and snapshot.solution_language == language \
                and _normalize_solution(snapshot.code) == local:
            return True
        saved = await self.client.services.test_session.get_previous_code_by_language_id(
                server_data.test_session_handle, language)
        return saved is not None and _normalize_solution(saved) == local

    async def set_language(
                self,
                language: CgSolutionLanguage,
                *,
                force: bool = False,
            ) -> CgPuzzleSetLanguageResult:
        """Switch this working directory to a different language, restoring the codingamer's own
           most recent code for it.

           CodinGame keeps your latest source *per language* for a puzzle, so switching is not
           "throw away what you have and start over"--any solution you'd previously written in the
           target language comes back (see
           `CgTestSessionService.get_previous_code_by_language_id`). Only a language you have never
           attempted gets a placeholder.

           **This changes local state only.** The server's notion of your current language is not
           moved by fetching code (confirmed live--it's a pure read); it follows once you actually
           run a server-side test or submit in the new language.

           Refuses when `data/solution.src` holds work the server doesn't have, since switching
           overwrites it. Local edits are considered safe to discard when they match either the
           server's saved code for the current language *or* the placeholder this package would
           have generated for it--the latter matters because importing with an explicit language
           you've never used writes a placeholder that was never saved server-side, which would
           otherwise leave the working directory permanently unable to switch away.

        Args:
            language: CodinGame language ID to switch to, e.g. "C++" (see `CgSolutionLanguage`).
            force:    Switch even when local edits would be lost.

        Returns:
            A `CgPuzzleSetLanguageResult`--check `from_server` to tell "your old solution is back"
            from "here's an empty starting point".

        Raises:
            FileNotFoundError: if this working directory has never been imported.
            CgPuzzleManagerError: if `language` isn't one this client knows, if it's already the
                                   current language, or if local edits would be lost and `force`
                                   is False.
        """
        _, server_data, puzzle_data = self._require_state()
        previous_language = puzzle_data.solution_language
        if language not in list_language_cg_ids():
            raise CgPuzzleManagerError(
                    f"{language!r} isn't a language this client knows. Known languages: "
                    f"{', '.join(list_language_cg_ids())}."
                )
        if language == previous_language:
            raise CgPuzzleManagerError(
                    f"{self.puzzle_dir} is already using {language!r}--nothing to switch."
                )

        test_session = self.client.services.test_session
        if not force and not await self._solution_is_safe_to_replace(server_data, previous_language):
            raise CgPuzzleManagerError(
                    f"{self.solution_file} has {previous_language!r} changes the server doesn't "
                    "have--switching would discard them. Submit them first (`cg puzzle submit`), "
                    "or pass --force to discard them."
                )

        saved_new = await test_session.get_previous_code_by_language_id(
                server_data.test_session_handle, language)
        from_server = saved_new is not None
        code = saved_new if saved_new is not None else _placeholder_solution(
                language, server_data.title or "", server_data.puzzle_pretty_id or "")

        self._write_solution(code, language)
        dataclasses.replace(puzzle_data, solution_language=language).save(self.puzzle_data_file)
        _align_solution_file_name(self.puzzle_dir, language)
        return CgPuzzleSetLanguageResult(
                language=language, previous_language=previous_language,
                code=code, from_server=from_server,
            )

    async def discard_local(self) -> CgPuzzleDiscardResult:
        """Discard local edits: overwrite `data/solution.src` with the server's current
           last-submitted answer for this puzzle (and update `data/puzzle-data.json`'s
           `solution_language` to match, in case the last submission was in a different language
           than previously recorded), then refresh the `solution.<ext>` symlink. Purely a local
           overwrite--no submission or other server-side side effect.

        Raises:
            FileNotFoundError: if this working directory has never been imported.
            CgPuzzleManagerError: if `.meta/` is missing (run `repair()` first), or if this
                                   puzzle has never been submitted at all (nothing server-side to
                                   discard to).
        """
        identity, server_data, puzzle_data = self._require_state()
        current = await self._fetch_current_answer_code()
        if current is None:
            raise CgPuzzleManagerError(
                    f"Puzzle {identity.puzzle_id} has no server-side answer yet (never "
                    "submitted)--nothing to discard local edits to."
                )
        code, solution_language = current
        self._write_solution(code, solution_language)
        if solution_language != puzzle_data.solution_language:
            dataclasses.replace(puzzle_data, solution_language=solution_language).save(self.puzzle_data_file)
        _align_solution_file_name(self.puzzle_dir, solution_language)
        return CgPuzzleDiscardResult(code=code, solution_language=solution_language)

    async def submit(self) -> CgSubmissionReport:
        """Submit the current local `data/solution.src` to the server for credit
           (`TestSession/submit`), in `data/puzzle-data.json`'s recorded `solution_language`,
           then fetch and return the resulting results report
           (`Report/findReportBySubmission`)--score, achievement completion, and per-validator
           pass/fail.

           Named `submit()`, not `push()` (unlike `codingame_tools.contribution_manager`'s
           git-vocabulary naming)--a puzzle working directory has two distinct server-side
           persistence phases, not one: the test session's current answer (see `play()`'s
           docstring--confirmed live to be silently updated by *any* `TestSession/play` call, not
           just this method) and this method's actual graded submission. "Push" would suggest
           the former; this method is unambiguously the latter.

           CAUTION: unlike `codingame_tools.contribution_manager`'s `push()`, this always
           creates a new graded submission--there's no draft/private-staging concept for puzzle
           solutions. See `CgTestSessionService.submit`'s docstring for the (currently
           unhandled) heavy-validation Cloudflare/524 timeout risk shared with contribution
           submission.

           The report is fetched via `CgReportServiceHelper.find_report_by_submission_when_ready`
           rather than the plain `find_report_by_submission`, since calling the latter immediately
           after submitting can race server-side grading--see `CgSubmissionReport`'s class
           docstring.

        Returns:
            The new submission's `CgSubmissionReport` (its `.submission_id` is the same numeric
            ID `TestSession/submit` itself returns).

        Raises:
            FileNotFoundError: if this working directory has never been imported.
            CgPuzzleManagerError: if `.meta/` is missing (run `repair()` first).
            TimeoutError: if grading hasn't finished within
                          `find_report_by_submission_when_ready`'s default timeout.
        """
        _, server_data, puzzle_data = self._require_state()
        code = file_to_server_text(self.solution_file.read_text(encoding="utf-8"))
        request = CgSubmitRequest(code=code, programming_language_id=puzzle_data.solution_language)
        submission_id = await self.client.services.test_session.submit(server_data.test_session_handle, request)
        return await self.client.services.report.helper.find_report_by_submission_when_ready(submission_id)

    # --- play ------------------------------------------------------------------------------------

    def resolve_play_indices(self, test_indices: list[int] | None = None) -> list[int]:
        """Resolve which 1-based test indices `play()`/`play_one()` should run against:
           `test_indices` if given, unchanged; otherwise every downloaded test case's index
           (`.meta/tests/`, i.e. every test case this working directory actually knows about--NOT
           necessarily every test case the puzzle has). No network access--for a caller that wants
           to loop over `play_one()` itself (e.g. to display each result as it comes in, rather
           than waiting for the whole batch--see `play()`), this is the piece that used to be
           done implicitly inside `play()`.

        Raises:
            FileNotFoundError: if this working directory has never been imported, or (only when
                                `test_indices` is not given) has no downloaded test cases at all.
            CgPuzzleManagerError: if `.meta/` is missing (run `repair()` first).
        """
        self._require_state()
        if test_indices is not None:
            return test_indices
        downloaded = list_downloaded_test_cases(self.tests_dir)
        if not downloaded:
            raise FileNotFoundError(f"{self.tests_dir} has no downloaded test cases--run `cg puzzle repair` first.")
        return [tc.index for tc in downloaded]

    async def play_one(self, index: int) -> CgPuzzleRemoteTestResult:
        """Run the current local `data/solution.src` against a single one of the puzzle's test
           cases via the server (`TestSession/play`--the IDE's "Test"/"Run" button, as opposed
           to `submit()`'s full "Submit"). One live API call.

           CONFIRMED LIVE (2026-08-01): this call has a side effect beyond just running the given
           test case--the server durably persists whatever `code` was sent as the test session's
           current answer (the same "current answer" returned by `TestSession/startTestSession`,
           and visible in the web IDE from any browser), whether or not the test case actually
           passes. This is NOT a grading/submission event (no `Report`/score is produced), and
           there's no separate "just save, don't run" call--the web IDE itself has no autosave
           either (confirmed: editing code there without running a test, then navigating away,
           prompts "All changes will be lost")--so running at least one test case is, in effect,
           the only way to persist a change short of a real submission. `submit()` also persists
           the code this way (again regardless of whether the submission scores well), as a side
           effect of grading it.

        Args:
            index: 1-based index to run against (see `CgTestSessionTestCase.index`). Need not be
                   locally downloaded--the server runs by index alone.

        Returns:
            The `CgPuzzleRemoteTestResult` for this index.

        Raises:
            FileNotFoundError: if this working directory has never been imported.
            CgPuzzleManagerError: if `.meta/` is missing (run `repair()` first).
        """
        _, server_data, puzzle_data = self._require_state()
        downloaded = list_downloaded_test_cases(self.tests_dir)
        labels_by_index = {tc.index: tc.label for tc in downloaded}
        code = file_to_server_text(self.solution_file.read_text(encoding="utf-8"))
        request = CgPlayRequest(
                code=code,
                programming_language_id=puzzle_data.solution_language,
                multiple_languages=CgMultipleLanguagesTestParams(test_index=index),
            )
        play_result = await self.client.services.test_session.play(server_data.test_session_handle, request)
        return CgPuzzleRemoteTestResult(
                index=index, label=labels_by_index.get(index, f"test {index}"), result=play_result,
            )

    async def play(self, test_indices: list[int] | None = None) -> list[CgPuzzleRemoteTestResult]:
        """Run the current local `data/solution.src` against one or more of the puzzle's test
           cases via the server (`TestSession/play`). Convenience batch wrapper around
           `play_one()`--each index is a separate live API call (there is no batch form of
           `TestSession/play`), run sequentially, in the order given; see `play_one()`'s docstring
           for the shared side-effect caveat.

           A caller that wants to display/act on each result as soon as it's available, rather
           than waiting for every index to finish first, should call `resolve_play_indices()` and
           `play_one()` directly in its own loop instead of this method (see `cg puzzle
           play-server`'s CLI implementation for exactly that).

        Args:
            test_indices: 1-based indices to run against (see `CgTestSessionTestCase.index`).
                          Need not be locally downloaded--the server runs by index alone.
                          If not given, runs every downloaded test case (`.meta/tests/`)--see
                          `resolve_play_indices()`.

        Returns:
            One `CgPuzzleRemoteTestResult` per index, in the order run.

        Raises:
            FileNotFoundError: if this working directory has never been imported, or (only when
                                `test_indices` is not given) has no downloaded test cases at all.
            CgPuzzleManagerError: if `.meta/` is missing (run `repair()` first).
        """
        indices = self.resolve_play_indices(test_indices)
        return [await self.play_one(index) for index in indices]

    # --- play_local --------------------------------------------------------------------------

    def resolve_play_local_test_cases(
                self,
                test_indices: list[int] | None = None,
            ) -> list[CgPuzzleDownloadedTestCase]:
        """Resolve which downloaded test cases `play_local()`/`play_local_one()` should run
           against: the downloaded test cases matching `test_indices`, in the order given, if
           given; otherwise every downloaded test case (`.meta/tests/`). No subprocess execution
           --for a caller that wants to loop over `play_local_one()` itself (e.g. to display each
           result as it comes in, rather than waiting for the whole batch--see `play_local()`),
           this is the piece that used to be done implicitly inside `play_local()`.

        Raises:
            FileNotFoundError: if this working directory has never been imported, or has no
                                downloaded test cases at all (run `cg puzzle repair` first).
            CgPuzzleManagerError: if `test_indices` contains an index with no downloaded test
                                   case.
        """
        identity = self.load_identity()
        if identity is None:
            raise FileNotFoundError(
                    f"{self.identity_file} does not exist--this working directory has never "
                    "been imported (see `cg puzzle import`)."
                )
        if self.load_puzzle_data() is None:
            raise FileNotFoundError(f"{self.puzzle_data_file} does not exist--this working directory is in an inconsistent state.")
        downloaded = list_downloaded_test_cases(self.tests_dir)
        if not downloaded:
            raise FileNotFoundError(f"{self.tests_dir} has no downloaded test cases--run `cg puzzle repair` first.")
        if test_indices is None:
            return downloaded
        by_index = {tc.index: tc for tc in downloaded}
        test_cases: list[CgPuzzleDownloadedTestCase] = []
        for index in test_indices:
            test_case = by_index.get(index)
            if test_case is None:
                raise CgPuzzleManagerError(f"No downloaded test case with index {index}.")
            test_cases.append(test_case)
        return test_cases

    def language_context(
                self,
                solution_language: CgSolutionLanguage | None = None,
                *,
                mount_root: Path | None = None,
            ) -> CgLanguageContext:
        """Describe this working directory to `codingame_tools.language`--see `CgLanguageContext`.

           `mount_root` is what a containerized language bind-mounts. It defaults to the enclosing
           VS Code workspace root, so that in-container paths equal host paths and one container
           serves the whole workspace; pass it explicitly (VS Code's `${workspaceFolder}`) when the
           real workspace is known, since `find_workspace_root` is only a guess.

           Infallible by design: never reads `puzzle.json`, never needs the directory to have been
           imported. `solution_language` is accepted for signature stability but no longer selects
           a path: there is one real solution file and `solution_file` finds it whatever extension
           it carries.
        """
        return CgLanguageContext(
                root=self.puzzle_dir,
                solution_file=self.solution_file,
                meta_dir=self.meta_dir,
                toolchain_dir=self.toolchain_dir,
                mount_root=mount_root or self.mount_root or find_workspace_root(self.puzzle_dir),
                toolchain_languages=self.toolchain_languages,
                toolchain_image=self.toolchain_image,
            )

    async def provision_vscode(
                self,
                *,
                workspace_root: Path | None = None,
                force: bool = False,
                check: bool = False,
                debug_adapter_logging: bool = False,
            ) -> list[Path]:
        """Generate this working directory's VS Code run/debug configuration, if its language has
           any, and write it into the workspace.

           What's generated is the same for every working directory of that language, so this is
           run once per language rather than once per directory, and nothing here goes stale when
           test cases or the solution language change.

        Args:
            workspace_root: Where `.vscode/` goes. Defaults to `find_workspace_root()`--VS Code
                             reads `launch.json` only from the workspace *root*, which is often
                             not this working directory (see `codingame_tools.language.vscode`).
            force:          Overwrite an existing config file that isn't strict JSON (i.e. uses
                             JSONC comments) instead of refusing.
            debug_adapter_logging:
                            Generate a configuration that logs the debug adapter's
                             own protocol exchange--see `CgVsCodeRequest`.
            check:          Report what *would* change without touching anything. This is how
                             staleness is detected: generated entries carry no version stamp, so
                             "would rewriting change anything?" is the whole question, and it stays
                             correct when a future release alters what gets generated.

        Returns:
            Every path that changed (or, under `check`, would change), in write order. Empty means
            already up to date, or that this language has no VS Code integration.

        Raises:
            FileNotFoundError: if this working directory has never been imported.
            CgVsCodeMergeError: if an existing config file can't be safely merged into.
        """
        puzzle_data = self.load_puzzle_data()
        if puzzle_data is None:
            raise FileNotFoundError(f"{self.puzzle_data_file} does not exist--this working directory is in an inconsistent state.")
        resolved_workspace_root = (
                Path(workspace_root).resolve() if workspace_root is not None
                else find_workspace_root(self.puzzle_dir)
            )
        request = CgVsCodeRequest(
                ctx=self.language_context(
                        puzzle_data.solution_language, mount_root=resolved_workspace_root),
                workspace_root=resolved_workspace_root,
                debug_adapter_logging=debug_adapter_logging,
            )
        provisioning = await get_language(puzzle_data.solution_language).build_vscode_provisioning(request)
        if provisioning is None:
            return []
        return write_provisioning(
                provisioning, root=self.puzzle_dir, workspace_root=resolved_workspace_root,
                language=puzzle_data.solution_language, force=force, dry_run=check)

    async def start_debug_session(
                self,
                test_index: int,
                *,
                timeout: float = DEFAULT_BUILD_TIMEOUT_SECONDS,
            ) -> CgDebugSession:
        """Get the solution ready for a debugger to attach to, fed by test case `test_index`'s
           input--see `codingame_tools.language.CgLanguage.start_debug_session`.

           Only meaningful for a language whose debugger attaches to a running target (C++ via
           gdbserver). Python3's debugger launches the program itself, so it never calls this.

        Raises:
            FileNotFoundError: if this working directory has never been imported.
            CgPuzzleManagerError: if there's no downloaded test case with that index.
            CgLanguageOperationNotSupportedError: if this language has no attach-style debugging.
        """
        puzzle_data = self.load_puzzle_data()
        if puzzle_data is None:
            raise FileNotFoundError(f"{self.puzzle_data_file} does not exist--this working directory is in an inconsistent state.")
        test_case = next(
                (tc for tc in list_downloaded_test_cases(self.tests_dir) if tc.index == test_index),
                None,
            )
        if test_case is None:
            raise CgPuzzleManagerError(f"No downloaded test case with index {test_index}.")
        ctx = self.language_context(puzzle_data.solution_language)
        # The downloaded file's bytes verbatim: `.meta/tests/` holds byte-exact fileservlet
        # downloads, so this is already exactly what CodinGame puts on the solution's stdin.
        return await get_language(puzzle_data.solution_language).start_debug_session(
                ctx, test_case.input_text, timeout=timeout)

    async def stop_debug_session(self) -> None:
        """Tear down whatever `start_debug_session()` started. Safe to call when nothing is
           running."""
        puzzle_data = self.load_puzzle_data()
        if puzzle_data is None:
            return
        ctx = self.language_context(puzzle_data.solution_language)
        await get_language(puzzle_data.solution_language).stop_debug_session(ctx)

    async def build_solution(
                self,
                *,
                profile: CgBuildProfile = "run",
                timeout: float = DEFAULT_BUILD_TIMEOUT_SECONDS,
            ) -> CgBuildResult:
        """Build `data/solution.src` for local execution, if its language needs building at all
           (Python3 doesn't--this is then an immediate no-op success).

           A separate step from `play_local_one()` so a caller can display build diagnostics apart
           from program output, report a compile error once rather than once per test case, and give
           building its own generous timeout. Cheap to call repeatedly: an unchanged source since the
           last successful build returns `up_to_date=True` having done nothing.

           `play_local()` calls this for you. A caller driving `play_local_one()` itself (as
           `cg puzzle play` does, to stream results) must call this first.

        Returns:
            A `CgBuildResult`--check `.ok`; a build failure is reported, never raised.

        Raises:
            FileNotFoundError: if this working directory has never been imported.
        """
        puzzle_data = self.load_puzzle_data()
        if puzzle_data is None:
            raise FileNotFoundError(f"{self.puzzle_data_file} does not exist--this working directory is in an inconsistent state.")
        language = get_language(puzzle_data.solution_language)
        ctx = self.language_context(puzzle_data.solution_language)
        return await language.build(ctx, profile=profile, timeout=timeout)

    async def play_local_one(
                self,
                test_case: CgPuzzleDownloadedTestCase,
                *,
                timeout: float = DEFAULT_RUN_TIMEOUT_SECONDS,
            ) -> CgPuzzleLocalTestResult:
        """Run the current local `data/solution.src` against a single downloaded test case
           entirely locally--no network access at all, unlike `play_one()`--by shelling out to
           the appropriate interpreter/compiler as a subprocess (see
           `codingame_tools.language.CgLanguage.run`) and comparing captured stdout to the test
           case's expected `output.txt`.

           Never raises just because the test failed (crashed, timed out, or mismatched)--that's
           reflected in the returned result's `passed`, same spirit as `codingame_tools.
           contribution_manager.manager.CgContributionManager.run_local_test`. See `play_local()`,
           which raises `CgPuzzleLocalTestFailedError` if any of a batch failed.

           **Does not build.** For a language that needs compiling, call `build_solution()` first
           (`play_local()` does this for you); this method only runs the already-built artifact.

           For stepping through `solution.src` in a debugger against a specific test case's input
           instead, see `codingame_tools.test_runner.debug_stdin` (launched directly, not through
           this method--a subprocess like this one spawns can't be stepped into).

        Args:
            test_case: Which downloaded test case to run (see `resolve_play_local_test_cases()`).
            timeout:   Wall-clock timeout in seconds--see `codingame_tools.language.
                       DEFAULT_RUN_TIMEOUT_SECONDS`.

        Returns:
            The outcome--see `CgPuzzleLocalTestResult`.

        Raises:
            FileNotFoundError: if this working directory has never been imported.
            CgLanguageOperationNotSupportedError: if `data/puzzle-data.json`'s `solution_language`
                                                   isn't yet supported by `codingame_tools.
                                                   language`.
        """
        puzzle_data = self.load_puzzle_data()
        if puzzle_data is None:
            raise FileNotFoundError(f"{self.puzzle_data_file} does not exist--this working directory is in an inconsistent state.")
        ctx = self.language_context(puzzle_data.solution_language)
        run_result = await get_language(puzzle_data.solution_language).run(
                ctx, test_case.input_text, timeout=timeout)
        passed = not run_result.timed_out and run_result.returncode == 0 \
            and outputs_match(run_result.output, test_case.output_text)
        return CgPuzzleLocalTestResult(
                index=test_case.index, label=test_case.label, passed=passed,
                input=test_case.input_text, expected_output=test_case.output_text,
                actual_output=run_result.output, stderr=run_result.stderr,
                timed_out=run_result.timed_out,
            )

    async def play_local(
                self,
                test_indices: list[int] | None = None,
                *,
                timeout: float = DEFAULT_RUN_TIMEOUT_SECONDS,
                build_timeout: float = DEFAULT_BUILD_TIMEOUT_SECONDS,
            ) -> list[CgPuzzleLocalTestResult]:
        """Run the current local `data/solution.src` against the downloaded `.meta/tests/` test
           cases entirely locally--no network access at all, unlike `play()`. Convenience batch
           wrapper that calls `build_solution()` once and then loops `play_local_one()`
           sequentially, in the order given.

           A caller that wants to display/act on each result as soon as it's available, rather
           than waiting for every test case to finish first, should call `build_solution()`,
           `resolve_play_local_test_cases()` and `play_local_one()` directly in its own loop
           instead of this method (see `cg puzzle play`'s CLI implementation for exactly that).

        Args:
            test_indices: If given, only run the downloaded test cases with these indices (the
                          same numbering `.meta/tests/`'s directory names and `play()`'s own
                          `test_indices` use), run in the order given. Defaults to running every
                          downloaded test case--see `resolve_play_local_test_cases()`.
            timeout:    Per-test-case wall-clock timeout in seconds--see
                        `codingame_tools.language.DEFAULT_RUN_TIMEOUT_SECONDS`.
            build_timeout: Wall-clock timeout for the one-time build step--see
                        `codingame_tools.language.DEFAULT_BUILD_TIMEOUT_SECONDS`.

        Returns:
            One `CgPuzzleLocalTestResult` per test case run, in the order run.

        Raises:
            FileNotFoundError: if this working directory has never been imported, or has no
                                downloaded test cases at all (run `cg puzzle repair` first).
            CgPuzzleManagerError: if `test_indices` contains an index with no downloaded test
                                   case.
            CgPuzzleBuildFailedError: if the solution failed to build--carries the build output.
            CgLanguageOperationNotSupportedError: if `data/puzzle-data.json`'s `solution_language`
                                                   isn't yet supported by `codingame_tools.
                                                   language`.
            CgPuzzleLocalTestFailedError: if any test case's output didn't match (or the solution
                                           crashed/timed out)--carries every result via `.results`.
        """
        test_cases = self.resolve_play_local_test_cases(test_indices)
        build_result = await self.build_solution(timeout=build_timeout)
        if not build_result.ok:
            raise CgPuzzleBuildFailedError(build_result)
        results = [await self.play_local_one(test_case, timeout=timeout) for test_case in test_cases]
        if any(not r.passed for r in results):
            raise CgPuzzleLocalTestFailedError(results)
        return results

    # --- status ----------------------------------------------------------------------------

    async def status(self, *, refresh: bool = False) -> CgPuzzleStatus:
        """A point-in-time summary of this working directory--see `CgPuzzleStatus`.

           By default, entirely local/cheap: no network access at all--just the three on-disk
           manifests. Pass `refresh=True` to also check `local_dirty` (a live
           `TestSession/startTestSession` call, same as `diff()`) and fetch `progress` (a live
           `Puzzle/findProgressByIds` call)--both stay `None` otherwise. Unlike
           `codingame_tools.contribution_manager`'s `status()`, there is no cache file this writes
           to for next time--puzzle working directories have no such cache at all (see the module
           docstring); every `refresh=True` call is genuinely live, every time.

        Args:
            refresh: If True, also check for local edits against the server's last-submitted
                     answer and fetch live progress/score info. Defaults to False.

        Raises:
            FileNotFoundError: if this working directory has never been imported.
            CgPuzzleManagerError: if `.meta/` is missing (run `repair()` first).
        """
        identity, server_data, puzzle_data = self._require_state()
        local_dirty: bool | None = None
        progress: CgLastActivityPuzzle | None = None
        if refresh:
            local_dirty = bool(await self.diff())
            progress_results = await self.client.services.puzzle.find_progress_by_ids([identity.puzzle_id])
            if progress_results and progress_results[0].id == identity.puzzle_id:
                progress = progress_results[0]
        return CgPuzzleStatus(
                puzzle_dir=self.puzzle_dir,
                puzzle_id=identity.puzzle_id,
                puzzle_handle=identity.puzzle_handle,
                title=server_data.title,
                puzzle_pretty_id=server_data.puzzle_pretty_id,
                puzzle_type=server_data.puzzle_type,
                difficulty=server_data.difficulty,
                solution_language=puzzle_data.solution_language,
                local_dirty=local_dirty,
                progress=progress,
            )

    # --- delete --------------------------------------------------------------------------------

    async def delete(self) -> None:
        """Remove this working directory entirely (`puzzle.json`, `.meta/`, `data/`, and the
           `solution.<ext>` convenience symlink)--purely local. Unlike `codingame_tools.
           contribution_manager.CgContributionManager.delete()`, there is no server-side
           counterpart at all here--a puzzle already exists on the server before you can solve
           it (see the module docstring), so there is nothing to delete *there*; this only ever
           removes your own local working directory.

           No confirmation prompt here--that's the CLI's job (`cg puzzle delete`), same as every
           other method in this class.

        Raises:
            FileNotFoundError: if this working directory has never been imported.
        """
        if self.load_identity() is None:
            raise FileNotFoundError(
                    f"{self.identity_file} does not exist--this working directory has never "
                    "been imported (nothing to delete)."
                )
        # Before removing the directory: a containerized language leaves a long-lived container
        # bind-mounted to it. Orphaning one is worse than untidy--container names are derived from
        # the directory path, so a new working directory later created at the same path would
        # otherwise silently attach to the stale container (and its stale build artifacts).
        await remove_containers_for_root(self.puzzle_dir)
        shutil.rmtree(self.puzzle_dir)
