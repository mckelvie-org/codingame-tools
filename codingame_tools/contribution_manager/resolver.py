"""Discovery of the contribution working directory--analogous to `codingame_tools.config`'s
   config.yaml discovery, but much simpler: no upward search, no global per-user fallback. A
   contribution working directory is inherently a local, per-task thing (like a git working
   directory), not shared/global state.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from .layout import DATA_SUBDIR_NAME, SOLUTION_FILE_STEM
from .schema import CONTRIBUTION_IDENTITY_FILE_NAME

if TYPE_CHECKING:
    from ..settings import CgSettings

__all__ = [
    "CG_CONTRIBUTION_DIR_ENV_VAR",
    "DEFAULT_CONTRIBUTION_SUBDIR_NAME",
    "CgContributionDirNotFoundError",
    "CgContributionDirInferenceError",
    "find_contribution_dir",
    "resolve_contribution_dir",
    "infer_contribution_dir",
]

CG_CONTRIBUTION_DIR_ENV_VAR = "CG_CONTRIBUTION_DIR"
"""Environment variable that can override contribution-dir discovery, same as an explicit
   `--contribution-dir` CLI flag (parsing/wiring that flag is the CLI layer's job--this module
   just accepts the resolved `explicit` value)."""

DEFAULT_CONTRIBUTION_SUBDIR_NAME = "contribution"
"""Name of the subdirectory of the current directory checked as a last-resort discovery step."""


class CgContributionDirNotFoundError(Exception):
    """Raised by `resolve_contribution_dir()` (unless `allow_default=True`) when no contribution
       working directory could be located by any discovery step. Does not indicate a bug--this is
       the normal outcome before a contribution has been imported/started in the current
       directory."""

    def __init__(self) -> None:
        super().__init__(
                "No contribution working directory found (checked the current directory and "
                "\"./contribution\" for a contribution.json). Pass an explicit directory, set "
                f"{CG_CONTRIBUTION_DIR_ENV_VAR}, or run `cg settings set contribution-dir DIR`."
            )


def find_contribution_dir(
            explicit: Path | str | None = None,
            *,
            settings: CgSettings | None = None,
            start_dir: Path | str | None = None,
        ) -> Path | None:
    """Locate the contribution working directory to use, following the documented discovery
       precedence:

        1. `explicit` (typically the resolved value of a `--contribution-dir` CLI flag), if given.
        2. The `CG_CONTRIBUTION_DIR` environment variable, if set.
        3. `settings.current_contribution_dir`--the *active* working directory, set by
           `cg contribution import`/`create` and `cg contribution activate`. Outranks the configured default
           below so that creating a working directory somewhere isn't silently overridden by a
           standing `contribution_dir` preference pointing elsewhere.
        4. `settings.contribution_dir` (see `CgSettings.contribution_dir`), if given and set.
        5. `start_dir` (or the current directory, if not given), if it contains a
           `contribution.json`.
        6. `start_dir / "contribution"`, if it contains a `contribution.json`.

       Steps 1-4 are taken at face value--the resolved directory need not contain a
       `contribution.json` yet (e.g. a fresh, empty target directory for `cg contribution
       import`). Steps 5-6 are implicit inference and are deliberately conservative: they only
       match if a `contribution.json` is actually already there.

    Returns:
        The resolved contribution directory path, or None if nothing was found at all. This
        function never creates anything.
    """
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    env_value = os.environ.get(CG_CONTRIBUTION_DIR_ENV_VAR)
    if env_value:
        return Path(env_value).expanduser().resolve()
    if settings is not None and settings.current_contribution_dir is not None:
        return settings.current_contribution_dir
    if settings is not None and settings.contribution_dir is not None:
        return settings.contribution_dir
    start = Path(start_dir).resolve() if start_dir is not None else Path.cwd()
    if (start / CONTRIBUTION_IDENTITY_FILE_NAME).is_file():
        return start
    default_subdir = start / DEFAULT_CONTRIBUTION_SUBDIR_NAME
    if (default_subdir / CONTRIBUTION_IDENTITY_FILE_NAME).is_file():
        return default_subdir
    return None


def resolve_contribution_dir(
            explicit: Path | str | None = None,
            *,
            settings: CgSettings | None = None,
            start_dir: Path | str | None = None,
            allow_default: bool = False,
        ) -> Path:
    """Locate the contribution working directory, following the discovery precedence documented
       on `find_contribution_dir`.

       If `allow_default` is True and no directory can be found, falls back to `start_dir` (or the
       current directory)--useful for commands like `cg contribution import` that are happy to
       treat "nothing found" as "use the current directory as the new working directory".
       `push()`-style callers, where there must already be a working directory, should leave
       this False.

    Raises:
        CgContributionDirNotFoundError: if no directory could be located anywhere, and
                                         `allow_default` is False.
    """
    found = find_contribution_dir(explicit, settings=settings, start_dir=start_dir)
    if found is not None:
        return found
    if allow_default:
        return Path(start_dir).resolve() if start_dir is not None else Path.cwd()
    raise CgContributionDirNotFoundError()


class CgContributionDirInferenceError(Exception):
    """Raised by `infer_contribution_dir` when `target_file` doesn't resolve into a contribution
       working directory."""


def infer_contribution_dir(target_file: Path | str) -> Path:
    """Infer a contribution working directory's root from a solution file somewhere within it--
       see `codingame_tools.puzzle_manager.resolver.infer_puzzle_dir`'s docstring for the full
       rationale (identical here, just `contribution.json` instead of `puzzle.json`): the only
       two things ever promised about `target_file` are that a debugger's breakpoints bind to
       whatever path was actually open in the editor, and that resolving every symlink in it
       always eventually lands on `data/solution.src`--so this isn't a search, it's two fixed
       path segments up from the fully-resolved `target_file`.

    Raises:
        CgContributionDirInferenceError: if `target_file`, once fully resolved, isn't
                                          `.../data/solution.src`, or `contribution.json` isn't
                                          present at the inferred root.
    """
    resolved = Path(target_file).resolve()
    # Matched on the stem, not the full name: the solution file carries its language's extension
    # and is renamed when the language changes, so `solution.cpp` and `solution.py` are equally
    # valid here and the set of legal names is open-ended.
    if resolved.stem != SOLUTION_FILE_STEM or resolved.parent.name != DATA_SUBDIR_NAME:
        raise CgContributionDirInferenceError(
                f"{target_file} does not resolve to a {DATA_SUBDIR_NAME}/{SOLUTION_FILE_STEM}.* "
                "file--not part of a contribution working directory."
            )
    root = resolved.parent.parent
    if not (root / CONTRIBUTION_IDENTITY_FILE_NAME).is_file():
        raise CgContributionDirInferenceError(
                f"{root} has no {CONTRIBUTION_IDENTITY_FILE_NAME}--not a contribution working directory.")
    return root
