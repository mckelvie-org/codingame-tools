"""`CgContributionManager`: builds a contribution working directory from an existing server-side
   contribution (`import_`), pushes a working directory's content back to the server (`push`), and
   reconciles local/server drift (`rebase`, `fetch`, and the `merge_start`/`merge_continue`/
   `merge_abort` state machine)--backed by a real git repository whose working tree is `data/`.

   Deliberately named `push`, not `commit`: this class already has a real, distinct git-level
   "commit" concept (`CgGitRepo.commit_worktree`, a plain local commit onto `main`, no network
   involved)--calling *this* method `commit()` too (as an earlier version of this API did) invited
   exactly the confusion `git`-literate users would expect: does it commit locally, or send data to
   the server? `push`, matching `git push`'s own "send my local state to the authoritative remote"
   meaning, does not.

   Three branches (see `codingame_tools.contribution_manager.layout` for the exact names):

   - `main`: the user's own line--`data/` is always `main`'s checkout. Commits here are optional/
     user-initiated for the user's own benefit, except a few points where this class also commits
     automatically (a successful `push()`, a `rebase()` fast-forward, `merge_discard_local`)--see
     each method's docstring.
   - `server`: mirrors known server state. Every commit carries git trailers (contribution ID,
     version, cover binary ID/hash--see `contribution_commit_data.CgContributionCommitMetadata`)
     and a `server.<version>` tag. Its tip is always "the current remote"; `git merge-base main
     server` is always "the last point `main` synced with the server"--no separate last-committed/
     remote cache needed, it falls out of branch topology for free.
   - `version-data`: an orphan branch (unrelated tree history), one commit per server version,
     holding just `contribution-version-data.json` (see `contribution_commit_data`)--the complete
     redacted `CgContribution`, kept in full rather than a narrower schema so nothing here needs to
     change if some future need for another field shows up.

   `server`/`version-data` are never checked out--every write to them goes through
   `git_repo.CgGitRepo`'s plumbing (a scratch index, or a single-blob tree for `version-data`),
   never touching `HEAD`, the real index, or anything under `data/`. This is deliberate: `data/`
   must always and only ever reflect `main`'s real content.

   The git-dir itself (objects/refs/HEAD/index/config) lives in one of two places, decided once at
   `create()`/`import_()` time (recorded in `.meta/contribution-meta.json`, see
   `CgContributionMeta.git_repo`) and never re-derived on subsequent commands--so a git project
   appearing around this directory later can't move a repo that already exists at a fixed spot:

   - **External**, at `<contribution_dir>/.meta/.contribution-git/` with `data/` as its work tree,
     via `--git-dir`/`--work-tree` decoupling (see `git_repo`). Chosen when the working directory is
     created inside an existing git project. Nothing under `<contribution_dir>` carries a `.git`
     marker, so that outer project's own embedded-repository detection is never tripped.
   - **Embedded**, at `data/.git`--`data/` as a perfectly ordinary git working directory, drivable
     with plain `git` commands. Chosen when nothing was already tracking this location, where there
     is no outer project for a `.git` marker to confuse.

   `.meta/` is **not** part of that choice: it is always `<contribution_dir>/.meta`, a sibling of
   `data/`, in both layouts. `data/` holds user state and only user state.

   ## The portability contract

   **`contribution.json` + `data/` are the exportable state of a contribution.** Copy just those two
   to another machine--or sync them through an outer git repo, or a backup, or a zip file--run
   `repair()`, and you get a consistent working directory. Everything else is reconstructible from
   them plus the server.

   This is the principle that decides where any given piece of state lives, and it cuts sharply:

   - `contribution.json` and `data/` may contain **only** facts true of the contribution *wherever
     it is*. They travel.
   - `.meta/` holds facts true of *this checkout on this machine*. It does not travel, is gitignored,
     and is always rebuildable.

   The git-dir location is the second kind, which is why it lives in `.meta/contribution-meta.json`
   and not in the identity manifest. Two checkouts of the same contribution can legitimately disagree
   about it: exported from a standalone directory (`data/.git`) into a colleague's monorepo, the copy
   must come up external (`.meta/.contribution-git`), because an embedded `.git` would turn their
   project's own tracking inside out. A layout recorded in `contribution.json` would travel with the
   export and be *wrong on arrival*--which is exactly what versions through 1.0.x did.

   The same contract is why `_resolve_git_dir` can find an existing repository on disk rather than
   depending on the record: a freshly exported directory has no `.meta/` at all, and must still work.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import shutil
import tempfile
from datetime import datetime, timezone
from enum import Enum
from importlib.resources import files
from pathlib import Path

from ..client.client import CgClient
from ..client.common.protocol.contribution import (
    CgContribution,
    CgContributionData,
    CgContributionId,
    CgContributionModerator,
    CgPuzzleType,
    CgTestCase,
)
from ..client.common.protocol.typedefs import CgSolutionLanguage
from ..client.common.raw_client import compute_content_hash
from ..common.dataclass_wizard_x import CgEpochMillis
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
from .contribution_commit_data import (
    CONTRIBUTION_COMMIT_DATA_FILE_NAME,
    CgContributionCommitMetadata,
    redact_commit_contribution,
)
from .git_repo import CgGitError, CgGitRepo, init_repo, is_inside_existing_repo
from .layout import (
    ASSETS_SUBDIR_NAME,
    CONSTRAINTS_FILE_NAME,
    CONTRIBUTION_META_FILE_NAME,
    CONTRIBUTION_STATUS_CACHE_FILE_NAME,
    COVER_IMAGE_FILE_NAME,
    COVER_PLACEHOLDER_ASSET_NAME,
    DATA_GIT_DIR_NAME,
    DATA_SUBDIR_NAME,
    GIT_METADATA_SUBDIR_NAME,
    GITIGNORE_FILE_NAME,
    INPUT_DESCRIPTION_FILE_NAME,
    MAIN_BRANCH_NAME,
    META_SUBDIR_NAME,
    OUTPUT_DESCRIPTION_FILE_NAME,
    SELECTED_TEST_FILE_NAME,
    SERVER_BRANCH_NAME,
    SERVER_TAG_PREFIX,
    SOLUTION_FILE_STEM,
    SOLUTION_SNAPSHOT_FILE_NAME,
    STATEMENT_FILE_NAME,
    STUB_GENERATOR_FILE_NAME,
    TRAILER_CONTRIBUTION_ID,
    TRAILER_COVER_BINARY_HASH,
    TRAILER_COVER_BINARY_ID,
    TRAILER_VERSION,
    VERSION_DATA_BRANCH_NAME,
    VERSION_DATA_TAG_PREFIX,
    find_solution_file,
    solution_file_name,
)
from .schema import (
    CONTRIBUTION_DATA_FILE_NAME,
    CONTRIBUTION_IDENTITY_FILE_NAME,
    CONTRIBUTION_SCHEMA_VERSION,
    CgContributionIdentity,
    CgContributionMeta,
    CgContributionSelectedTest,
    CgContributionSolutionSnapshot,
    CgContributionStatusCache,
    CgContributionView,
)
from .test_cases_dir import (
    TESTS_SUBDIR_NAME,
    CgContributionLocalTestCase,
    commit_test_cases,
    import_test_cases,
    list_local_test_cases,
    renormalize_test_case_dirs,
)

__all__ = [
    "STATEMENT_FILE_NAME",
    "INPUT_DESCRIPTION_FILE_NAME",
    "OUTPUT_DESCRIPTION_FILE_NAME",
    "CONSTRAINTS_FILE_NAME",
    "STUB_GENERATOR_FILE_NAME",
    "SOLUTION_FILE_STEM",
    "find_solution_file",
    "solution_file_name",
    "COVER_IMAGE_FILE_NAME",
    "CgContributionManagerError",
    "CgRebaseStatus",
    "CgMergeStartStatus",
    "CgMergeStartResult",
    "CgContributionSyncStatus",
    "CgContributionStatus",
    "CgContributionLocalTestResult",
    "CgContributionLocalTestFailedError",
    "CgContributionManager",
]

logger = logging.getLogger(__name__)

_ACTIVE_VERSION_POLL_INTERVAL_SECONDS = 2.0
_ACTIVE_VERSION_POLL_MAX_ATTEMPTS = 10
"""See `CgContributionManager._refresh_active_version`--calibrated for the brief eventual-
   consistency lag confirmed live (caught up within a few seconds), not the much longer 524-
   timeout scenario `CgContributionServiceHelper` polls for (30s interval, unbounded by
   default)."""


class CgContributionManagerError(Exception):
    """Raised for contribution-manager-level errors not better represented by a more specific
       exception (e.g. attempting to `push()` without a `puzzle_type` set, or an operation that
       refuses because a merge is in progress)."""


class CgRebaseStatus(str, Enum):
    """The outcome of `CgContributionManager.rebase()`."""

    UP_TO_DATE = "up_to_date"
    """`server` hasn't advanced since `main` last synced with it (its tip already equals
       `git merge-base main server`)--nothing to do, regardless of whether `main`/the working
       directory have uncommitted edits."""

    FAST_FORWARDED = "fast_forwarded"
    """`server` advanced, but `main` had no edits since it last synced (`main`'s tip still equals
       the old merge-base)--fast-forward: `main` gets a new commit matching `server`'s new tip."""

    CONFLICT = "conflict"
    """Both `server` and `main` have diverged since they last synced--nothing was changed. Use
       `cg contribution diff` to inspect, and `cg contribution merge` to resolve."""


class CgMergeStartStatus(str, Enum):
    """The outcome of `CgContributionManager.merge_start()`."""

    STARTED = "started"
    """A real `git merge server` was attempted. If `text_conflicts`/`binary_conflicts` are both
       empty, it already completed (git commits automatically when there's nothing left
       unresolved)--`merge_in_progress` is already `False` again, no `merge_continue()` needed or
       possible. Otherwise, resolve the conflicts and run `merge_continue()`."""

    ALREADY_IN_PROGRESS = "already_in_progress"
    """`merge_start()` is idempotent--if a merge is already in progress (`MERGE_HEAD` exists), it
       leaves it completely untouched rather than erroring or restarting it."""

    UP_TO_DATE = "up_to_date"
    """`server`'s tip already equals `git merge-base main server`--nothing to merge. Consistent
       with `CgRebaseStatus.UP_TO_DATE`."""


@dataclasses.dataclass(frozen=True)
class CgMergeStartResult:
    """The outcome of `CgContributionManager.merge_start()`."""

    status: CgMergeStartStatus

    text_conflicts: tuple[str, ...] = ()
    """Relative paths where git left `<<<<<<<`-style conflict markers for manual resolution."""

    binary_conflicts: tuple[str, ...] = ()
    """Relative paths where both sides changed differently but the content isn't text--git's own
       default behavior for a binary conflict is to leave `main`'s (local) version as-is, no
       markers; pull `.git show server:<path>` (or `cg contribution git show server:<path>`) by
       hand if you want the server's version instead."""


class CgContributionSyncStatus(str, Enum):
    """Read-only classification of how `main` and `server` currently relate--see
       `CgContributionManager.status()`. Distinct from `CgRebaseStatus` (the *outcome of taking an
       action*): this describes the current state without changing anything, and distinguishes
       `LOCAL_AHEAD`/`SERVER_AHEAD` from each other, which `CgRebaseStatus` doesn't need to (it
       only cares whether `server` moved)."""

    NOT_PUSHED = "not_pushed"
    """`create()`d but never successfully `push()`d--no `server` branch exists at all yet."""

    UP_TO_DATE = "up_to_date"
    """`main` and `server` agree, and there are no uncommitted local edits either."""

    LOCAL_AHEAD = "local_ahead"
    """`main` has commits and/or uncommitted edits beyond the last sync point, but `server` hasn't
       moved--a plain `push()` would succeed with no conflict."""

    SERVER_AHEAD = "server_ahead"
    """`server` has moved since the last sync, but `main` hasn't changed--`cg contribution rebase`
       would fast-forward cleanly."""

    DIVERGED = "diverged"
    """Both sides have changed since they last synced--`cg contribution rebase`/`push()` would
       report a conflict; use `cg contribution merge` to resolve."""

    MERGE_IN_PROGRESS = "merge_in_progress"
    """A `cg contribution merge` is currently unresolved (`MERGE_HEAD` exists)--other sync-status
       classification doesn't apply until it's finished (`merge continue`) or `merge abort`ed."""


@dataclasses.dataclass(frozen=True)
class CgContributionStatus:
    """A point-in-time summary of a contribution working directory--see
       `CgContributionManager.status()`. Combines purely local facts (`sync_status`,
       `local_dirty`, `local_title`) with the last-known server state (`server`/
       `moderator_approvals`/`moderator_denials`/`status_cache_refreshed_at`), which is either
       served from `.meta/contribution-status.json` (cheap, no network access) or freshly
       re-fetched first, depending on `status(remote=...)`."""

    contribution_dir: Path
    """The working directory this status describes."""

    pushed: bool
    """Whether this working directory has ever been successfully `push()`d--i.e. whether
       `contribution_handle` is set. If False, `server`/`local_version` are always None and
       `sync_status` is always `NOT_PUSHED`."""

    contribution_handle: CgContributionId | None
    """The public handle this working directory tracks, or None if never pushed."""

    local_title: str
    """`data/contribution-data.json`'s current title, always available once imported/created,
       regardless of push/sync state."""

    local_dirty: bool
    """Whether the working tree currently differs from `main`'s tip (staged or unstaged)--False
       whenever `merge_in_progress` is True (not meaningful mid-merge)."""

    merge_in_progress: bool
    """Whether a `cg contribution merge` is currently unresolved."""

    sync_status: CgContributionSyncStatus
    """How `main` currently relates to `server`--see `CgContributionSyncStatus`."""

    local_version: int | None
    """The server version `main` last synced with (`server`'s tip's `Cg-Version` trailer), or None
       if never pushed. Not necessarily the server's *current* version unless `sync_status` is
       `UP_TO_DATE` or `LOCAL_AHEAD`--see `server.last_version.version` for that, when `server` is
       populated fresh (`status(remote=True)`)."""

    local_draft: bool
    """`data/contribution-data.json`'s `draft` flag--what's currently on disk (i.e. what the
       *next* `push()` would send), which may differ from `server.draft` if there are local edits
       not yet pushed. Always available once imported/created. Prefer this over `server.draft`
       for "what will be pushed"--`server` reflects the server's state as of the last fetch, not
       necessarily what's currently on disk here."""

    local_ready_for_moderation: bool
    """`data/contribution-data.json`'s `ready_for_moderation` flag--see `local_draft`'s
       docstring; same local-vs-server caveat applies to `server.ready_for_moderation`."""

    local_puzzle_type: CgPuzzleType | None
    """`data/contribution-data.json`'s `puzzle_type` (e.g. "PUZZLE_INOUT"), always available once
       imported/created--see `local_draft`'s docstring for why this (not `server.
       contribution_type`) is the one to use for "what will be pushed"."""

    local_solution_language: CgSolutionLanguage | None
    """`data/contribution-data.json`'s `data.solution_language` (e.g. "Python3")--the reference
       solution's language. May be None if a solution hasn't been provided yet. Same local-vs-
       server rationale as `local_puzzle_type`--this is versioned (content) state, changed only
       via `push()`, not part of `CgContributionStatusCache`'s non-versioned metadata."""

    local_difficulty: str | None
    """`data/contribution-data.json`'s `data.difficulty` (e.g. "easy"). May be None if not set
       yet. Same local-vs-server rationale as `local_puzzle_type`/`local_solution_language`--
       versioned content state, not part of `CgContributionStatusCache`."""

    server: CgContribution | None
    """The last-known full, unredacted contribution record from the server (from `.meta/
       contribution-status.json`'s `contribution` field--see `CgContributionStatusCache`), or
       None if never pushed or never fetched under a version of this package new enough to write
       that cache. Reflects the server's state as of `status_cache_refreshed_at`, which may lag
       behind local edits--see `local_draft`/`local_ready_for_moderation`/`local_puzzle_type`/
       `local_solution_language`/`local_difficulty` for what's actually on disk right now."""

    moderator_approvals: list[CgContributionModerator] | None
    """Moderators who had cast a `"validate"` (approve) vote on this contribution's privileged
       approve/reject moderation gate (`Contribution/findContributionModerators`) as of
       `status_cache_refreshed_at`--3 needed to publish. `None` under the same conditions as
       `server` (never pushed, or never fetched yet). Distinct from the ungated community vote
       (`server.up_votes`/`down_votes`)--never conflate the two."""

    moderator_denials: list[CgContributionModerator] | None
    """Moderators who had cast a `"deny"` (reject) vote as of `status_cache_refreshed_at`--see
       `moderator_approvals`'s docstring; 3 needed to reject."""

    status_cache_refreshed_at: datetime | None
    """When `server`/`moderator_approvals`/`moderator_denials` were captured (`.meta/
       contribution-status.json`'s own `refreshed_at`)--None exactly when those three are None.
       Always UTC."""


