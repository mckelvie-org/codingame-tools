"""Local working-directory management for solving existing CodinGame puzzles--much simpler than
   `codingame_tools.contribution_manager` (which authors/edits a contribution): exactly one file
   (`data/solution.src`) is ever editable, so there is no git repository involved at all.

   See `CgPuzzleManager` for `import_`/`repair`/`diff`/`discard_local`/`submit`/`play`/`play_local`/
   `status`/`delete`--its module docstring covers the three-way state-split design in full.
   `play`/`play_local` each have a `resolve_*`/`*_one` pair too (`resolve_play_indices`/
   `play_one`, `resolve_play_local_test_cases`/`play_local_one`), for a caller (e.g. `cg puzzle
   play-server`/`cg puzzle play`'s own CLI handlers) that wants to act on each result as it comes
   in rather than waiting for the whole batch.
   `codingame_tools.puzzle_manager.schema` for the working directory's three manifest files
   (`CgPuzzleIdentity`/`CgPuzzleServerData`/`CgPuzzleData`);
   `codingame_tools.puzzle_manager.statement_render` for rendering the cached HTML statement
   (`CgPuzzleManager.load_statement_html`) into display blocks, used by `cg puzzle description`;
   `codingame_tools.test_runner` (a separate, package-agnostic package--not `puzzle_manager`'s
   own) for how `play_local`/VS Code debugging actually run a solution; and
   `codingame_tools.puzzle_manager.resolver` for how a puzzle directory is located.
"""

from __future__ import annotations

from ..client.common.protocol.report import CgSubmissionReport
from .manager import (
    DATA_SUBDIR_NAME,
    META_SUBDIR_NAME,
    SOLUTION_FILE_STEM,
    STATEMENT_FILE_NAME,
    STUB_GENERATOR_FILE_NAME,
    TESTS_SUBDIR_NAME,
    CgPuzzleBuildFailedError,
    CgPuzzleDiscardResult,
    CgPuzzleLocalTestFailedError,
    CgPuzzleLocalTestResult,
    CgPuzzleManager,
    CgPuzzleManagerError,
    CgPuzzleRemoteTestResult,
    CgPuzzleSetLanguageResult,
    CgPuzzleStatus,
    find_solution_file,
    solution_file_name,
)
from .resolver import (
    CG_PUZZLE_DIR_ENV_VAR,
    DEFAULT_PUZZLE_SUBDIR_NAME,
    CgPuzzleDirInferenceError,
    CgPuzzleDirNotFoundError,
    find_puzzle_dir,
    infer_puzzle_dir,
    resolve_puzzle_dir,
)
from .schema import (
    PUZZLE_IDENTITY_FILE_NAME,
    PUZZLE_SCHEMA_VERSION,
    CgPuzzleData,
    CgPuzzleIdentity,
    CgPuzzleServerData,
)
from .statement_render import CgStatementBlock, parse_statement_html

__all__ = [
    "CgPuzzleManager",
    "CgPuzzleManagerError",
    "CgPuzzleDiscardResult",
    "CgPuzzleSetLanguageResult",
    "CgPuzzleLocalTestResult",
    "CgPuzzleBuildFailedError",
    "CgPuzzleLocalTestFailedError",
    "CgPuzzleRemoteTestResult",
    "CgPuzzleStatus",
    "CgPuzzleIdentity",
    "CgPuzzleServerData",
    "CgPuzzleData",
    "CgSubmissionReport",
    "CgStatementBlock",
    "parse_statement_html",
    "PUZZLE_IDENTITY_FILE_NAME",
    "PUZZLE_SCHEMA_VERSION",
    "DATA_SUBDIR_NAME",
    "META_SUBDIR_NAME",
    "SOLUTION_FILE_STEM",
    "find_solution_file",
    "solution_file_name",
    "STATEMENT_FILE_NAME",
    "STUB_GENERATOR_FILE_NAME",
    "TESTS_SUBDIR_NAME",
    "CgPuzzleDirNotFoundError",
    "CgPuzzleDirInferenceError",
    "find_puzzle_dir",
    "resolve_puzzle_dir",
    "infer_puzzle_dir",
    "CG_PUZZLE_DIR_ENV_VAR",
    "DEFAULT_PUZZLE_SUBDIR_NAME",
]
