"""Local working-directory management for CodinGame contributions (puzzles)--a real git working
   directory (`data/`), backed by a remote server rather than a git remote.

   See `CgContributionManager` for `import_`/`repair`/`create`/`push`/`rebase`/`fetch`/
   `merge_start`/`merge_continue`/`merge_abort`/`merge_discard_local`/`merge_discard_server`/
   `discard_local`/`delete`/`status`/`read_status_cache`--its module docstring covers the `main`/
   `server`/`version-data` branch design in full, `push()`'s covers the create-vs-update duality
   hidden behind that one method, and `repair()`'s covers reconstructing a missing/corrupted
   git-dir; `codingame_tools.contribution_manager.schema` for the working directory's own
   manifest files (`CgContributionIdentity`/`CgContributionView`/`CgContributionStatusCache`--the
   last one an offline, non-git-tracked cache of server metadata that isn't tied to any content
   version, e.g. votes/comments/the moderator approve-reject gate); `codingame_tools.
   contribution_manager.contribution_commit_data` for `CgContributionCommitMetadata` (the
   git-trailer-backed remote commit metadata) and `redact_commit_contribution`; `codingame_tools.
   contribution_manager.git_repo` for the low-level git plumbing wrapper; and `codingame_tools.
   contribution_manager.resolver` for how a contribution directory is located.
"""

from __future__ import annotations

from .contribution_commit_data import (
    CONTRIBUTION_COMMIT_DATA_FILE_NAME,
    CgContributionCommitMetadata,
    redact_commit_contribution,
)
from .git_repo import CgGitError, CgGitRepo, init_repo, is_inside_existing_repo
from .layout import (
    CONTRIBUTION_META_FILE_NAME,
    CONTRIBUTION_STATUS_CACHE_FILE_NAME,
    COVER_IMAGE_FILE_NAME,
    DATA_SUBDIR_NAME,
    GIT_METADATA_SUBDIR_NAME,
    GITIGNORE_FILE_NAME,
    MAIN_BRANCH_NAME,
    META_SUBDIR_NAME,
    SERVER_BRANCH_NAME,
    SERVER_TAG_PREFIX,
    SOLUTION_FILE_STEM,
    TRAILER_CONTRIBUTION_ID,
    TRAILER_COVER_BINARY_HASH,
    TRAILER_COVER_BINARY_ID,
    TRAILER_VERSION,
    VERSION_DATA_BRANCH_NAME,
    VERSION_DATA_TAG_PREFIX,
    find_solution_file,
    solution_file_name,
)
from .manager import (
    CONSTRAINTS_FILE_NAME,
    CONTRIBUTION_DIFFICULTIES,
    INPUT_DESCRIPTION_FILE_NAME,
    OUTPUT_DESCRIPTION_FILE_NAME,
    STATEMENT_FILE_NAME,
    STUB_GENERATOR_FILE_NAME,
    SUPPORTED_PUZZLE_TYPES,
    CgContributionBuildFailedError,
    CgContributionLocalTestFailedError,
    CgContributionLocalTestResult,
    CgContributionManager,
    CgContributionManagerError,
    CgContributionSetLanguageResult,
    CgContributionStatus,
    CgContributionSyncStatus,
    CgMergeStartResult,
    CgMergeStartStatus,
    CgRebaseStatus,
)
from .resolver import (
    CG_CONTRIBUTION_DIR_ENV_VAR,
    DEFAULT_CONTRIBUTION_SUBDIR_NAME,
    CgContributionDirInferenceError,
    CgContributionDirNotFoundError,
    find_contribution_dir,
    infer_contribution_dir,
    resolve_contribution_dir,
)
from .schema import (
    CONTRIBUTION_DATA_FILE_NAME,
    CONTRIBUTION_IDENTITY_FILE_NAME,
    CONTRIBUTION_SCHEMA_VERSION,
    CgContributionIdentity,
    CgContributionMeta,
    CgContributionStatusCache,
    CgContributionView,
)
from .test_cases_dir import (
    LOCAL_SUBDIR_NAME,
    TEST_META_FILE_NAME,
    TESTS_SUBDIR_NAME,
    VALIDATOR_SUBDIR_NAME,
    CgContributionLocalTestCase,
    CgContributionTestCaseError,
    CgTestCaseFileMeta,
    commit_test_cases,
    import_test_cases,
    list_local_test_cases,
    normalize_test_title,
    renormalize_test_case_dirs,
)

__all__ = [
    "CgContributionManager",
    "CONTRIBUTION_DIFFICULTIES",
    "SUPPORTED_PUZZLE_TYPES",
    "CgContributionManagerError",
    "CgRebaseStatus",
    "CgMergeStartStatus",
    "CgMergeStartResult",
    "CgContributionSyncStatus",
    "CgContributionStatus",
    "CgContributionLocalTestResult",
    "CgContributionBuildFailedError",
    "CgContributionLocalTestFailedError",
    "CgContributionSetLanguageResult",
    "CgContributionLocalTestCase",
    "list_local_test_cases",
    "CgContributionIdentity",
    "CgContributionMeta",
    "CgContributionView",
    "CgContributionStatusCache",
    "CONTRIBUTION_IDENTITY_FILE_NAME",
    "CONTRIBUTION_DATA_FILE_NAME",
    "CONTRIBUTION_META_FILE_NAME",
    "CONTRIBUTION_STATUS_CACHE_FILE_NAME",
    "CONTRIBUTION_SCHEMA_VERSION",
    "CgContributionCommitMetadata",
    "CONTRIBUTION_COMMIT_DATA_FILE_NAME",
    "redact_commit_contribution",
    "STATEMENT_FILE_NAME",
    "INPUT_DESCRIPTION_FILE_NAME",
    "OUTPUT_DESCRIPTION_FILE_NAME",
    "CONSTRAINTS_FILE_NAME",
    "STUB_GENERATOR_FILE_NAME",
    "SOLUTION_FILE_STEM",
    "find_solution_file",
    "solution_file_name",
    "COVER_IMAGE_FILE_NAME",
    "DATA_SUBDIR_NAME",
    "META_SUBDIR_NAME",
    "GIT_METADATA_SUBDIR_NAME",
    "GITIGNORE_FILE_NAME",
    "MAIN_BRANCH_NAME",
    "SERVER_BRANCH_NAME",
    "VERSION_DATA_BRANCH_NAME",
    "SERVER_TAG_PREFIX",
    "VERSION_DATA_TAG_PREFIX",
    "TRAILER_CONTRIBUTION_ID",
    "TRAILER_VERSION",
    "TRAILER_COVER_BINARY_ID",
    "TRAILER_COVER_BINARY_HASH",
    "CgGitError",
    "CgGitRepo",
    "init_repo",
    "is_inside_existing_repo",
    "CgContributionDirNotFoundError",
    "CgContributionDirInferenceError",
    "find_contribution_dir",
    "resolve_contribution_dir",
    "infer_contribution_dir",
    "CG_CONTRIBUTION_DIR_ENV_VAR",
    "DEFAULT_CONTRIBUTION_SUBDIR_NAME",
    "TESTS_SUBDIR_NAME",
    "TEST_META_FILE_NAME",
    "LOCAL_SUBDIR_NAME",
    "VALIDATOR_SUBDIR_NAME",
    "CgContributionTestCaseError",
    "CgTestCaseFileMeta",
    "normalize_test_title",
    "import_test_cases",
    "commit_test_cases",
    "renormalize_test_case_dirs",
]