@dataclasses.dataclass(frozen=True)
class CgContributionLocalTestResult:
    """The outcome of running `data/solution.src` against one local `tests/` test case--see
       `CgContributionManager.run_local_test`."""

    ordinal: str
    """The test case's ordinal directory name (see `CgContributionLocalTestCase.ordinal`)."""

    side: str
    """Either `"local"` or `"validator"`."""

    title: str
    """The test case's real title."""

    passed: bool
    """In compare mode: whether the run completed without crashing/timing out and its stdout
       matched `expected_output`. In update mode: whether the run completed without crashing/
       timing out at all (a crashed/timed-out run is never used to overwrite `output.txt`--there's
       nothing good to accept as the new baseline)."""

    updated: bool
    """Whether `output.txt` was actually overwritten from this run (update mode only--always
       False in compare mode, and False even in update mode if the run crashed/timed out)."""

    input: str
    """The test case's input, exactly as fed to the solution's stdin."""

    expected_output: str
    """Compare mode: the test case's `output.txt` content as read before this run. Update mode:
       the same content this run just wrote to `output.txt` (i.e. `actual_output`)--so this field
       always means "whatever `output.txt` reads as immediately after this result", in both
       modes."""

    actual_output: str
    """What the solution actually wrote to stdout."""

    stderr: str
    """What the solution wrote to stderr (not itself a failure condition, but useful context when
       a test does fail)."""

    timed_out: bool
    """Whether the run was killed for exceeding its timeout rather than running to completion."""

    returncode: int
    """The subprocess's exit code (0 means it ran without crashing; meaningless--always -1--when
       `timed_out` is True, same as `CgLocalRunResult.returncode`). -1 when `exception` is set
       instead (the run never even got this far)."""

    exception: str | None = None
    """Set by a caller (not by `run_local_test` itself, which raises rather than returning a
       result if something goes genuinely wrong) when a batch runner catches and continues past an
       unexpected exception for this one test case--see `cg contribution play`."""


@dataclasses.dataclass(frozen=True)
class CgContributionSetLanguageResult:
    """The outcome of `CgContributionManager.set_language()`."""

    language: CgSolutionLanguage
    """The language now recorded in `data/contribution-data.json`."""

    previous_language: CgSolutionLanguage | None
    """What it was before (`None` if the contribution had no language set yet)."""

    wrote_stub: bool
    """True when a starter `data/solution.src` was written for the new language; False when that
       language has no stub to offer and `solution.src` was left *empty* for you to fill in (the
       same thing `create()` does for such a language).

       An empty file rather than a placeholder is correct, not a shortfall: it's sent as a null
       `solutionSource`, which makes `updateContribution` skip solution validation, whereas any
       non-null one must pass every test case--so a placeholder would block `push()`."""


class CgContributionLocalTestFailedError(CgContributionManagerError):
    """Raised by `CgContributionManager.run_local_test` callers (not by `run_local_test` itself,
       which reports one test at a time) to summarize a batch where at least one test case failed.
       Carries every result (not just the failing ones) via `.results`."""

    def __init__(self, results: list[CgContributionLocalTestResult]) -> None:
        self.results = results
        failed = [r for r in results if not r.passed]
        summary = ", ".join(f"{r.ordinal} {r.side} ({r.title})" for r in failed)
        super().__init__(f"{len(failed)}/{len(results)} local test case(s) failed: {summary}")


class CgContributionBuildFailedError(CgContributionManagerError):
    """Raised by `CgContributionManager.run_local_tests` when `build_solution()` failed, so no test
       case was run at all. Carries the full `CgBuildResult` (compiler diagnostics in
       `.result.output`) via `.result`.

       Note `build_solution()` itself does *not* raise this--it returns the result, so a caller
       driving the build directly can display diagnostics however it likes. This exists for the
       batch wrapper, which has no other way to say "nothing ran"."""

    def __init__(self, result: CgBuildResult) -> None:
        self.result = result
        super().__init__(f"solution failed to build:\n{result.output}")


def _normalize_solution(code: str | None) -> str:
    """Solution text for the "is this still the stub we generated?" comparison only.

       Ignores trailing newlines entirely, and maps `None` (no file) onto `""`. Note this is *not*
       needed to undo the on-disk form--`_read_sidecar` exactly inverts `_write_sidecar`, so an
       untouched stub already compares equal byte-for-byte. The tolerance is for the human case: an
       editor configured to trim or add trailing blank lines shouldn't turn "still the stub we
       generated" into "the author wrote a real solution here".

       Deliberately *more* forgiving than the null-solution test in `_read_local_data`, which is
       exact: the two answer different questions. "Has the user written anything real here?" can
       afford to ignore a stray newline; "should this be pushed as a null solutionSource?" must not,
       because it changes what the server is asked to validate."""
    return "" if code is None else code.rstrip("\n")


def _write_sidecar(path: Path, content: str | None) -> None:
    """Write a server-side text value to `path` in this client's on-disk form (see
       `common.text_files.server_text_to_file`), creating parent directories, or remove `path` if
       `content` is None.

       Exactly inverted by `_read_sidecar`, so a fetch/push cycle on an untouched file is the
       identity."""
    if content is None:
        if path.is_file():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(server_text_to_file(content), encoding="utf-8")


def _read_sidecar(path: Path) -> str | None:
    """Recover the server-side text value from `path` (see
       `common.text_files.file_to_server_text`), or None if it doesn't exist.

       Exactly inverts `_write_sidecar`."""
    if not path.is_file():
        return None
    return file_to_server_text(path.read_text(encoding="utf-8"))


def _ordinal_matches(requested: str, actual: str) -> bool:
    """Whether a user-supplied ordinal (e.g. `"1"`) refers to an on-disk ordinal directory (e.g.
       `"01"`)--exact string match always counts; if both sides are purely numeric, numeric
       equality counts too (so zero-padding doesn't have to be typed out), but a non-numeric
       ordinal like `"05a"` must be typed exactly."""
    if requested == actual:
        return True
    return requested.isdigit() and actual.isdigit() and int(requested) == int(actual)


def _align_solution_file_name(root_dir: Path, solution_language: str | None) -> None:
    """Make `data/solution.*` carry the extension for `solution_language`, renaming it if it
       doesn't already, and sweep away the `solution.<ext>` symlink older versions left at the
       working directory root.

       A language change is a rename, and deliberately so. The file has to carry its language's
       real extension--every tool that reads it dispatches on that--and cg used to get there with a
       fixed `data/solution.src` plus a symlink beside it, which cost a day of debugging when the
       debug info named one path and the editor resolved the other. One real file has no such gap.

       For a contribution this rename lands in `data/`, which is a git work tree, so `git` sees it
       on both `main` and the `server` mirror (which derives the same extension from the server's
       own `solutionLanguage`). That is the intended behaviour: git's similarity detection then
       does the right thing at both extremes--a Python solution replaced by a C++ one is
       dissimilar, so it surfaces as a structural add/delete conflict rather than a meaningless
       line-by-line merge of two languages, while a C solution edited into a C++ one is similar
       enough to be tracked as a rename and carry server-side edits across.

       Does nothing when there is no solution file yet; `create()` may run before the author has
       written anything."""
    data_dir = root_dir / DATA_SUBDIR_NAME
    # Older cg kept `data/solution.src` and a `solution.<ext>` symlink at the root. The symlink is
    # never right again, so remove it whatever it points at -- but only if it is a symlink, so a
    # real file a user put there is untouched.
    for stale in root_dir.glob(f"{SOLUTION_FILE_STEM}.*"):
        if stale.is_symlink():
            stale.unlink()

    extension = get_language(solution_language).extension if solution_language else None
    wanted = data_dir / solution_file_name(extension)
    existing = find_solution_file(data_dir, extension)
    if existing is None or existing == wanted:
        return
    if wanted.exists():
        # Two real solution files: refuse rather than clobber one. repair() reports it.
        return
    existing.rename(wanted)


def _minimal_valid_contribution_data(title: str) -> CgContributionData:
    """The smallest `CgContributionData` confirmed live to be accepted by `createContribution`--a
       title-only payload 500s (confirmed live 2026-07-29); the server needs a non-empty
       statement/difficulty and at least one test/validator pair too, nothing else. Two distinct
       uses: `create()`'s own local starting scaffold (layers a language-specific solution stub on
       top--see there), and `push()`'s throwaway, in-memory-only, never-written-to-disk stub
       contribution for a first push's `createContribution` call (title correct, everything else
       irrelevant--no cover, no solution--see `push()`'s docstring for why)."""
    return CgContributionData(
            title=title,
            statement="TODO: write the problem statement.",
            difficulty="easy",
            test_cases=[
                    CgTestCase(title="Test 1", test_in="1", test_out="1", is_test=True, is_validator=False, need_validation=True),
                    CgTestCase(title="Validator 1", test_in="1", test_out="1", is_test=False, is_validator=True, need_validation=True),
                ],
        )


STARTER_STUB_GENERATOR = """\
read n:int
write answer

INPUT
n: the single integer read from stdin

OUTPUT
A single line holding the answer.
"""
"""Stub generator seeded by `create()`, deliberately consistent with the seeded test pair.

   The stub generator is the one seeded file that isn't inert: CodinGame runs it to produce the
   starter code every solver of this puzzle begins from, so one that disagrees with the test cases
   hands them a program that reads the wrong thing. `_minimal_valid_contribution_data` seeds a
   single test/validator pair of `test_in="1"`/`test_out="1"`--one line, one integer--so this reads
   exactly that: one `int` on one line.

   `write answer` emits a placeholder output line, which is the convention for generated stubs; the
   solver replaces it. The `INPUT`/`OUTPUT` blocks become explanatory comments in the generated
   code.

   Syntax reference: https://github.com/CodinGame/codingame-game-engine/blob/master/stubGeneratorSyntax.md
   Types are `int`, `float`, `long`, `word(<length>)`, `string(<length>)`; `loop`/`loopline`/
   `gameloop` handle repeated input. Keep this in step with the seeded test cases if either
   changes--nothing else checks that they agree."""


def _cover_placeholder_bytes() -> bytes:
    """The seeded "under construction" cover image, read from package data.

       Deliberately loud: `push()` uploads whatever is in `data/cover.png`, so this is the one
       seeded placeholder that becomes *visible*. A tasteful title card would sail past its author
       unnoticed and end up published; this can't. New contributions are private drafts, so nobody
       else sees it in the meantime.

       Regenerate with `bin/gen-default-cover-image`--see `scripts/gen_cover_placeholder.py`."""
    # One component per joinpath() call: Traversable.joinpath is single-argument in the typeshed
    # signature, and multi-argument support only arrived in 3.11.
    return (files("codingame_tools.contribution_manager")
            .joinpath(ASSETS_SUBDIR_NAME)
            .joinpath(COVER_PLACEHOLDER_ASSET_NAME)
            .read_bytes())


def _starter_contribution_data(title: str) -> CgContributionData:
    """`create()`'s starting scaffold: the minimal server-accepted payload, plus a placeholder for
       every remaining editable field.

       Seeding them all means `cg contribution create` produces a working directory where every file
       an author edits already exists--so they can be listed, opened, and diffed, rather than the
       author having to know which filenames to conjure. They're placeholders to be replaced, not
       content: the statement says so.

       All of it is deliberately self-consistent, describing the trivial echo puzzle that the seeded
       test pair and Python starter solution already implement--input description, output
       description, constraints and stub generator included. A scaffold whose parts contradict each
       other is worse than none, because the contradiction is only discovered later, by the server,
       as a validation failure."""
    return dataclasses.replace(
            _minimal_valid_contribution_data(title),
            statement=(
                "TODO: write the problem statement.\n"
                "\n"
                "This is placeholder scaffolding. The seeded test case, reference solution, stub "
                "generator and descriptions all describe the same trivial \"read one integer, "
                "print it back\" puzzle, so that they agree with each other until you replace "
                "them. Replace all of it."
            ),
            input_description="Line 1: An integer [[n]].",
            output_description="A single line containing [[n]], unchanged.",
            constraints="0 \u2264 [[n]] \u2264 1000",
            stub_generator=STARTER_STUB_GENERATOR,
        )


def _write_meta_gitignore(contribution_dir: Path) -> None:
    """Write `<contribution_dir>/.gitignore` containing `.meta/`, so `.meta/`'s contents (this
       client's generated state--see `META_SUBDIR_NAME`) can never end up tracked by whatever outer
       project comes to track the working directory, now or later.

       Always `<contribution_dir>`, in both git-dir layouts, because that is where `.meta/` always
       is. Note it protects `.meta/` from the *outer* project only: the contribution's own repo has
       `data/` as its work tree and cannot see `.meta/` at all."""
    (contribution_dir / GITIGNORE_FILE_NAME).write_text(f"{META_SUBDIR_NAME}/\n")


