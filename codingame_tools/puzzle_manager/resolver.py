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
    "PUZZLES_SUBDIR_NAME",
    "default_puzzles_dir",
    "project_root",
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

PUZZLES_SUBDIR_NAME = "puzzles"
"""Name of the directory under the project root that holds one subdirectory per puzzle.

   `cg puzzle import` creates `<project>/puzzles/<pretty-id>/`. A puzzle's pretty id is already a
   slug (`travelling-salesman`) and unique across the site, so it needs no further munging and two
   puzzles can never collide."""


class CgPuzzleDirNotFoundError(Exception):
    """Raised by `resolve_puzzle_dir()` when no puzzle working directory could be located by any
       discovery step. Does not indicate a bug--this is the normal outcome before any puzzle has
       been imported."""

    def __init__(self) -> None:
        super().__init__(
                "No puzzle working directory found: none is active, and this directory holds "
                "no puzzle.json. Run `cg puzzle import PUZZLE` to make one, `cg puzzle activate "
                "DIR` to select one you already have, pass --puzzle-dir, or set "
                f"{CG_PUZZLE_DIR_ENV_VAR}."
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
           `cg puzzle import` and `cg puzzle activate`. This is what "which puzzle am I working
           on" means now that every import creates its own directory.
        4. `start_dir` (or the current directory) itself, if it holds a `puzzle.json`.

       Steps 1-3 are taken at face value--the resolved directory need not exist yet. Step 4 checks
       for the identity file, and checks that one directory only: no walk upward, and no `./puzzle`
       convention. A puzzle nested under `puzzles/<pretty-id>/` is found by being active, not by
       being nearby, so `cd` never changes which puzzle a command acts on unless you are standing
       exactly in one and none is active.

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
    start = Path(start_dir).resolve() if start_dir is not None else Path.cwd()
    # The current directory itself, and only itself -- no search upward, and no conventional
    # subdirectory. Guarded on the identity file so that running a command from somewhere
    # unrelated reports "no puzzle working directory" rather than failing further in with a
    # confusing complaint about this directory's missing data/.
    if (start / PUZZLE_IDENTITY_FILE_NAME).is_file():
        return start
    return None


def resolve_puzzle_dir(
            explicit: Path | str | None = None,
            *,
            settings: CgSettings | None = None,
            start_dir: Path | str | None = None,
        ) -> Path:
    """Locate the puzzle working directory, following the discovery precedence documented on
       `find_puzzle_dir`.

       There is no "default" directory to fall back to any more: `cg puzzle import` computes its
       own destination from the puzzle's pretty id (see `default_puzzles_dir`), and every other
       command needs a working directory that already exists. The older fallback returned
       `./puzzle`, and the version before that returned bare cwd--which dropped `puzzle.json` and
       `data/` into whatever directory you happened to be standing in (confirmed live
       2026-07-30). Computing the destination from the puzzle's name removes the guess entirely.

    Raises:
        CgPuzzleDirNotFoundError: if no directory could be located anywhere.
    """
    found = find_puzzle_dir(explicit, settings=settings, start_dir=start_dir)
    if found is not None:
        return found
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


def project_root(settings: CgSettings | None = None, *, start_dir: Path | str | None = None) -> Path:
    """The directory the `puzzles/` and `contributions/` trees hang off.

       In order: `settings.project_dir` when configured; the project the *resolved config* belongs
       to, when that config is a project-style `<root>/.cg/config/config.yaml`; the nearest
       ancestor of the current directory holding a `.cg/`; and finally the current directory.

       The config comes before the current directory so that `--config` selects a whole project,
       not merely its settings. Otherwise pointing at another project's config would read that
       project's settings while creating working directories in this one -- and it would disagree
       with `sdk_dir`, which has always derived from the resolved config.

       Anchoring on `.cg/` rather than the current directory is what stops `cg puzzle import` from
       creating a second `puzzles/` tree every time it is run from a subdirectory."""
    from ..config.resolver import PROJECT_CONFIG_MARKER_DIR_NAME

    if settings is not None and settings.project_dir is not None:
        return settings.project_dir
    if settings is not None:
        marker = settings.config.config_dir.parent
        if marker.name == PROJECT_CONFIG_MARKER_DIR_NAME:
            return marker.parent
    start = Path(start_dir).resolve() if start_dir is not None else Path.cwd()
    for candidate in (start, *start.parents):
        if (candidate / PROJECT_CONFIG_MARKER_DIR_NAME).is_dir():
            return candidate
    return start


def default_puzzles_dir(settings: CgSettings | None = None, *,
                        start_dir: Path | str | None = None) -> Path:
    """Where `cg puzzle import` creates working directories: `<project root>/puzzles/`."""
    return project_root(settings, start_dir=start_dir) / PUZZLES_SUBDIR_NAME
