"""Discovery of the puzzle working directory--analogous to
   `codingame_tools.contribution_manager.resolver`, and just as simple: no upward search, no
   global per-user fallback. A puzzle working directory is a local, per-task thing, not shared/
   global state.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from .layout import DATA_SUBDIR_NAME, SOLUTION_FILE_STEM
from .schema import PUZZLE_IDENTITY_FILE_NAME

if TYPE_CHECKING:
    from ..settings import CgSettings

__all__ = [
    "CG_PUZZLE_DIR_ENV_VAR",
    "DEFAULT_PUZZLE_SUBDIR_NAME",
    "CgPuzzleDirNotFoundError",
    "CgPuzzleDirInferenceError",
    "find_puzzle_dir",
    "resolve_puzzle_dir",
    "infer_puzzle_dir",
]

CG_PUZZLE_DIR_ENV_VAR = "CG_PUZZLE_DIR"
"""Environment variable that can override puzzle-dir discovery, same as an explicit
   `--puzzle-dir` CLI flag (parsing/wiring that flag is the CLI layer's job--this module just
   accepts the resolved `explicit` value)."""

DEFAULT_PUZZLE_SUBDIR_NAME = "puzzle"
"""Name of the subdirectory of the current directory checked as a last-resort discovery step."""


class CgPuzzleDirNotFoundError(Exception):
    """Raised by `resolve_puzzle_dir()` (unless `allow_default=True`) when no puzzle working
       directory could be located by any discovery step. Does not indicate a bug--this is the
       normal outcome before a puzzle has been imported in the current directory."""

    def __init__(self) -> None:
        super().__init__(
                "No puzzle working directory found (checked the current directory and "
                "\"./puzzle\" for a puzzle.json). Pass an explicit directory, set "
                f"{CG_PUZZLE_DIR_ENV_VAR}, or run `cg settings set puzzle-dir DIR`."
            )


def find_puzzle_dir(
            explicit: Path | str | None = None,
            *,
            settings: CgSettings | None = None,
            start_dir: Path | str | None = None,
        ) -> Path | None:
    """Locate the puzzle working directory to use, following the documented discovery
       precedence:

        1. `explicit` (typically the resolved value of a `--puzzle-dir` CLI flag), if given.
        2. The `CG_PUZZLE_DIR` environment variable, if set.
        3. `settings.current_puzzle_dir`--the *active* working directory, set by
           `cg puzzle import`/`create` and `cg puzzle activate`. Outranks the configured default
           below so that creating a working directory somewhere isn't silently overridden by a
           standing `puzzle_dir` preference pointing elsewhere.
        4. `settings.puzzle_dir` (see `CgSettings.puzzle_dir`), if given and set.
        5. `start_dir` (or the current directory, if not given), if it contains a `puzzle.json`.
        6. `start_dir / "puzzle"`, if it contains a `puzzle.json`.

       Steps 1-4 are taken at face value--the resolved directory need not contain a `puzzle.json`
       yet (e.g. before the first `cg puzzle import`). Steps 5-6 are implicit inference and are
       deliberately conservative: they only match if a `puzzle.json` is actually already there.

    Returns:
        The resolved puzzle directory path, or None if nothing was found at all. This function
        never creates anything.
    """
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    env_value = os.environ.get(CG_PUZZLE_DIR_ENV_VAR)
    if env_value:
        return Path(env_value).expanduser().resolve()
    if settings is not None and settings.current_puzzle_dir is not None:
        return settings.current_puzzle_dir
    if settings is not None and settings.puzzle_dir is not None:
        return settings.puzzle_dir
    start = Path(start_dir).resolve() if start_dir is not None else Path.cwd()
    if (start / PUZZLE_IDENTITY_FILE_NAME).is_file():
        return start
    default_subdir = start / DEFAULT_PUZZLE_SUBDIR_NAME
    if (default_subdir / PUZZLE_IDENTITY_FILE_NAME).is_file():
        return default_subdir
    return None


def resolve_puzzle_dir(
            explicit: Path | str | None = None,
            *,
            settings: CgSettings | None = None,
            start_dir: Path | str | None = None,
            allow_default: bool = False,
        ) -> Path:
    """Locate the puzzle working directory, following the discovery precedence documented on
       `find_puzzle_dir`.

       If `allow_default` is True and no directory can be found, falls back to
       `start_dir / "puzzle"` (or `./puzzle` under the current directory)--useful for `cg puzzle
       import`, which is happy to treat "nothing found" as "start a fresh working directory
       there". Deliberately *not* bare `start_dir`/cwd itself--unlike a contribution working
       directory (whose own `import` always requires an explicit target directory, so its
       resolver's `allow_default` fallback is never actually exercised in practice), `cg puzzle
       import` relies on this fallback for its everyday no-argument usage, and dropping
       `puzzle.json`/`data/` directly into whatever the current directory happens to be would be
       a real footgun--confirmed live (2026-07-30): an earlier version of this fell back to bare
       cwd and did exactly that. `submit()`-style callers, where there must already be a working
       directory, should leave `allow_default` False.

    Raises:
        CgPuzzleDirNotFoundError: if no directory could be located anywhere, and `allow_default`
                                   is False.
    """
    found = find_puzzle_dir(explicit, settings=settings, start_dir=start_dir)
    if found is not None:
        return found
    if allow_default:
        start = Path(start_dir).resolve() if start_dir is not None else Path.cwd()
        return start / DEFAULT_PUZZLE_SUBDIR_NAME
    raise CgPuzzleDirNotFoundError()


class CgPuzzleDirInferenceError(Exception):
    """Raised by `infer_puzzle_dir` when `target_file` doesn't resolve into a puzzle working
       directory."""


def infer_puzzle_dir(target_file: Path | str) -> Path:
    """Infer a puzzle working directory's root from a solution file somewhere within it--e.g. VS
       Code's `${file}` macro, however many symlink hops away from `data/solution.src` it might
       be (a puzzle working directory's own `solution.<ext>` convenience symlink, or some other
       symlink elsewhere entirely that a user set up themselves--see `codingame_tools.
       puzzle_manager.manager`'s module docstring). The only two things ever promised about
       `target_file`: a debugger's breakpoints bind to whatever path was actually open in the
       editor (so this function must not need that path to be anything in particular), and
       resolving every symlink in it always eventually lands on `data/solution.src`.

       So this isn't a search: fully resolving `target_file` (following every symlink to its real
       target) always lands on `<root>/data/solution.src`--`DATA_SUBDIR_NAME`/`SOLUTION_FILE_NAME`
       are fixed constants, not configurable--so `<root>` is deterministically two path segments
       up from there. Confirmed by requiring `puzzle.json` to actually exist at that root, so a
       `target_file` that isn't part of any puzzle working directory at all fails clearly rather
       than returning a nonsense path.

    Raises:
        CgPuzzleDirInferenceError: if `target_file`, once fully resolved, isn't
                                    `.../data/solution.src`, or `puzzle.json` isn't present at the
                                    inferred root.
    """
    resolved = Path(target_file).resolve()
    # Matched on the stem, not the full name: the solution file carries its language's extension
    # and is renamed when the language changes, so `solution.cpp` and `solution.py` are equally
    # valid here and the set of legal names is open-ended.
    if resolved.stem != SOLUTION_FILE_STEM or resolved.parent.name != DATA_SUBDIR_NAME:
        raise CgPuzzleDirInferenceError(
                f"{target_file} does not resolve to a {DATA_SUBDIR_NAME}/{SOLUTION_FILE_STEM}.* "
                "file--not part of a puzzle working directory."
            )
    root = resolved.parent.parent
    if not (root / PUZZLE_IDENTITY_FILE_NAME).is_file():
        raise CgPuzzleDirInferenceError(
                f"{root} has no {PUZZLE_IDENTITY_FILE_NAME}--not a puzzle working directory.")
    return root