def _materialize_data(
            target_dir: Path,
            *,
            puzzle_type: CgPuzzleType,
            draft: bool,
            ready_for_moderation: bool,
            data: CgContributionData,
            cover_bytes: bytes | None,
        ) -> CgContributionView:
    """Write one view's content directly into `target_dir`: sidecar text files, `solution.src`,
       `cover.png`, `tests/`, `contribution-data.json`. `target_dir` is whatever the caller
       wants--`self.data_dir` (the real working tree) for `import_()`, or a throwaway staging
       directory for `fetch()`'s `server` tree-building (see `CgGitRepo.write_tree_from_dir`)--this
       function itself has no opinion about which.

       Everything written here is contribution content, and nothing else. That is what makes the
       `server` branch a faithful mirror of what CodinGame holds, and it is why `.meta/` lives
       outside `data/`: back when the git-dir could sit at `data/.meta/`, every tree committed onto
       `server` had to carry a synthetic `.gitignore` protecting it, or a `checkout_all()` landing
       on a `.gitignore`-less commit followed by `git clean -fd` would delete the git-dir out from
       under the repo. With the embedded layout at `data/.git`, git excludes its own git-dir
       inherently and no such special case exists.

    Returns:
        The `CgContributionView` that was written to `target_dir/contribution-data.json`.
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    _write_sidecar(target_dir / STATEMENT_FILE_NAME, data.statement)
    _write_sidecar(target_dir / INPUT_DESCRIPTION_FILE_NAME, data.input_description)
    _write_sidecar(target_dir / OUTPUT_DESCRIPTION_FILE_NAME, data.output_description)
    _write_sidecar(target_dir / CONSTRAINTS_FILE_NAME, data.constraints)
    _write_sidecar(target_dir / STUB_GENERATOR_FILE_NAME, data.stub_generator)
    # The solution file is always written, empty when there is no solution--never removed. An
    # empty file is this client's representation of a null solutionSource (see _read_local_data)
    # and gives the author something to type into.
    #
    # Its extension comes from this view's own solution_language, which is what keeps the `server`
    # mirror and the working tree naming the same file: both derive it from the same field, so a
    # language change moves them together and git sees one rename rather than two unrelated trees.
    _solution_extension = (
            get_language(data.solution_language).extension if data.solution_language else None)
    _write_sidecar(target_dir / solution_file_name(_solution_extension), data.solution or "")

    cover_path = target_dir / COVER_IMAGE_FILE_NAME
    if cover_bytes is not None:
        cover_path.write_bytes(cover_bytes)
    elif cover_path.is_file():
        cover_path.unlink()

    import_test_cases(data.test_cases, target_dir / TESTS_SUBDIR_NAME)

    working_data = CgContributionData(
            title=data.title,
            difficulty=data.difficulty,
            topics=data.topics,
            solution_language=data.solution_language,
        )
    view = CgContributionView(
            puzzle_type=puzzle_type,
            draft=draft,
            ready_for_moderation=ready_for_moderation,
            data=working_data,
        )
    view.save(target_dir / CONTRIBUTION_DATA_FILE_NAME)
    return view


def _read_local_data(data_dir: Path, working_data: CgContributionData) -> tuple[CgContributionData, bytes | None]:
    """Read the real content files (sidecar text files, `solution.src`, `tests/`, `cover.png`)
       currently in `data_dir` into a full `CgContributionData`--merging in `working_data`'s
       non-file-backed fields (`title`/`difficulty`/`topics`/`solution_language`)--and the current
       `cover.png` bytes, if any. `cover_binary_id` is left `None`; resolving it (network/hash-
       reuse) is `push()`'s job, not this function's."""
    # An *empty* solution.src means "no reference solution", and is sent as a null solutionSource.
    # `updateContribution` skips solution validation entirely for null, but validates any non-null
    # value against every test case (see CgLanguage.build_contribution_create_stub_source). Spelling
    # "none" as an empty file rather than a missing one is what lets this client always keep the
    # file present, so the `solution.<ext>` symlink resolves and there's something to type into.
    #
    # The test is on the *decoded* value, so both a zero-length file and a file containing exactly
    # one newline count--the one place `common.text_files`' conversion isn't injective, landing here
    # as a feature: an editor with "insert final newline" enabled can't turn "no reference solution"
    # into "a solution consisting of one blank line" behind the author's back.
    #
    # Nothing weaker than that, though. A whitespace-only file is a real (broken) program and is
    # pushed as one, for the server to reject--rather than being silently reinterpreted as "no
    # solution at all".
    #
    # This does conflate a server-side solution that is genuinely `""` with a null one. Accepted
    # deliberately: an empty program passes no test cases, so no contribution could have been
    # accepted with one.
    solution_path = find_solution_file(data_dir)
    solution = _read_sidecar(solution_path) if solution_path is not None else None
    if solution == "":
        solution = None
    cover_path = data_dir / COVER_IMAGE_FILE_NAME
    cover_bytes = cover_path.read_bytes() if cover_path.is_file() else None
    data = dataclasses.replace(
            working_data,
            statement=_read_sidecar(data_dir / STATEMENT_FILE_NAME),
            input_description=_read_sidecar(data_dir / INPUT_DESCRIPTION_FILE_NAME),
            output_description=_read_sidecar(data_dir / OUTPUT_DESCRIPTION_FILE_NAME),
            constraints=_read_sidecar(data_dir / CONSTRAINTS_FILE_NAME),
            stub_generator=_read_sidecar(data_dir / STUB_GENERATOR_FILE_NAME),
            solution=solution,
            test_cases=commit_test_cases(data_dir / TESTS_SUBDIR_NAME),
            cover_binary_id=None,
        )
    return data, cover_bytes


def _metadata_to_trailers(metadata: CgContributionCommitMetadata) -> dict[str, str]:
    trailers = {
            TRAILER_CONTRIBUTION_ID: metadata.contribution_id,
            TRAILER_VERSION: str(metadata.version),
        }
    if metadata.cover_binary_id is not None:
        trailers[TRAILER_COVER_BINARY_ID] = str(metadata.cover_binary_id)
    if metadata.cover_binary_hash is not None:
        trailers[TRAILER_COVER_BINARY_HASH] = metadata.cover_binary_hash
    return trailers


def _trailers_to_metadata(trailers: dict[str, str]) -> CgContributionCommitMetadata:
    return CgContributionCommitMetadata(
            contribution_id=trailers.get(TRAILER_CONTRIBUTION_ID, ""),
            version=int(trailers.get(TRAILER_VERSION, "0")),
            cover_binary_id=int(trailers[TRAILER_COVER_BINARY_ID]) if TRAILER_COVER_BINARY_ID in trailers else None,
            cover_binary_hash=trailers.get(TRAILER_COVER_BINARY_HASH),
        )


def _looks_like_text(content: bytes) -> bool:
    """Same heuristic git and most other tools use: binary content almost always contains a NUL
       byte within the first few KB; text content essentially never does."""
    return b"\x00" not in content[:8192]


class CgContributionManager:
    """Builds/updates a contribution working directory (`contribution_dir`) against the server,
       via an already-authenticated `CgClient`. See the module docstring for the git repo
       this is backed by."""

    contribution_dir: Path
    client: CgClient

    mount_root: Path | None
    """Editor workspace root to bind-mount for containerized languages, or `None` to derive it (see
       `language_context`). Normally VS Code's `${workspaceFolder}`, passed through by the CLI: cg's
       own `find_workspace_root` is a heuristic, and the editor knows the real answer."""

    def __init__(
                self,
                contribution_dir: Path | str,
                client: CgClient,
                *,
                toolchain_dir: Path | None = None,
                mount_root: Path | None = None,
                toolchain_languages: list[str] | None = None,
                toolchain_image: str | None = None,
            ) -> None:
        # Always resolved to an absolute path: git_repo.py's subprocess calls set `cwd` to
        # `git_dir`/`work_tree` themselves (see CgGitRepo._run), so a relative `contribution_dir`
        # here would make `--git-dir=`/`--work-tree=` (built from it) resolve against the *wrong*
        # cwd inside git's own subprocess--not the caller's original cwd--confirmed live (`cg
        # contribution import ... contribution` from a repo root failed `git init` outright, since
        # the relative `--git-dir=contribution/.meta/...` was interpreted relative to
        # `contribution/data`, not the original cwd).
        self.contribution_dir = Path(contribution_dir).resolve()
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
        """Path to this working directory's `contribution.json` (global identity) manifest."""
        return self.contribution_dir / CONTRIBUTION_IDENTITY_FILE_NAME

    @property
    def data_dir(self) -> Path:
        """Path to this working directory's `data/` subdirectory--`main`'s git working tree."""
        return self.contribution_dir / DATA_SUBDIR_NAME

    @property
    def contribution_data_file(self) -> Path:
        return self.data_dir / CONTRIBUTION_DATA_FILE_NAME

    @property
    def tests_dir(self) -> Path:
        return self.data_dir / TESTS_SUBDIR_NAME

    @property
    def solution_file(self) -> Path:
        """The one real solution file, `data/solution.<ext>`.

           Resolved by looking for whatever is actually there rather than by deriving the name from
           the recorded language: the two can disagree--a working directory written by an older cg
           still has `solution.src`, and a fetch can change the language before the rename runs--and
           the file that exists is the one the user has been editing."""
        found = find_solution_file(self.data_dir)
        return found if found is not None else self.data_dir / solution_file_name(None)

    @property
    def meta_dir(self) -> Path:
        """This working directory's `.meta/`--always `<contribution_dir>/.meta`, in both git-dir
           layouts (see the module docstring). Never inside `data/`, which holds user state only.

           A plain path join, so it **never raises** and needs no `contribution.json` to answer.
           That matters because `language_context()` needs a meta dir and must stay infallible--`cg
           contribution play` works today on a directory holding nothing but
           `data/contribution-data.json`, and that must keep working."""
        return self.contribution_dir / META_SUBDIR_NAME

    @property
    def meta_file(self) -> Path:
        """Path to `.meta/contribution-meta.json`--see `CgContributionMeta`."""
        return self.meta_dir / CONTRIBUTION_META_FILE_NAME

    def load_meta(self) -> CgContributionMeta | None:
        """This working directory's `.meta/contribution-meta.json`, or `None` if it doesn't exist
           (never created, or `.meta/` was deleted) or fails to parse.

           Unparseable is treated as absent rather than fatal, like every other `.meta/` file: the
           whole directory is disposable and rebuildable, and `git_dir` can find the repository on
           disk without it."""
        if not self.meta_file.is_file():
            return None
        try:
            return CgContributionMeta.load(self.meta_file)
        except Exception:
            logger.warning("Failed to parse %s--treating as absent.", self.meta_file, exc_info=True)
            return None

    def _save_meta(self, git_dir: Path) -> None:
        """Record where this working directory's git-dir is, as a root-relative POSIX path."""
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        relative = git_dir.relative_to(self.contribution_dir).as_posix()
        CgContributionMeta(git_repo=relative).save(self.meta_file)
        self._drop_legacy_identity_meta()

    def _drop_legacy_identity_meta(self) -> None:
        """Remove `gitDirInData` from `contribution.json` once the same fact lives in `.meta/`.

           Versions through 1.0.x kept the git-dir location in the identity manifest. Nothing reads
           it any more--`_resolve_git_dir` finds an existing repository on disk, which gets a 1.0.x
           working directory to the right answer without it--but `CatchAll` faithfully round-trips
           unknown keys, so it would otherwise sit there forever implying it still means something.

           Only ever *removes* a key this package used to own, and only once its replacement has
           been written; any other unrecognized key is left alone."""
        identity = self.load_identity()
        if identity is None or not identity.extra_data or "gitDirInData" not in identity.extra_data:
            return
        identity.extra_data = {k: v for k, v in identity.extra_data.items() if k != "gitDirInData"}
        identity.save(self.identity_file)

    def _git_dir_for(self, git_dir_in_data: bool) -> Path:
        """The git-dir the given layout puts this working directory's repo in--see the module
           docstring for the two, and `git_dir` for reading the layout actually in force."""
        if git_dir_in_data:
            return self.data_dir / DATA_GIT_DIR_NAME
        return self.meta_dir / GIT_METADATA_SUBDIR_NAME

    def _resolve_git_dir(self) -> Path:
        """Where this working directory's git-dir is, in decreasing order of authority:

           1. **`.meta/contribution-meta.json`**, written at `create()`/`import_()` time. The normal
              answer, and the reason no command has to go looking.
           2. **An existing repository on disk**, at either of the two known locations. `.meta/` is
              disposable--deleting it and repairing is documented as always valid--so the recorded
              answer must never be the *only* copy. Probing keeps a `data/.git`, which survives
              losing `.meta/`, from being orphaned and then shadowed by a second, empty repository.
              Refuses if both exist, rather than picking one and abandoning the other's history.
           3. **Derived** from whether this directory sits inside an existing git project, the same
              rule `create()` applies. Only reached when there is no repository yet.

           Ordering 1 above 3 is what keeps a git project appearing around this directory *after*
           creation from relocating a repository that already exists at a fixed spot; ordering 2
           above 3 covers the same hazard when the record itself is gone.

        Raises:
            CgContributionManagerError: if repositories exist at both locations.
        """
        recorded = self.load_meta()
        if recorded is not None:
            return self.contribution_dir / Path(recorded.git_repo)

        external = self._git_dir_for(False)
        embedded = self._git_dir_for(True)
        found = [path for path in (external, embedded) if path.is_dir()]
        if len(found) > 1:
            raise CgContributionManagerError(
                    f"Found git-dirs at both {external} and {embedded}, and {self.meta_file} is "
                    "missing, so there is nothing to say which one is this working directory's. "
                    "Refusing to guess--inspect both (`git --git-dir=<path> log --oneline main`) "
                    "and delete the one you don't want."
                )
        if found:
            return found[0]
        return self._git_dir_for(not is_inside_existing_repo(self.contribution_dir))

    @property
    def git_dir(self) -> Path:
        """Path to this working directory's git-dir--see `_resolve_git_dir` for how it is found and
           the module docstring for the two places it can be.

        Raises:
            FileNotFoundError: if this working directory has never been imported--nothing to
                                derive it from.
        """
        if self.load_identity() is None:
            raise FileNotFoundError(f"{self.identity_file} does not exist--this working directory has never been imported.")
        self._reject_legacy_layout()
        return self._resolve_git_dir()

    def _reject_legacy_layout(self) -> None:
        """Refuse to operate on a working directory laid out by a pre-1.1 version of this package.

           Those put `.meta/` inside `data/` whenever the git-dir was in `data/`, so the git-dir
           landed at `data/.meta/.contribution-git/` rather than today's `data/.git/`. Left
           undetected, `git_dir` would simply point at a path that doesn't exist and the next
           `repair()` would cheerfully initialize a *second*, empty repo beside the real one--
           silently abandoning the local history in the first.

           There is no automatic migration: relocating a git-dir means rewriting the absolute
           `core.worktree` it was initialized with, and for a never-pushed contribution that repo
           holds the only copy of the local history. Better to say so and let the user decide.

        Raises:
            CgContributionManagerError: if the legacy layout is present.
        """
        legacy_git_dir = self.data_dir / META_SUBDIR_NAME / GIT_METADATA_SUBDIR_NAME
        if not legacy_git_dir.is_dir():
            return
        raise CgContributionManagerError(
                f"{legacy_git_dir} is a git-dir in the layout used before codingame-tools 1.1, "
                f"which put .meta/ inside data/. It is now always {self.meta_dir}, and a git-dir "
                f"inside data/ is now an ordinary {self.data_dir / DATA_GIT_DIR_NAME}.\n"
                "\n"
                "To migrate, from the working directory root:\n"
                f"  git --git-dir={legacy_git_dir} --work-tree={self.data_dir} log --oneline main\n"
                "    -- review anything local you would lose (nothing, if every version was pushed)\n"
                f"  rm -rf {self.data_dir / META_SUBDIR_NAME}\n"
                "  cg contribution repair\n"
                "\n"
                "`repair()` re-fetches from the server for a pushed contribution, or re-commits "
                "data/ as it stands for one that was never pushed. Either way data/ itself--your "
                "actual content--is left untouched; only the git history is rebuilt."
            )

    @property
    def status_cache_file(self) -> Path:
        """Path to `.meta/contribution-status.json` (see `CgContributionStatusCache`). Like
           `meta_dir`, and unlike `git_dir`, this never raises--nothing about its location depends
           on which git-dir layout is in force."""
        return self.meta_dir / CONTRIBUTION_STATUS_CACHE_FILE_NAME

    @property
    def git_repo(self) -> CgGitRepo:
        return CgGitRepo(self.git_dir, self.data_dir)

    @property
    def merge_in_progress(self) -> bool:
        return self.git_repo.merge_head_exists()

    def server_metadata(self) -> CgContributionCommitMetadata | None:
        """The `CgContributionCommitMetadata` (version, cover info) at `server`'s current tip, or
           None if this working directory has never been imported. Public specifically so the CLI
           can display version numbers without reaching into `git_repo`/trailer-parsing details."""
        server_sha = self.git_repo.resolve_ref(SERVER_BRANCH_NAME)
        if server_sha is None:
            return None
        return _trailers_to_metadata(self.git_repo.read_trailers(server_sha))

    # --- identity / view load-save -------------------------------------------------------------

    def load_identity(self) -> CgContributionIdentity | None:
        """Load `contribution.json`, or None if this directory has never been imported."""
        if not self.identity_file.is_file():
            return None
        return CgContributionIdentity.load(self.identity_file)

    def load(self) -> CgContributionView:
        """Load `data/contribution-data.json`.

        Raises:
            FileNotFoundError: if this working directory hasn't been imported/initialized yet.
        """
        return CgContributionView.load(self.contribution_data_file)

    def save(self, view: CgContributionView) -> None:
        """Write `view` back to `data/contribution-data.json`, creating `data/` if needed."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        view.save(self.contribution_data_file)

    def _save_identity(self, contribution_id: CgContributionId | None) -> None:
        """Write `contribution.json` if it doesn't already exist (never overwrites--identity is
           constant for a working directory's lifetime, except `contribution_handle` itself--see
           `_write_contribution_handle`). `contribution_id=None` for `create()`'s brand new,
           never-yet-`push()`d working directories."""
        if self.identity_file.is_file():
            return
        self.contribution_dir.mkdir(parents=True, exist_ok=True)
        CgContributionIdentity(
                schema_version=CONTRIBUTION_SCHEMA_VERSION, contribution_handle=contribution_id,
            ).save(self.identity_file)

    def _write_contribution_handle(self, contribution_id: CgContributionId | None) -> None:
        """Overwrite `contribution.json`'s `contribution_handle`, the one field that isn't
           constant for a working directory's whole lifetime: set (from `None`) by `push()`, the
           first time a `create()`d working directory successfully reaches the server via
           `createContribution`; reset back to `None` by `delete(keep_local=True)`, once the
           contribution it pointed at no longer exists server-side."""
        identity = self.load_identity()
        assert identity is not None
        dataclasses.replace(identity, contribution_handle=contribution_id).save(self.identity_file)

    # --- server-branch commit helper (shared by import_/fetch/commit) --------------------------

    def _record_server_commit(
                self, repo: CgGitRepo, tree: str, contribution: CgContribution,
                cover_bytes: bytes | None, message: str,
            ) -> str:
        """Commit `tree` onto `server` (trailers + `server.<version>` tag), and record the
           corresponding `version-data` commit alongside it. Returns the new `server` commit SHA.
        """
        version = contribution.last_version
        metadata = CgContributionCommitMetadata(
                contribution_id=contribution.public_handle, version=version.version,
                cover_binary_id=version.data.cover_binary_id,
                cover_binary_hash=compute_content_hash(cover_bytes) if cover_bytes is not None else None,
            )
        parent = repo.resolve_ref(SERVER_BRANCH_NAME)
        parents = [parent] if parent is not None else []
        sha = repo.commit_tree(tree, parents, message, trailers=_metadata_to_trailers(metadata))
        repo.update_ref(f"refs/heads/{SERVER_BRANCH_NAME}", sha)
        repo.tag(f"{SERVER_TAG_PREFIX}{version.version}", sha)

        redacted = redact_commit_contribution(contribution)
        blob = repo.write_blob((redacted.saves() + "\n").encode("utf-8"))
        vd_tree = repo.write_tree_single_file(CONTRIBUTION_COMMIT_DATA_FILE_NAME, blob)
        vd_parent = repo.resolve_ref(VERSION_DATA_BRANCH_NAME)
        vd_parents = [vd_parent] if vd_parent is not None else []
        vd_sha = repo.commit_tree(vd_tree, vd_parents, message)
        repo.update_ref(f"refs/heads/{VERSION_DATA_BRANCH_NAME}", vd_sha)
        repo.tag(f"{VERSION_DATA_TAG_PREFIX}{version.version}", vd_sha)

        return sha

    # --- status cache (non-version-tied server metadata; shared by import_/repair/fetch) -------

    async def _refresh_status_cache(self, contribution: CgContribution) -> None:
        """Fetch the moderator approve/reject vote lists and write `.meta/contribution-status.
           json` (`CgContributionStatusCache`)--called every time `import_()`/`repair()`/`fetch()`
           obtain a fresh `CgContribution` via `findContribution`, regardless of whether the
           content version changed, since none of `CgContributionStatusCache`'s fields are tied to
           it. Deliberately NOT called from `push()`--`updateContribution`'s response is a fresh
           `CgContribution` too, but refreshing the moderator vote lists on every push would be two
           extra live calls on a path that's already the heaviest one in this class; `fetch()`
           (`cg contribution fetch`, or `status(remote=True)`/`rebase()`, which both call it) is
           the deliberate, cheap-by-default place for this."""
        moderator_approvals = await self.client.services.contribution.find_contribution_moderators(
                contribution.id, "validate")
        moderator_denials = await self.client.services.contribution.find_contribution_moderators(
                contribution.id, "deny")
        cache = CgContributionStatusCache(
                version=contribution.last_version.version,
                contribution=contribution,
                moderator_approvals=moderator_approvals,
                moderator_denials=moderator_denials,
                _refreshed_at=CgEpochMillis.upcast(datetime.now(timezone.utc)),
            )
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        cache.save(self.status_cache_file)

    def read_status_cache(self) -> CgContributionStatusCache | None:
        """Load `.meta/contribution-status.json` (see `_refresh_status_cache`), or None if it
           doesn't exist yet (never `fetch()`ed/`import_()`ed under a version of this package new
           enough to write it) or fails to parse (opportunistic cache, same self-healing spirit as
           the cover-image reuse in `fetch()`--corrupt/unreadable is treated as absent, not fatal).
        """
        path = self.status_cache_file
        if not path.is_file():
            return None
        try:
            return CgContributionStatusCache.load(path)
        except Exception:
            logger.warning("Failed to parse %s--treating as absent.", path, exc_info=True)
            return None

    # --- import_ / commit ----------------------------------------------------------------------

    async def import_(
                self,
                contribution_id: CgContributionId,
                *,
                contribution: CgContribution | None = None,
            ) -> CgContributionView:
        """Build this working directory from an existing server-side contribution:
           `findContribution` (unless `contribution` is already given), downloading the cover
           image if one is set, then initializing the git repo with a single shared root commit
           on both `main` and `server` (so `git merge-base main server` starts out meaningful),
           plus the corresponding `version-data` commit. Writes `contribution.json` if this is a
           fresh working directory, and `.meta/contribution-meta.json` either way (repair mode runs
           precisely when `.meta/` went missing, so the record needs rewriting there too).

           Also doubles as one of `repair()`'s two modes: if `contribution.json` and `data/`
           already exist (e.g. from cloning an outer project that tracks them, or a corrupted/
           manually-deleted git-dir) but the git-dir itself is missing, this re-runs the same
           initialization *without* overwriting `data/`'s already-on-disk content for `main`--only
           `server`/`version-data` are seeded fresh from the current server state. There's no
           attempt to reconstruct the *true* historical sync point in this case--nothing durable
           survives to reconstruct it from; `main` and `server` simply start sharing a root again,
           from right now. See `repair()`'s docstring for the other mode (no `contribution_handle`
           yet at all--this method doesn't handle that one, since it always needs a real
           `contribution_id` to fetch).

        Raises:
            CgContributionManagerError: if this directory already tracks a *different*
                                         contribution, or already has a git repository.
        """
        identity = self.load_identity()
        if identity is not None and identity.contribution_handle != contribution_id:
            raise CgContributionManagerError(
                    f"{self.identity_file} already tracks contribution "
                    f"{identity.contribution_handle!r}; refusing to import {contribution_id!r} "
                    "into the same directory."
                )
        repairing = identity is not None
        self._reject_legacy_layout()
        git_dir = self._resolve_git_dir()
        if git_dir.is_dir():
            raise CgContributionManagerError(
                    f"{git_dir} already exists--this working directory has already been imported "
                    "(see `cg contribution git` to inspect it directly)."
                )

        if contribution is None:
            contribution = await self.client.services.contribution.find_contribution(contribution_id)
        version = contribution.last_version
        data = version.data

        cover_bytes: bytes | None = None
        if data.cover_binary_id is not None:
            download = await self.client.servlets.file_servlet(data.cover_binary_id)
            cover_bytes = download.content

        if not repairing:
            _materialize_data(
                    self.data_dir,
                    puzzle_type=contribution.contribution_type,
                    draft=version.draft if version.draft is not None else True,
                    ready_for_moderation=version.ready_for_moderation if version.ready_for_moderation is not None else False,
                    data=data,
                    cover_bytes=cover_bytes,
                )
            _write_meta_gitignore(self.contribution_dir)
            self._save_identity(contribution_id)
            _align_solution_file_name(self.contribution_dir, data.solution_language)

        if repairing:
            # Unlike the fresh-import path above (which built data/ itself, via
            # _materialize_data()--always normalized), repairing snapshots whatever's already
            # on disk, preserved as-is from the outer clone. If that on-disk tests/ layout isn't
            # already in the canonical ordinal-dirname form (e.g. it came from an older tool, or
            # from local edits that inserted/reordered directories), this commit's tree would
            # permanently encode that non-canonical layout--and a later fetch()/import_() (always
            # canonical, via _materialize_data()) would then show a spurious diff/conflict against
            # it even when the actual test content never changed. See push()/merge_continue()
            # for the same concern at those other points content ever gets committed from
            # whatever's on disk.
            renormalize_test_case_dirs(self.tests_dir)

        self._save_meta(git_dir)  # both paths: repair mode runs when .meta/ went missing
        init_repo(git_dir, self.data_dir)
        repo = CgGitRepo(git_dir, self.data_dir)
        repo.set_head(MAIN_BRANCH_NAME)

        tree = repo.write_tree_from_worktree()
        message = "Repair from server" if repairing else "Import from server"
        server_sha = self._record_server_commit(repo, tree, contribution, cover_bytes, f"{message} (version {version.version})")
        repo.reset_index_to(server_sha)

        await self._refresh_status_cache(contribution)

        return self.load()

    async def repair(self) -> CgContributionView:
        """Reconstruct this working directory's git-dir from scratch, without disturbing `data/`'s
           already-on-disk content--for recovering from a missing or corrupted `.meta/`/git-dir
           (e.g. an outer project clone that deliberately didn't bring the git-dir along--see the
           module docstring--or the git-dir having been manually deleted/corrupted).

           Two modes, chosen automatically from `contribution.json`'s `contribution_handle`:

           - Set (this working directory has already been `push()`d/`import_()`d before): re-bases
             off the server--delegates to `import_()`'s own repair mode, which re-fetches current
             server state fresh to seed `server`/`version-data`'s first commit, while `main`'s
             first commit is built from `data/`'s current on-disk content, preserved as-is. No
             attempt is made to reconstruct the *true* historical sync point--nothing durable
             survives to reconstruct it from.
           - Not set (this working directory was `create()`d but never successfully `push()`d):
             purely local, no network access at all--just re-establishes `main`'s initial commit
             from `data/`'s current on-disk content, the same way `create()` itself would, but
             preserving what's already there instead of overwriting it with placeholder content.
             No `server`/`version-data` branches are created; there's no server-side contribution
             yet to base them on.

        Raises:
            FileNotFoundError: if this working directory has never been created/imported at all
                                (no `contribution.json`), or if `data/` itself is missing (nothing
                                on disk to repair/preserve).
            CgContributionManagerError: if the git-dir already exists (nothing to repair).
        """
        identity = self.load_identity()
        if identity is None:
            raise FileNotFoundError(
                    f"{self.identity_file} does not exist--nothing to repair (this working "
                    "directory has never been created/imported)."
                )
        if identity.contribution_handle is not None:
            return await self.import_(identity.contribution_handle)

        # Never pushed--purely local reconstruction, no network access, mirroring create()'s own
        # git-init/commit steps but preserving data/'s current on-disk content instead of
        # overwriting it with placeholder content.
        self._reject_legacy_layout()
        git_dir = self._resolve_git_dir()
        if git_dir.is_dir():
            raise CgContributionManagerError(f"{git_dir} already exists--nothing to repair.")
        if not self.data_dir.is_dir():
            raise FileNotFoundError(f"{self.data_dir} does not exist--nothing to repair from.")

        _write_meta_gitignore(self.contribution_dir)
        renormalize_test_case_dirs(self.tests_dir)  # see import_()'s repair mode for why
        # Before the first commit, so the rename lands in the initial tree rather than showing up
        # as an uncommitted change immediately afterwards. This is also the migration path for a
        # working directory an older cg left holding `data/solution.src` plus a root symlink.
        _align_solution_file_name(self.contribution_dir, self.load().data.solution_language)

        self._save_meta(git_dir)
        init_repo(git_dir, self.data_dir)
        repo = CgGitRepo(git_dir, self.data_dir)
        repo.set_head(MAIN_BRANCH_NAME)
        repo.commit_worktree("Initial local content (repaired, not yet pushed to the server)")

        return self.load()

    async def create(
                self,
                *,
                title: str,
                puzzle_type: CgPuzzleType = "PUZZLE_INOUT",
                language: str = "Python3",
            ) -> CgContributionView:
        """Initialize a brand new, *purely local* contribution working directory--no network
           access at all (`async` only for interface consistency with every other method here),
           and deliberately so: no server-side contribution exists yet, matching
           how `git init` never touches a remote either. `contribution.json`'s
           `contribution_handle` is left `None`; the first successful `push()` fills it in, via
           `createContribution` instead of the usual `updateContribution`--see `push()`'s
           docstring for the full create-vs-update story.

           Seeds the same placeholder statement/difficulty/test-case content `push()`'s first call
           will need (confirmed live that `createContribution` 500s on a title-only payload--see
           `push()`)--edit it via the usual sidecar files before that first push. Also seeds
           `contribution-data.json`'s `draft`/`ready_for_moderation` to a private-draft default
           (`True`/`False`)--just a starting value, not locked down: like every other field here,
           freely editable before the first push, which reads whatever's actually there at that
           point, the same as any later push.

           A real git repo is still initialized here, with an initial commit onto `main`--local
           history from before the first push is a normal, supported thing to have (e.g. via `cg
           contribution git`), it just isn't reachable from `main` after that first push succeeds
           (see `push()`'s docstring for why, same as every other place in this class that resets
           `main` directly onto a freshly-built commit rather than preserving prior lineage).

           Refuses upfront if this directory already looks like a contribution working directory
           in any way--`create()` itself has no repair mode: a *brand new* contribution can't
           already have a matching `contribution.json`/git-dir from some earlier session, so any
           pre-existing state here means something is wrong, not something to press on through. If
           `contribution.json` exists but the git-dir is missing/corrupted (e.g. this directory
           was already `create()`d, possibly even already `push()`d), use `repair()` instead.

        Args:
            title:       The new contribution's title.
            puzzle_type: The type of the contribution. Defaults to "PUZZLE_INOUT" (a standard
                         noninteractive solo puzzle--the only type this package's contribution
                         manager has been exercised against).
            language:    The reference solution's language (see `CgSolutionLanguage`). Defaults
                         to "Python3". Always gets the `solution.<ext>` convenience symlink (see
                         `_align_solution_file_name`) if `language` maps to a known extension--but
                         `data/solution.src` itself (the symlink's target) is only pre-populated
                         with a real stub if `codingame_tools.language.get_language(language).
                         build_contribution_create_stub_source()` returns one (currently only
                         "Python3"); for any other language, the symlink is left dangling until
                         you write `data/solution.src` yourself.

        Raises:
            CgContributionManagerError: if this directory already tracks a contribution, or a
                                         git-dir already exists at the location this would use.
        """
        identity = self.load_identity()
        if identity is not None:
            raise CgContributionManagerError(
                    f"{self.identity_file} already exists (tracks contribution "
                    f"{identity.contribution_handle!r})--`create()` only makes sense for a brand "
                    "new working directory."
                )
        self._reject_legacy_layout()
        git_dir = self._git_dir_for(not is_inside_existing_repo(self.contribution_dir))
        if git_dir.is_dir():
            raise CgContributionManagerError(
                    f"{git_dir} already exists, though {self.identity_file} does not--refusing "
                    "to create a new contribution into a directory in this inconsistent state."
                )

        # Both the seeded test and validator case are test_in="1"/test_out="1" (see
        # _minimal_valid_contribution_data)--a solution that just echoes its input back trivially
        # passes both. codingame_tools.language.CgLanguage.build_contribution_create_stub_source
        # only returns one for languages with such a trivial stub (currently just Python3); an
        # empty `data/solution.src` isn't meaningfully better than no file at all otherwise (the
        # symlink itself--see _align_solution_file_name--already tells the user exactly where to
        # put their code, for every language, regardless of this).
        solution = await get_language(language).build_contribution_create_stub_source()
        data = dataclasses.replace(
                _starter_contribution_data(title), solution_language=language, solution=solution)
        _materialize_data(
                self.data_dir, puzzle_type=puzzle_type, draft=True, ready_for_moderation=False,
                data=data, cover_bytes=_cover_placeholder_bytes(),
            )
        _write_meta_gitignore(self.contribution_dir)
        self._save_identity(None)
        self._save_meta(git_dir)
        # Record the generated stub, so `set_language()` can tell a brand-new contribution (nothing
        # to lose) from one whose solution.src holds real work.
        self._write_solution_snapshot(language, solution)
        _align_solution_file_name(self.contribution_dir, data.solution_language)

        init_repo(git_dir, self.data_dir)
        repo = CgGitRepo(git_dir, self.data_dir)
        repo.set_head(MAIN_BRANCH_NAME)
        repo.commit_worktree("Initial local content (not yet pushed to the server)")

        return self.load()

    async def push(self, *, direct_create: bool = False, force: bool = False) -> CgContribution | None:
        """Push this working directory's content to the server, updating `server`/`version-data`
           to reflect the result on success, then auto-committing `main` to match (its content
           already matches what was just pushed, by construction).

           **Deliberately hides a create-vs-update decision that real git never has to make.**
           `git push` always requires an already-configured remote (`git remote add`/`git push -u`
           first)--pushing establishes no new identity, it only updates one that already exists.
           This method is different: if this working directory has never been pushed before (i.e.
           it was built via `create()`, not `import_()`, and no `push()` has succeeded yet), it
           establishes a contribution on the server first, and on success writes its handle into
           `contribution.json` (`CgContributionIdentity.contribution_handle`, previously `None`)--
           establishing the "remote" implicitly, as a side effect of the very first push, rather
           than as a separate explicit step. Every later `push()` against the same working
           directory takes the normal `updateContribution` path, exactly like today. This is a
           deliberate simplification of the git model, chosen specifically so that `create()`
           itself never has to call `createContribution` with placeholder content just to get a
           handle to import--it stays purely local (see `create()`'s docstring) until the user has
           real content ready to push.

           **The first push is itself two API calls, not one--`direct_create` opts back into the
           single-call version.** `createContribution`, unlike `updateContribution`, has no
           `prevVersion`-style idempotency check--if a request succeeds server-side but the
           response is lost (timeout, network error, and especially the same Cloudflare/524 origin
           timeout `CgContributionServiceHelper.update_contribution` already has to recover
           from for *heavy* content), there is no reliable way to learn the resulting handle, and
           blindly retrying risks a genuine duplicate contribution. This risk scales with the size/
           complexity of what's being validated--exactly what a first push often has a lot of
           (hand-written test suites, or, worse, an entire test suite carried over via `delete(
           keep_local=True)`'s "use an existing contribution as a template" workflow). So by
           default, the first push doesn't send the real content to `createContribution` at all:
           1. `createContribution` is called with a minimal, throwaway, in-memory-only stub (real
              title, otherwise just enough to be accepted--see
              `_minimal_valid_contribution_data`--always a private draft, never for moderation, no
              cover)--small and fast enough that a 524 here is unlikely in the first place.
           2. The returned handle is written into `contribution.json` *immediately*, before doing
              anything else with it--so if step 3 below fails, a retried `push()` sees
              `contribution_handle` already set and raises (see the next paragraph) rather than
              risking another `createContribution` call.
           3. A commit representing that stub (not the real content) becomes `server`'s first
              commit, via the same plumbing `fetch()` uses to build a tree without touching `main`.
           4. The *real* content is then submitted the normal way--a plain `updateContribution`
              call, version 1 -> 2, with `CgContributionServiceHelper`'s existing 524-retry/
              polling already protecting it via `prevVersion`. If this step itself fails/times out,
              the fix is exactly the same as any other failed push: just run `push()` again.

           Passing `direct_create=True` skips all of that and calls `createContribution` once,
           directly, with the real content--the original, simpler behavior, for callers confident
           their first push is small/fast enough not to need the extra round trip.

           The create-vs-update decision is made from `contribution.json`'s `contribution_handle`
           (`None` => first push), *not* from whether the `server` git branch happens to exist--
           those two can disagree, and when they do, `contribution.json` is authoritative: e.g. an
           outer project clone whose git-dir was deliberately not brought along (see `repair()`'s
           docstring) has a real `contribution_handle` but no `server` branch *yet*, and someone
           could always delete/corrupt the git-dir by hand. Trusting "`server` branch missing" as
           "never pushed" in either case would call `createContribution` *again* for a contribution
           that already exists--a duplicate, not a recoverable mistake. So if `contribution_handle`
           is already set but `server` still doesn't resolve, this raises instead of guessing--see
           `Raises` below.

           **A push with nothing to push is a no-op**, returning None without contacting the server,
           because `updateContribution` has no notion of an empty update: it increments the version
           and re-runs moderation regardless of whether anything differs. Publishing a new version
           of identical content costs a review cycle and buries the history of real changes, so it
           has to be asked for explicitly rather than happening by accident.

           "Nothing to push" is decided by comparing the working tree against `server`'s tip *tree*,
           which is exactly what the last push or fetch recorded--so it covers every content file
           including the cover image, and is unaffected by commit metadata. Checked before the cover
           upload, so an unchanged cover isn't re-uploaded just to discover there was no update to
           make.

        Args:
            direct_create: Skip the minimal-stub-first safety step on a first push, and call
                            `createContribution` once, directly, with the real content--see above.
                            Ignored (has no effect) on anything but a first push.
            force:         Push even when the working tree is identical to what the server already
                            has. Ignored on a first push, which always has something to establish.

        Returns:
            The contribution as the server now holds it, or None if there was nothing to push.

        Raises:
            FileNotFoundError: if this working directory hasn't been created/imported yet.
            CgContributionManagerError: if `puzzle_type` isn't set, if a merge is in progress, or
                                         if `contribution.json` already has a `contribution_handle`
                                         but this working directory's git repo has no `server`
                                         branch (run `repair()` first--see above).
        """
        if self.merge_in_progress:
            raise CgContributionManagerError(
                    "A merge is in progress (see `cg contribution merge continue`/`abort`)--"
                    "resolve or abort it before pushing."
                )
        view = self.load()
        if view.puzzle_type is None:
            raise CgContributionManagerError("Cannot push: puzzle_type is not set in contribution-data.json.")

        # Canonicalize tests/'s ordinal directory names before snapshotting data_dir below: this
        # commit's tree becomes server's new tip verbatim (see the write_tree_from_worktree() call
        # further down), and server's *next* tree (built fresh from a later fetch()/import_(), via
        # _materialize_data()--always canonical) would otherwise show a spurious diff/conflict
        # against a non-canonical layout committed here, even when the actual test content never
        # changed. Content-preserving (only directory names change)--see
        # test_cases_dir.renormalize_test_case_dirs.
        renormalize_test_case_dirs(self.tests_dir)

        identity = self.load_identity()
        assert identity is not None  # merge_in_progress above already required a loadable git_dir
        first_push = identity.contribution_handle is None

        repo = self.git_repo
        server_sha = repo.resolve_ref(SERVER_BRANCH_NAME)
        if not first_push and server_sha is None:
            raise CgContributionManagerError(
                    f"{self.identity_file} already tracks contribution "
                    f"{identity.contribution_handle!r}, but this working directory's git repo has "
                    f"no {SERVER_BRANCH_NAME} branch (missing/corrupted git-dir, or a freshly "
                    "cloned outer project that hasn't been repaired yet)--call repair() (`cg "
                    "contribution repair`) before pushing."
                )

        if first_push and not direct_create:
            stub_data = _minimal_valid_contribution_data(view.data.title)
            stub_handle = await self.client.services.contribution.helper.create_contribution(
                    view.puzzle_type, stub_data, draft=True, ready_for_moderation=False)
            self._write_contribution_handle(stub_handle)  # see the docstring--persisted before find_contribution
            stub_contribution = await self.client.services.contribution.find_contribution(stub_handle)
            with tempfile.TemporaryDirectory(prefix="cg-contribution-stub-") as tmp:
                staging = Path(tmp)
                _materialize_data(
                        staging, puzzle_type=view.puzzle_type, draft=True, ready_for_moderation=False,
                        data=stub_data, cover_bytes=None,
                    )
                stub_tree = repo.write_tree_from_dir(staging)
            server_sha = self._record_server_commit(
                    repo, stub_tree, stub_contribution, None,
                    f"Create placeholder on server (version {stub_contribution.last_version.version})",
                )

        # Only true if direct_create was requested--the stub step above (when it ran) already
        # turned this into an ordinary update, same as any push against an existing contribution.
        needs_direct_create = first_push and direct_create

        # Nothing to push? Say so and stop, before the cover upload below spends a request finding
        # out. server's tip tree is what the last push/fetch recorded, so comparing the worktree
        # against it covers every content file, cover image included.
        if not first_push and not force:
            assert server_sha is not None  # guaranteed by the raise above for a non-first push
            server_tree = repo.rev_parse(f"{server_sha}^{{tree}}", check=False)
            if server_tree is not None and repo.write_tree_from_worktree() == server_tree:
                return None

        # No prior server state to compare a cover image's hash against when creating (directly,
        # or via the stub established above, which never has a cover either)--the empty metadata
        # below just always fails that comparison, forcing a fresh upload, same as any other cover
        # change.
        if needs_direct_create:
            current_metadata = CgContributionCommitMetadata(contribution_id="", version=0)
        else:
            assert server_sha is not None  # the raise above (or the stub step) already ensured this
            current_metadata = _trailers_to_metadata(repo.read_trailers(server_sha))

        cover_path = self.data_dir / COVER_IMAGE_FILE_NAME
        cover_binary_id: int | None
        cover_bytes: bytes | None
        if cover_path.is_file():
            cover_bytes = cover_path.read_bytes()
            cover_content_hash = compute_content_hash(cover_bytes)
            if cover_content_hash == current_metadata.cover_binary_hash and current_metadata.cover_binary_id is not None:
                cover_binary_id = current_metadata.cover_binary_id
            else:
                upload = await self.client.servlets.file_upload(
                        cover_bytes, filename=COVER_IMAGE_FILE_NAME, content_type="image/png")
                cover_binary_id = upload.id
        else:
            cover_binary_id = None
            cover_bytes = None

        local_data, _ = _read_local_data(self.data_dir, view.data)
        data = dataclasses.replace(local_data, cover_binary_id=cover_binary_id)

        if needs_direct_create:
            contribution_id = await self.client.services.contribution.helper.create_contribution(
                    view.puzzle_type, data, view.draft, view.ready_for_moderation)
            result = await self.client.services.contribution.find_contribution(contribution_id)
            self._write_contribution_handle(contribution_id)
        else:
            result = await self.client.services.contribution.helper.update_contribution(
                    current_metadata.contribution_id,
                    view.puzzle_type,
                    data,
                    view.draft,
                    view.ready_for_moderation,
                    current_metadata.version,
                )
            result = await self._refresh_active_version(result, current_metadata.contribution_id)

        tree = repo.write_tree_from_worktree()
        new_server_sha = self._record_server_commit(
                repo, tree, result, cover_bytes, f"Push to server (version {result.last_version.version})")
        # main's ref moves directly onto server's new commit (not a separate sibling commit with
        # matching content)--deliberately, so `git merge-base main server` still equals server's
        # tip afterward. A sibling commit here (e.g. via commit_worktree()) would have the *same*
        # tree but a *different* SHA (different parent/message/trailers), leaving merge-base stuck
        # at the pre-push point and making the next rebase()/merge_start() wrongly see "local
        # changed" even though content-wise nothing has, confirmed by direct testing. Uses
        # reset_index_to() rather than a raw update_ref(), so the real index (never touched by
        # write_tree_from_worktree()'s scratch-index tree build) stays in sync with main's new
        # tip too--otherwise a later real `git merge` (merge_start()) reads a stale index. On a
        # first push specifically, this also means any *local-only* history main had before the
        # push (e.g. commits made via `cg contribution git` while drafting) stops being reachable
        # from main's new tip--not deleted, just no longer part of main's ancestry, recoverable via
        # the reflog for as long as it lasts. Same tradeoff already accepted everywhere else this
        # class resets main's ref directly instead of preserving lineage; not special-cased here.
        repo.reset_index_to(new_server_sha)
        return result

    async def _refresh_active_version(self, result: CgContribution, contribution_id: CgContributionId) -> CgContribution:
        """`updateContribution`'s response has been confirmed live to report a stale
           `active_version` (lagging one version behind `last_version.version` in that same
           response), apparently because the server finishes activating the new version slightly
           asynchronously--see `CgContribution.active_version`'s docstring. This re-fetches via
           `findContribution`, polling briefly (a few seconds, best-effort) until `active_version`
           catches up to the version just submitted.

           Gives up and returns the latest `findContribution` result even if `active_version`
           still hasn't caught up, rather than blocking indefinitely on unconfirmed eventual-
           consistency timing--logs a warning in that case.
        """
        target_version = result.last_version.version
        if result.active_version == target_version:
            return result  # already fresh (e.g. helper's own 524-recovery already re-fetched it)
        refreshed = result
        for attempt in range(_ACTIVE_VERSION_POLL_MAX_ATTEMPTS):
            refreshed = await self.client.services.contribution.find_contribution(contribution_id)
            if refreshed.active_version == target_version:
                return refreshed
            if attempt + 1 < _ACTIVE_VERSION_POLL_MAX_ATTEMPTS:
                await asyncio.sleep(_ACTIVE_VERSION_POLL_INTERVAL_SECONDS)
        logger.warning(
                "push(): active_version for contribution %r is still %s (expected %s) after "
                "%d findContribution attempts; using it anyway.",
                contribution_id, refreshed.active_version, target_version, _ACTIVE_VERSION_POLL_MAX_ATTEMPTS,
            )
        return refreshed

    # --- fetch -------------------------------------------------------------------------------

    async def fetch(self) -> CgContribution:
        """Refresh `server`'s tip from a fresh `findContribution`, and unconditionally refresh
           `.meta/contribution-status.json` (see `_refresh_status_cache`)--even when the content
           version hasn't changed, since none of that cache's fields (score/votes/comment count/
           views/moderator approve-reject tallies/etc.) are tied to it; only the `server`/
           `version-data` git commit is skipped in that case. Never touches `main`, the working
           tree, or the real index--the fetched content is staged into a throwaway temp directory
           purely to build a tree object from, so this is safe to call regardless of what's
           currently on disk in `data/`.

           Reuses the previous cover image's bytes (read straight out of the object database, via
           `server`'s current tip) rather than re-downloading, if its binary ID is unchanged;
           self-heals (re-downloads) rather than raising if that reuse ever turns out to be stale/
           corrupted--this cache is opportunistic, not sacred.

        Raises:
            FileNotFoundError: if this working directory has never been imported/committed.
            CgContributionManagerError: if a merge is in progress.
        """
        if self.merge_in_progress:
            raise CgContributionManagerError(
                    "A merge is in progress (see `cg contribution merge continue`/`abort`)--"
                    "resolve or abort it before fetching."
                )
        identity = self.load_identity()
        assert identity is not None  # merge_in_progress above already required a loadable git_dir

        repo = self.git_repo
        server_sha = repo.resolve_ref(SERVER_BRANCH_NAME)
        if server_sha is None:
            raise FileNotFoundError(f"{self.git_dir} has no {SERVER_BRANCH_NAME} branch--nothing to fetch against.")
        current_metadata = _trailers_to_metadata(repo.read_trailers(server_sha))

        contribution = await self.client.services.contribution.find_contribution(current_metadata.contribution_id)
        await self._refresh_status_cache(contribution)
        if contribution.last_version.version == current_metadata.version:
            return contribution  # server's tip already reflects this exact version; status cache still refreshed above

        version = contribution.last_version
        data = version.data
        new_binary_id = data.cover_binary_id
        cover_bytes: bytes | None
        if new_binary_id is None:
            cover_bytes = None
        elif new_binary_id == current_metadata.cover_binary_id:
            cached = repo.read_file_at(SERVER_BRANCH_NAME, COVER_IMAGE_FILE_NAME)
            if cached is not None and compute_content_hash(cached) == current_metadata.cover_binary_hash:
                cover_bytes = cached
            else:
                download = await self.client.servlets.file_servlet(new_binary_id)
                cover_bytes = download.content
        else:
            download = await self.client.servlets.file_servlet(new_binary_id)
            cover_bytes = download.content

        with tempfile.TemporaryDirectory(prefix="cg-contribution-fetch-") as tmp:
            staging = Path(tmp)
            _materialize_data(
                    staging,
                    puzzle_type=contribution.contribution_type,
                    draft=version.draft if version.draft is not None else True,
                    ready_for_moderation=version.ready_for_moderation if version.ready_for_moderation is not None else False,
                    data=data,
                    cover_bytes=cover_bytes,
                )
            tree = repo.write_tree_from_dir(staging)
        self._record_server_commit(repo, tree, contribution, cover_bytes, f"Fetch from server (version {version.version})")
        return contribution

    # --- rebase ----------------------------------------------------------------------------------

    async def rebase(self) -> CgRebaseStatus:
        """Detect drift between `server` and `main`, and automatically resolve it when that's
           unambiguous:

           - `server` unchanged since `main` last synced: nothing to do, regardless of local edits
             (`CgRebaseStatus.UP_TO_DATE`).
           - `server` changed, `main` unchanged since it last synced: fast-forward--`main` gets a
             new commit matching `server`'s new tip (`CgRebaseStatus.FAST_FORWARDED`).
           - Both changed: a real conflict, left entirely alone (`CgRebaseStatus.CONFLICT`)--use
             `cg contribution diff` to inspect, and `cg contribution merge` to resolve.

        Raises:
            FileNotFoundError: if this working directory has never been imported/committed.
            CgContributionManagerError: if a merge is already in progress.
        """
        if self.merge_in_progress:
            raise CgContributionManagerError(
                    "A merge is in progress (see `cg contribution merge continue`/`abort`)--"
                    "resolve or abort it before rebasing."
                )
        repo = self.git_repo
        base_before = repo.merge_base(MAIN_BRANCH_NAME, SERVER_BRANCH_NAME)
        if base_before is None:
            raise FileNotFoundError(f"{self.git_dir} has no shared history between main/server--nothing to rebase against.")

        await self.fetch()

        server_after = repo.resolve_ref(SERVER_BRANCH_NAME)
        if server_after == base_before:
            return CgRebaseStatus.UP_TO_DATE
        assert server_after is not None  # can't have changed away from a real SHA to nothing

        main_sha = repo.resolve_ref(MAIN_BRANCH_NAME)
        if main_sha == base_before:
            # A *true* fast-forward: main's ref moves directly onto server's tip, same as real
            # git's own definition--no new commit created. checkout_all() still needs to run to
            # bring the working tree/index along with it.
            repo.checkout_all(SERVER_BRANCH_NAME)
            repo.update_ref(f"refs/heads/{MAIN_BRANCH_NAME}", server_after)
            _align_solution_file_name(self.contribution_dir, self.load().data.solution_language)
            return CgRebaseStatus.FAST_FORWARDED

        return CgRebaseStatus.CONFLICT

    # --- instant, one-shot merge resolutions (no merge state machine involved) -----------------

    async def merge_discard_local(self) -> CgContributionView:
        """Discard all local edits: unconditionally fetch, then move `main`'s ref directly onto
           `server`'s new tip (same as `git reset --hard server`--no new commit, and deliberately
           *not* a new commit with matching content either: a sibling commit here would leave
           `git merge-base main server` stuck at the old sync point instead of advancing to
           server's tip, making the next `rebase()`/`merge_start()` wrongly see "local changed"
           even though nothing would be, confirmed by direct testing--see `push()`'s docstring
           for the same reasoning). Any local commits `main` had are not deleted, just no longer
           reachable from `main` itself--recoverable via `main`'s reflog for as long as it lasts.
           Unlike `rebase()`, doesn't check whether local actually diverged first--always
           overwrites. Instant--never touches `MERGE_HEAD`.

        Raises:
            FileNotFoundError: if this working directory has never been imported/committed.
            CgContributionManagerError: if a merge is already in progress.
        """
        if self.merge_in_progress:
            raise CgContributionManagerError(
                    "A merge is in progress (see `cg contribution merge continue`/`abort`)--"
                    "resolve or abort it first."
                )
        await self.fetch()
        repo = self.git_repo
        server_sha = repo.resolve_ref(SERVER_BRANCH_NAME)
        if server_sha is None:
            raise FileNotFoundError(f"{self.git_dir} has no {SERVER_BRANCH_NAME} branch--nothing to discard local changes to.")
        repo.checkout_all(SERVER_BRANCH_NAME)
        repo.update_ref(f"refs/heads/{MAIN_BRANCH_NAME}", server_sha)
        _align_solution_file_name(self.contribution_dir, self.load().data.solution_language)
        return self.load()

    async def merge_discard_server(self) -> CgContribution:
        """Update `server` to reflect the current server state, without touching `main`/the
           working tree at all--just `fetch()` under a different name, kept as its own method for
           CLI-naming continuity with the old design (where `last_committed`/`remote` were
           distinct concepts this bridged; they no longer are).

        Raises:
            FileNotFoundError: if this working directory has never been imported/committed.
            CgContributionManagerError: if a merge is already in progress.
        """
        return await self.fetch()

    # --- merge state machine ----------------------------------------------------------------------

    async def merge_start(self) -> CgMergeStartResult:
        """Begin (or, if one's already in progress, do nothing and report it) a merge:

           1. `fetch()` (refuses if a merge is already in progress--checked first, so this never
              runs in that case).
           2. If `server`'s tip already equals `git merge-base main server`, there's nothing to
              merge (`CgMergeStartStatus.UP_TO_DATE`).
           3. Otherwise, a real `git merge server` against the working tree. If it completes
              cleanly (including a trivial fast-forward), git has already committed the result--
              `merge_in_progress` is `False` again, nothing more to do (except renormalizing
              `tests/`'s directory layout--see below--and re-generating the solution symlink).
              If it stops with conflicts, `text_conflicts`/`binary_conflicts` (split by content--
              see `_looks_like_text`) list the affected paths; resolve them (by hand, or `cg
              contribution merge interactive`) and run `merge_continue()`.

           A clean merge's own auto-commit (from git itself) can leave `tests/`'s ordinal
           directories in a non-canonical layout (e.g. both sides added test cases using
           different numbering)--see `push()`'s docstring for why that matters for a stable
           round trip with `server`. So a clean merge here also renormalizes `tests/` and folds
           any resulting rename into that same commit via `restage_and_amend_if_dirty()`, rather
           than leaving it for the next `push()` to silently fix up.

        Raises:
            FileNotFoundError: if this working directory has never been imported/committed.
        """
        if self.merge_in_progress:
            return CgMergeStartResult(status=CgMergeStartStatus.ALREADY_IN_PROGRESS)

        repo = self.git_repo
        await self.fetch()

        base = repo.merge_base(MAIN_BRANCH_NAME, SERVER_BRANCH_NAME)
        server_sha = repo.resolve_ref(SERVER_BRANCH_NAME)
        if server_sha == base:
            return CgMergeStartResult(status=CgMergeStartStatus.UP_TO_DATE)

        clean = repo.merge_branch(SERVER_BRANCH_NAME)
        if clean:
            renormalize_test_case_dirs(self.tests_dir)
            repo.restage_and_amend_if_dirty()
            _align_solution_file_name(self.contribution_dir, self.load().data.solution_language)
            return CgMergeStartResult(status=CgMergeStartStatus.STARTED)

        conflicts = repo.status_conflicts()
        text_conflicts: list[str] = []
        binary_conflicts: list[str] = []
        for rel_path in conflicts:
            content = (self.data_dir / rel_path).read_bytes() if (self.data_dir / rel_path).is_file() else b""
            (text_conflicts if _looks_like_text(content) else binary_conflicts).append(rel_path)
        return CgMergeStartResult(
                status=CgMergeStartStatus.STARTED,
                text_conflicts=tuple(text_conflicts),
                binary_conflicts=tuple(binary_conflicts),
            )

    def merge_continue(self) -> None:
        """Finish an in-progress merge: stage everything (refusing first if a still-unmerged path
           still has a leftover `<<<<<<<` marker--see `CgGitRepo.merge_continue`) and commit, then
           renormalize `tests/`'s ordinal directory layout--see `push()`'s docstring for why
           that matters for a stable round trip with `server`--folding any resulting rename into
           that same merge commit via `restage_and_amend_if_dirty()` (done *after* the merge
           commit exists, deliberately: renaming a conflicted-but-still-unresolved path before
           git's own unmerged-index-stage bookkeeping is resolved and committed would confuse
           `status_conflicts()`, which looks paths up by their pre-rename name). Refreshes the
           solution symlink afterward (a resolved `contribution-data.json` conflict may have
           changed `solution_language`).

        Raises:
            CgContributionManagerError: if no merge is in progress, or (wrapping git's own error)
                                         if unresolved conflict markers remain.
        """
        if not self.merge_in_progress:
            raise CgContributionManagerError("No merge in progress (run `cg contribution merge` to start one).")
        repo = self.git_repo
        try:
            repo.merge_continue()
        except CgGitError as e:
            raise CgContributionManagerError(str(e)) from e
        renormalize_test_case_dirs(self.tests_dir)
        repo.restage_and_amend_if_dirty()
        _align_solution_file_name(self.contribution_dir, self.load().data.solution_language)

    def merge_abort(self) -> None:
        """Abort an in-progress merge: restore `main`'s pre-merge working tree state and discard
           `MERGE_HEAD`. `server` is left untouched--the merge never reached `merge_continue()`, so
           nothing about it was ever recorded anywhere.

        Raises:
            CgContributionManagerError: if no merge is in progress.
        """
        if not self.merge_in_progress:
            raise CgContributionManagerError("No merge in progress.")
        self.git_repo.merge_abort()
        _align_solution_file_name(self.contribution_dir, self.load().data.solution_language)

    # --- discard_local -------------------------------------------------------------------------

    def discard_local(self) -> CgContributionView:
        """Discard local edits: reset this working directory's content to match `server`'s
           current tip exactly--purely local, no network access at all (unlike
           `merge_discard_local()`, which `fetch()`es fresh first--this uses whatever `server`
           already has). Resets both the index and working tree (via `CgGitRepo.checkout_all`,
           i.e. `git read-tree --reset -u`--`git checkout <ref> -- .` would *not* remove a file
           that exists locally but not in `server`'s tree, confirmed by direct testing), without
           moving `main`'s ref or creating a commit--if `main` had local commits beyond the last
           sync, this discards them from the working tree too (matching the old, since-renamed
           `revert()`'s "match the last synced state exactly" contract), but they remain
           recoverable via `main`'s own history, since this never does a hard reset of the ref
           itself.

           Named to match `merge_discard_local()`/`merge_discard_server()`'s existing "discard"
           vocabulary (all three answer "throw away one side and take the other," differing only
           in whether a merge is in progress and whether they fetch first)--deliberately not
           `revert()` (the original name), which collides with real git's very different meaning
           (a new commit that undoes a past one, preserving history)--and not bare `discard()`,
           which reads as "discard the whole contribution" rather than "discard my local edits."

        Raises:
            FileNotFoundError: if this working directory has never been imported/committed.
            CgContributionManagerError: if a merge is in progress.
        """
        if self.merge_in_progress:
            raise CgContributionManagerError(
                    "A merge is in progress (see `cg contribution merge continue`/`abort`)--"
                    "resolve or abort it before discarding local edits."
                )
        repo = self.git_repo
        if repo.resolve_ref(SERVER_BRANCH_NAME) is None:
            raise FileNotFoundError(f"{self.git_dir} has no {SERVER_BRANCH_NAME} branch--nothing to discard to.")
        repo.checkout_all(SERVER_BRANCH_NAME)
        _align_solution_file_name(self.contribution_dir, self.load().data.solution_language)
        return self.load()

    # --- delete --------------------------------------------------------------------------------

    async def delete(self, *, keep_local: bool = False, keep_server: bool = False) -> None:
        """Delete this contribution from the server (`Contribution/deleteContribution`--
           unrecoverable), then remove this entire working directory (the default)--or, with
           `keep_local`, detach it instead: drop the `server`/`version-data` branches and reset
           `contribution.json`'s `contribution_handle` back to `None`, leaving a purely local
           working directory in exactly the state `create()` would have left it in, ready for its
           *current* content to be pushed as a brand new contribution on the next `push()` (see
           `push()`'s create-vs-update docstring)--e.g. for using an existing contribution as a
           template for a new one.

           `keep_server` skips the server-side deletion entirely (nothing sent to
           `deleteContribution`) and just removes this working directory--for when you only want
           to stop tracking a contribution locally without touching it on the server. Mutually
           exclusive with `keep_local` (together they'd mean "delete nothing," which isn't a
           `delete()` at all).

           A working directory that was `create()`d but never successfully `push()`d has no
           server-side contribution at all (`contribution.json`'s `contribution_handle` is
           `None`--the authoritative signal here, same as `push()`'s create-vs-update decision;
           see that method's docstring for why this is trusted over the `server` git branch's
           mere existence)--by default (neither `keep_local` nor `keep_server`), that's not an
           error: there's simply nothing to send to `deleteContribution`, so this just removes the
           local working directory, same as it would for any other directory. `keep_local` and
           `keep_server` each DO require a real `contribution_handle` to exist, though, and raise
           if not--both are explicit statements about server state (`keep_local`: "detach from the
           thing I'm currently tracking"; `keep_server`: "leave the thing I'm currently tracking
           alone") that don't make sense to honor silently as no-ops when there's nothing being
           tracked yet.

           `main` and its commit history (including anything reachable only via the old
           `server`/`version-data` branches, by SHA, until a real `git gc` eventually collects it)
           are left untouched by `keep_local`; only the branches/identity that pointed at the
           now-deleted contribution are affected.

           No confirmation prompt here--that's the CLI's job (`cg contribution delete`), not this
           class's (matches every other method here: no interactive behavior, ever).

        Raises:
            FileNotFoundError: if this working directory has never been created/imported, or (only
                                with `keep_local` or `keep_server`) has no `contribution_handle`
                                yet (`create()`d but never successfully `push()`d).
            CgContributionManagerError: if a merge is in progress, or both `keep_local` and
                                         `keep_server` are set.
        """
        if keep_local and keep_server:
            raise CgContributionManagerError(
                    "keep_local and keep_server are mutually exclusive--together they'd mean "
                    "deleting nothing at all."
                )
        if self.merge_in_progress:
            raise CgContributionManagerError(
                    "A merge is in progress (see `cg contribution merge continue`/`abort`)--"
                    "resolve or abort it before deleting."
                )
        identity = self.load_identity()
        assert identity is not None  # merge_in_progress above already required a loadable git_dir
        contribution_handle = identity.contribution_handle
        if contribution_handle is None and keep_local:
            raise FileNotFoundError(
                    f"{self.identity_file} has no contribution_handle--nothing to detach from "
                    "(this working directory was create()d but never successfully pushed)."
                )
        if contribution_handle is None and keep_server:
            raise FileNotFoundError(
                    f"{self.identity_file} has no contribution_handle--nothing server-side for "
                    "keep_server to leave alone (this working directory was create()d but never "
                    "successfully pushed). Omit keep_server to just remove the local working "
                    "directory."
                )
        if contribution_handle is not None and not keep_server:
            await self.client.services.contribution.delete_contribution(contribution_handle)

        if keep_local:
            repo = self.git_repo
            repo.delete_ref(f"refs/heads/{SERVER_BRANCH_NAME}")
            repo.delete_ref(f"refs/heads/{VERSION_DATA_BRANCH_NAME}")
            self._write_contribution_handle(None)
        else:
            # See CgPuzzleManager.delete: a containerized language leaves a container bind-mounted
            # to this directory, and its name derives from the path--orphaning one means a future
            # working directory at the same path silently attaches to it.
            await remove_containers_for_root(self.contribution_dir)
            shutil.rmtree(self.contribution_dir)

    # --- status ----------------------------------------------------------------------------

    async def status(self, *, remote: bool = False) -> CgContributionStatus:
        """A point-in-time summary of this working directory--see `CgContributionStatus`.

           By default, entirely local/cheap: no network access at all--`server`/
           `moderator_approvals`/`moderator_denials` (if this working directory has ever been
           pushed) come straight from `.meta/contribution-status.json` (see `read_status_cache`),
           refreshed on some earlier `fetch()`/`import_()`/`repair()` call. Pass `remote=True` to
           `fetch()` fresh first (same tradeoff as `cg contribution diff --remote`)--skipped
           automatically if this working directory has never been pushed (nothing to fetch) or a
           merge is in progress (fetching mid-merge is refused by `fetch()` itself); `fetch()`
           unconditionally refreshes that cache file (see its docstring), so this always reflects
           whatever `fetch()` just saw.

        Args:
            remote: If True, `fetch()` fresh from the server before reporting--otherwise reports
                    whatever `.meta/contribution-status.json` last cached (possibly stale, or
                    entirely absent if this working directory has never been fetched under a
                    version of this package new enough to write it). Defaults to False.

        Raises:
            FileNotFoundError: if this working directory has never been imported/created.
        """
        identity = self.load_identity()
        if identity is None:
            raise FileNotFoundError(f"{self.identity_file} does not exist--this working directory has never been imported/created.")
        merge_in_progress = self.merge_in_progress
        if remote and not merge_in_progress and identity.contribution_handle is not None:
            await self.fetch()

        repo = self.git_repo
        view = self.load()
        local_title = view.data.title
        local_dirty = False if merge_in_progress else bool(repo.diff_name_status(MAIN_BRANCH_NAME))

        server_sha = repo.resolve_ref(SERVER_BRANCH_NAME)
        metadata = self.server_metadata()
        status_cache = self.read_status_cache()
        server_contribution = status_cache.contribution if status_cache is not None else None
        moderator_approvals = status_cache.moderator_approvals if status_cache is not None else None
        moderator_denials = status_cache.moderator_denials if status_cache is not None else None
        status_cache_refreshed_at = status_cache.refreshed_at if status_cache is not None else None

        sync_status: CgContributionSyncStatus
        if merge_in_progress:
            sync_status = CgContributionSyncStatus.MERGE_IN_PROGRESS
        elif server_sha is None:
            sync_status = CgContributionSyncStatus.NOT_PUSHED
        else:
            base = repo.merge_base(MAIN_BRANCH_NAME, SERVER_BRANCH_NAME)
            main_sha = repo.resolve_ref(MAIN_BRANCH_NAME)
            server_changed = server_sha != base
            local_changed = local_dirty or main_sha != base
            if server_changed and local_changed:
                sync_status = CgContributionSyncStatus.DIVERGED
            elif server_changed:
                sync_status = CgContributionSyncStatus.SERVER_AHEAD
            elif local_changed:
                sync_status = CgContributionSyncStatus.LOCAL_AHEAD
            else:
                sync_status = CgContributionSyncStatus.UP_TO_DATE

        return CgContributionStatus(
                contribution_dir=self.contribution_dir,
                pushed=identity.contribution_handle is not None,
                contribution_handle=identity.contribution_handle,
                local_title=local_title,
                local_dirty=local_dirty,
                merge_in_progress=merge_in_progress,
                sync_status=sync_status,
                local_version=metadata.version if metadata is not None else None,
                local_draft=view.draft,
                local_ready_for_moderation=view.ready_for_moderation,
                local_puzzle_type=view.puzzle_type,
                local_solution_language=view.data.solution_language,
                local_difficulty=view.data.difficulty,
                server=server_contribution,
                moderator_approvals=moderator_approvals,
                moderator_denials=moderator_denials,
                status_cache_refreshed_at=status_cache_refreshed_at,
            )

    # --- local test running -----------------------------------------------------------------

    def list_local_tests(
                self,
                ordinals: list[str] | None = None,
                *,
                local: bool = True,
                validator: bool = True,
            ) -> list[CgContributionLocalTestCase]:
        """Enumerate `tests/` (see `codingame_tools.contribution_manager.test_cases_dir.
           list_local_test_cases`), optionally filtered.

        Args:
            ordinals:  If given, only test cases whose ordinal matches one of these (by exact
                       string match, or--if both sides are purely numeric--by numeric equality,
                       so `"1"` matches ordinal directory `"01"`). Defaults to every ordinal.
            local:     Include `"local"`-side test cases. Defaults to True.
            validator: Include `"validator"`-side test cases. Defaults to True.

        Returns:
            Matching test cases, in the same order `list_local_test_cases` returns them.
        """
        test_cases = list_local_test_cases(self.tests_dir)
        if ordinals is not None:
            test_cases = [tc for tc in test_cases if any(_ordinal_matches(o, tc.ordinal) for o in ordinals)]
        if not local:
            test_cases = [tc for tc in test_cases if tc.side != "local"]
        if not validator:
            test_cases = [tc for tc in test_cases if tc.side != "validator"]
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

           Infallible by design: never requires this directory to have been imported (`meta_dir`
           falls back to the non-`data/` layout when there's no `contribution.json` to say
           otherwise). `solution_language` is accepted for signature stability but no longer selects
           a path: there is one real solution file and `solution_file` finds it whatever extension
           it carries.
        """
        return CgLanguageContext(
                root=self.contribution_dir,
                solution_file=self.solution_file,
                meta_dir=self.meta_dir,
                toolchain_dir=self.toolchain_dir,
                mount_root=mount_root or self.mount_root or find_workspace_root(self.contribution_dir),
                toolchain_languages=self.toolchain_languages,
                toolchain_image=self.toolchain_image,
            )

    @property
    def solution_snapshot_file(self) -> Path:
        """Path to `.meta/solution-snapshot.json`--see `CgContributionSolutionSnapshot`."""
        return self.meta_dir / SOLUTION_SNAPSHOT_FILE_NAME

    @property
    def selected_test_file(self) -> Path:
        """Path to `.meta/selected-test.json`--see `CgContributionSelectedTest`."""
        return self.meta_dir / SELECTED_TEST_FILE_NAME

    def load_selected_test(self) -> CgContributionSelectedTest | None:
        """The explicitly selected test case, or None if none has been chosen."""
        if not self.selected_test_file.is_file():
            return None
        return CgContributionSelectedTest.load(self.selected_test_file)

    def select_test(self, ordinal: str, side: str) -> None:
        """Choose which test case the debugger runs against.

        Raises:
            CgContributionManagerError: if no test case matches, so a typo surfaces now rather than
                                         when a debug session fails to start.
        """
        matching = self.list_local_tests(
                [ordinal], local=side == "local", validator=side == "validator")
        if not matching:
            available = ", ".join(f"{tc.ordinal}/{tc.side}" for tc in self.list_local_tests()) or "(none)"
            raise CgContributionManagerError(
                    f"No {side} test case with ordinal {ordinal!r}. Available: {available}.")
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        CgContributionSelectedTest(ordinal=matching[0].ordinal, side=side).save(self.selected_test_file)

    def clear_selected_test(self) -> None:
        """Forget the explicit selection, falling back to the default (the first local test)."""
        self.selected_test_file.unlink(missing_ok=True)

    def resolve_debug_test(self) -> CgContributionLocalTestCase:
        """Which single test a debug session should use: the selection, else the first *local* test.

           Local rather than merely first: validators are the hidden, scoring cases, and landing in
           a debugger on one by default would be surprising. Falls back to the first test of any
           side only if there are no local ones at all.

        Raises:
            CgContributionManagerError: if `tests/` holds no test cases.
        """
        all_tests = self.list_local_tests()
        if not all_tests:
            raise CgContributionManagerError(f"No test cases in {self.tests_dir}.")
        selected = self.load_selected_test()
        if selected is not None:
            for test_case in all_tests:
                if test_case.ordinal == selected.ordinal and test_case.side == selected.side:
                    return test_case
        locals_first = [tc for tc in all_tests if tc.side == "local"]
        return (locals_first or all_tests)[0]

    def load_solution_snapshot(self) -> CgContributionSolutionSnapshot | None:
        """The starter stub this client last generated into `data/solution.src`, or `None` if there
           isn't one (never generated, or `.meta/` predates the snapshot)."""
        if not self.solution_snapshot_file.is_file():
            return None
        return CgContributionSolutionSnapshot.load(self.solution_snapshot_file)

    def _write_solution_snapshot(self, language: CgSolutionLanguage, code: str | None) -> None:
        """Record a freshly generated starter stub. Called only where this client *generates* a
           solution (`create()`, `set_language()`)--never for content that came from the server or
           from git, since the point of the snapshot is to distinguish "still just our placeholder"
           from "holds real work"."""
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        CgContributionSolutionSnapshot(solution_language=language, code=code).save(
                self.solution_snapshot_file)

    def _solution_is_generated_stub(self, language: CgSolutionLanguage | None) -> bool:
        """Whether `data/solution.src` is still exactly the stub this client generated for
           `language`--i.e. there is no real reference solution to lose."""
        snapshot = self.load_solution_snapshot()
        if snapshot is None or language is None or snapshot.solution_language != language:
            return False
        current = self.solution_file.read_text(encoding="utf-8") \
            if self.solution_file.is_file() else None
        return _normalize_solution(current) == _normalize_solution(snapshot.code)

    async def set_language(
                self,
                language: CgSolutionLanguage,
                *,
                force: bool = False,
            ) -> CgContributionSetLanguageResult:
        """Switch this contribution's reference-solution language, writing a fresh starter stub.

           **This is destructive in a way the puzzle equivalent is not, and deliberately harder to
           do by accident.** A contribution stores exactly one solution server-side, with no
           per-language history--unlike a puzzle, where CodinGame keeps your latest source for each
           language and switching is reversible (see
           `CgTestSessionService.get_previous_code_by_language_id`). Here there is nothing to
           restore and nothing to switch back to: the existing solution is replaced by a stub, and
           the last durable copy is overwritten as soon as the next `push()` lands.

           So the only non-destructive case is "`data/solution.src` is still exactly the stub this
           client generated" (see `_solution_is_generated_stub`). Notably, *matching what the server
           currently has* does **not** count as safe, unlike the puzzle version--the server copy is
           precisely what the next push destroys.

           Purely local: no network call, because there is no per-language code to fetch.

        Args:
            language: CodinGame language ID to switch to, e.g. "C++" (see `CgSolutionLanguage`).
            force:    Switch even though a real reference solution would be discarded.

        Returns:
            A `CgContributionSetLanguageResult`--`wrote_stub` is False when the new language has no
            stub to offer and `solution.src` was removed instead.

        Raises:
            FileNotFoundError: if this working directory hasn't been imported/initialized.
            CgContributionManagerError: if `language` isn't one this client knows, if it's already
                                         the current language, or if a real solution would be lost
                                         and `force` is False.
        """
        view = self.load()
        previous_language = view.data.solution_language
        if language not in list_language_cg_ids():
            raise CgContributionManagerError(
                    f"{language!r} isn't a language this client knows. Known languages: "
                    f"{', '.join(list_language_cg_ids())}."
                )
        if language == previous_language:
            raise CgContributionManagerError(
                    f"{self.contribution_dir} is already using {language!r}--nothing to switch."
                )
        if not force and not self._solution_is_generated_stub(previous_language):
            raise CgContributionManagerError(
                    f"{self.solution_file} holds a real {previous_language!r} reference solution. "
                    "A contribution stores only ONE solution, with no per-language history, so "
                    "switching replaces it with a starter stub and the next `cg contribution push` "
                    "overwrites the last durable copy--there is nothing to switch back to. Save it "
                    "somewhere outside this working directory first, then pass --force."
                )

        # A language with no stub yields None, written as an *empty* solution.src rather than no
        # file at all--empty is this client's spelling of a null solutionSource (see
        # _read_local_data), so `push()` still skips solution validation while the author keeps a
        # file to type into and a symlink that resolves. Writing a comment-only placeholder instead
        # would be non-null, fail validation, and block the push--see
        # CgLanguage.build_contribution_create_stub_source.
        stub = await get_language(language).build_contribution_create_stub_source()
        _write_sidecar(self.solution_file, stub or "")
        self._write_solution_snapshot(language, stub)
        self.save(dataclasses.replace(
                view, data=dataclasses.replace(view.data, solution_language=language)))
        _align_solution_file_name(self.contribution_dir, language)
        return CgContributionSetLanguageResult(
                language=language, previous_language=previous_language, wrote_stub=stub is not None,
            )

    async def provision_vscode(
                self,
                solution_language: CgSolutionLanguage,
                *,
                workspace_root: Path | None = None,
                force: bool = False,
                check: bool = False,
                debug_adapter_logging: bool = False,
            ) -> list[Path]:
        """Generate this working directory's VS Code run/debug configuration, if `solution_language`
           has any, and write it into the workspace.

           What's generated is the same for every working directory of that language, so this is run
           once per language rather than once per directory, and nothing here goes stale when test
           cases or the solution language change.

        Args:
            solution_language: The language `data/solution.src` is written in.
            workspace_root:    Where `.vscode/` goes. Defaults to `find_workspace_root()`--VS Code
                                reads `launch.json` only from the workspace *root*, which is often
                                not this working directory (see `codingame_tools.language.vscode`).
            force:             Overwrite an existing config file that isn't strict JSON (i.e. uses
                                JSONC comments) instead of refusing.
            debug_adapter_logging:
                                Generate a configuration that logs the debug adapter's own
                                 protocol exchange--see `CgVsCodeRequest`.
            check:             Report what *would* change without touching anything. This is how
                                staleness is detected: generated entries carry no version stamp, so
                                "would rewriting change anything?" is the whole question, and it
                                stays correct when a future release alters what gets generated.

        Returns:
            Every path that changed (or, under `check`, would change), in write order. Empty means
            already up to date, or that this language has no VS Code integration.

        Raises:
            CgVsCodeMergeError: if an existing config file can't be safely merged into.
        """
        resolved_workspace_root = (
                Path(workspace_root).resolve() if workspace_root is not None
                else find_workspace_root(self.contribution_dir)
            )
        request = CgVsCodeRequest(
                ctx=self.language_context(
                        solution_language, mount_root=resolved_workspace_root),
                workspace_root=resolved_workspace_root,
                debug_adapter_logging=debug_adapter_logging,
            )
        provisioning = await get_language(solution_language).build_vscode_provisioning(request)
        if provisioning is None:
            return []
        return write_provisioning(
                provisioning, root=self.contribution_dir,
                workspace_root=resolved_workspace_root, language=solution_language, force=force, dry_run=check)

    async def start_debug_session(
                self,
                solution_language: CgSolutionLanguage,
                ordinal: str,
                side: str,
                *,
                timeout: float = DEFAULT_BUILD_TIMEOUT_SECONDS,
            ) -> CgDebugSession:
        """Get the solution ready for a debugger to attach to, fed by the given test case's input--
           see `codingame_tools.language.CgLanguage.start_debug_session`.

           Only meaningful for a language whose debugger attaches to a running target (C++ via
           gdbserver). Python3's debugger launches the program itself, so it never calls this.

        Raises:
            CgContributionManagerError: if no test case matches `ordinal`/`side`.
            CgLanguageOperationNotSupportedError: if this language has no attach-style debugging.
        """
        matching = self.list_local_tests(
                [ordinal], local=side == "local", validator=side == "validator")
        if not matching:
            raise CgContributionManagerError(
                    f"No {side} test case with ordinal {ordinal!r} under {self.tests_dir}.")
        ctx = self.language_context(solution_language)
        # `input_text`, not `input_file`: the file carries a final newline this client added,
        # which isn't part of the value and must not reach the solution--see common.text_files.
        return await get_language(solution_language).start_debug_session(
                ctx, matching[0].input_text, timeout=timeout)

    async def stop_debug_session(self, solution_language: CgSolutionLanguage) -> None:
        """Tear down whatever `start_debug_session()` started. Safe to call when nothing is
           running."""
        await get_language(solution_language).stop_debug_session(
                self.language_context(solution_language))

    async def build_solution(
                self,
                solution_language: CgSolutionLanguage,
                *,
                profile: CgBuildProfile = "run",
                timeout: float = DEFAULT_BUILD_TIMEOUT_SECONDS,
            ) -> CgBuildResult:
        """Build `data/solution.src` for local execution, if `solution_language` needs building at
           all (Python3 doesn't--this is then an immediate no-op success).

           A separate step from `run_local_test()` so a caller can display build diagnostics apart
           from program output, report a compile error once rather than once per test case, and give
           building its own generous timeout. Cheap to call repeatedly: an unchanged source since the
           last successful build returns `up_to_date=True` having done nothing.

           `run_local_tests()` calls this for you. A caller driving `run_local_test()` itself (as
           `cg contribution play` does, to stream results) must call this first.

        Returns:
            A `CgBuildResult`--check `.ok`; a build failure is reported, never raised.
        """
        ctx = self.language_context(solution_language)
        return await get_language(solution_language).build(ctx, profile=profile, timeout=timeout)

    async def run_local_test(
                self,
                test_case: CgContributionLocalTestCase,
                solution_language: CgSolutionLanguage,
                *,
                update_expected: bool = False,
                timeout: float = DEFAULT_RUN_TIMEOUT_SECONDS,
            ) -> CgContributionLocalTestResult:
        """Run `data/solution.src` against one local test case's input, entirely locally--no
           network access at all--by shelling out to the appropriate interpreter/compiler as a
           subprocess (see `codingame_tools.language.CgLanguage.run`).

           Never raises just because the test failed (crashed, timed out, or mismatched)--that's
           reflected in the returned result's `passed`, for a caller running a batch of these to
           collect and report on afterward (see `cg contribution play`, which is also where
           "a test raising an unexpected exception" is caught and turned into a result with
           `exception` set--this method itself doesn't do that, since it only ever runs one test).

           **Does not build.** For a language that needs compiling, call `build_solution()` first
           (`run_local_tests()` does this for you); this method only runs the already-built
           artifact.

        Args:
            test_case:         Which test case to run (see `list_local_tests`).
            solution_language: The language to run `data/solution.src` as (see
                                `CgContributionView.data.solution_language`).
            update_expected:   If True, overwrite `test_case.output_file` with the solution's
                                actual output instead of comparing against it--for accepting the
                                solution's current behavior as the new known-good baseline. Only
                                written if the run completed without crashing/timing out.
            timeout:            Wall-clock timeout in seconds--see `codingame_tools.language.
                                DEFAULT_RUN_TIMEOUT_SECONDS`.

        Returns:
            The outcome--see `CgContributionLocalTestResult`.

        Raises:
            CgLanguageOperationNotSupportedError: if `solution_language` isn't yet supported by
                                                   `codingame_tools.language`.
        """
        ctx = self.language_context(solution_language)
        run_result = await get_language(solution_language).run(
                ctx, test_case.input_text, timeout=timeout)
        ok = not run_result.timed_out and run_result.returncode == 0
        if update_expected:
            if ok:
                test_case.output_file.write_text(run_result.output, encoding="utf-8")
            expected_output = run_result.output if ok else test_case.output_text
            return CgContributionLocalTestResult(
                    ordinal=test_case.ordinal, side=test_case.side, title=test_case.title,
                    passed=ok, updated=ok, input=test_case.input_text,
                    expected_output=expected_output, actual_output=run_result.output,
                    stderr=run_result.stderr, timed_out=run_result.timed_out,
                    returncode=run_result.returncode,
                )
        passed = ok and outputs_match(run_result.output, test_case.output_text)
        return CgContributionLocalTestResult(
                ordinal=test_case.ordinal, side=test_case.side, title=test_case.title,
                passed=passed, updated=False, input=test_case.input_text,
                expected_output=test_case.output_text, actual_output=run_result.output,
                stderr=run_result.stderr, timed_out=run_result.timed_out,
                returncode=run_result.returncode,
            )

    async def run_local_tests(
                self,
                test_cases: list[CgContributionLocalTestCase],
                solution_language: CgSolutionLanguage,
                *,
                update_expected: bool = False,
                timeout: float = DEFAULT_RUN_TIMEOUT_SECONDS,
                build_timeout: float = DEFAULT_BUILD_TIMEOUT_SECONDS,
            ) -> list[CgContributionLocalTestResult]:
        """Convenience batch wrapper: call `build_solution()` once, then run every test case in
           `test_cases` (e.g. from `list_local_tests`) via `run_local_test`, and raise if any
           failed--for programmatic callers that just want a pass/fail outcome without
           `cg contribution play`'s own interleaved per-test console output (which needs its own
           loop, to catch and continue past an unexpected exception for one test case rather than
           aborting the whole batch--see the CLI command itself for that version).

        Returns:
            One `CgContributionLocalTestResult` per test case, in `test_cases`' order.

        Raises:
            CgContributionBuildFailedError: if the solution failed to build--carries the build
                                             output; no test case is run.
            CgLanguageOperationNotSupportedError: if `solution_language` isn't yet supported--
                                                   raised immediately, from whichever test case
                                                   hits it first (every other test case would fail
                                                   identically, so this doesn't run the rest first).
            CgContributionLocalTestFailedError: if any test case failed--carries every result via
                                                 `.results`.
        """
        build_result = await self.build_solution(solution_language, timeout=build_timeout)
        if not build_result.ok:
            raise CgContributionBuildFailedError(build_result)
        results = [
                await self.run_local_test(tc, solution_language, update_expected=update_expected, timeout=timeout)
                for tc in test_cases
            ]
        if any(not r.passed for r in results):
            raise CgContributionLocalTestFailedError(results)
        return results
