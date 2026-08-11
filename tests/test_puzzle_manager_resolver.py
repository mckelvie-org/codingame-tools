"""Unit tests for codingame_tools.puzzle_manager.resolver: puzzle working directory discovery
   precedence (explicit > CG_PUZZLE_DIR > settings > cwd > ./puzzle).

These are pure/local tests--no network--so they run under the default `pdm run test` invocation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codingame_tools.config.cg_config import CgConfigData
from codingame_tools.config.resolver import CgConfig
from codingame_tools.puzzle_manager.layout import DATA_SUBDIR_NAME, solution_file_name
from codingame_tools.puzzle_manager.resolver import (
    CG_PUZZLE_DIR_ENV_VAR,
    DEFAULT_PUZZLE_SUBDIR_NAME,
    CgPuzzleDirInferenceError,
    CgPuzzleDirNotFoundError,
    find_puzzle_dir,
    infer_puzzle_dir,
    resolve_puzzle_dir,
)
from codingame_tools.puzzle_manager.schema import PUZZLE_IDENTITY_FILE_NAME
from codingame_tools.settings import CgSettings, CgSettingsData

SOLUTION_FILE_NAME = solution_file_name("py")
"""These tests all write Python solutions, so the solution file is `solution.py`. The name is no
   longer fixed: it carries the language's extension and is renamed when the language changes."""


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CG_PUZZLE_DIR_ENV_VAR, raising=False)


def _settings_with_puzzle_dir(value: str | None, tmp_path: Path) -> CgSettings:
    config = CgConfig(config_file=tmp_path / "config.yaml", raw_data=CgConfigData())
    return CgSettings(
            settings_file=tmp_path / "settings.json",
            raw_data=CgSettingsData(puzzle_dir=value),
            config=config,
        )


def test_explicit_wins_even_without_a_manifest_file(tmp_path: Path) -> None:
    target = tmp_path / "fresh-empty-dir"
    assert find_puzzle_dir(target) == target.resolve()


def test_env_var_used_when_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CG_PUZZLE_DIR_ENV_VAR, str(tmp_path / "from-env"))
    assert find_puzzle_dir() == (tmp_path / "from-env").resolve()


def test_explicit_overrides_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CG_PUZZLE_DIR_ENV_VAR, str(tmp_path / "from-env"))
    explicit = tmp_path / "explicit"
    assert find_puzzle_dir(explicit) == explicit.resolve()


def test_settings_used_when_no_explicit_or_env(tmp_path: Path) -> None:
    settings = _settings_with_puzzle_dir(str(tmp_path / "from-settings"), tmp_path)
    assert find_puzzle_dir(settings=settings) == (tmp_path / "from-settings").resolve()


def test_env_overrides_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CG_PUZZLE_DIR_ENV_VAR, str(tmp_path / "from-env"))
    settings = _settings_with_puzzle_dir(str(tmp_path / "from-settings"), tmp_path)
    assert find_puzzle_dir(settings=settings) == (tmp_path / "from-env").resolve()


def test_cwd_used_when_it_contains_manifest(tmp_path: Path) -> None:
    (tmp_path / PUZZLE_IDENTITY_FILE_NAME).write_text("{}")
    assert find_puzzle_dir(start_dir=tmp_path) == tmp_path


def test_puzzle_subdir_used_when_it_contains_manifest(tmp_path: Path) -> None:
    sub = tmp_path / DEFAULT_PUZZLE_SUBDIR_NAME
    sub.mkdir()
    (sub / PUZZLE_IDENTITY_FILE_NAME).write_text("{}")
    assert find_puzzle_dir(start_dir=tmp_path) == sub


def test_cwd_preferred_over_puzzle_subdir(tmp_path: Path) -> None:
    (tmp_path / PUZZLE_IDENTITY_FILE_NAME).write_text("{}")
    sub = tmp_path / DEFAULT_PUZZLE_SUBDIR_NAME
    sub.mkdir()
    (sub / PUZZLE_IDENTITY_FILE_NAME).write_text("{}")
    assert find_puzzle_dir(start_dir=tmp_path) == tmp_path


def test_returns_none_when_nothing_found(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert find_puzzle_dir(start_dir=empty) is None


def test_settings_with_no_override_falls_through_to_cwd_check(tmp_path: Path) -> None:
    (tmp_path / PUZZLE_IDENTITY_FILE_NAME).write_text("{}")
    settings = _settings_with_puzzle_dir(None, tmp_path)
    assert find_puzzle_dir(settings=settings, start_dir=tmp_path) == tmp_path


# --- resolve_puzzle_dir ---------------------------------------------------------------------


def test_resolve_raises_not_found_without_allow_default(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(CgPuzzleDirNotFoundError):
        resolve_puzzle_dir(start_dir=empty)


def test_resolve_allow_default_falls_back_to_puzzle_subdir_not_bare_start_dir(tmp_path: Path) -> None:
    """Confirmed live (2026-07-30): falling back to bare start_dir/cwd is a real footgun for `cg
       puzzle import`'s everyday no-argument usage (unlike a contribution working directory,
       whose own `import` always requires an explicit target and so never actually exercises this
       fallback)--it silently dropped puzzle.json/data/ directly into the current directory."""
    empty = tmp_path / "empty"
    empty.mkdir()
    assert resolve_puzzle_dir(start_dir=empty, allow_default=True) == empty / DEFAULT_PUZZLE_SUBDIR_NAME


def test_resolve_allow_default_still_prefers_a_real_match(tmp_path: Path) -> None:
    (tmp_path / PUZZLE_IDENTITY_FILE_NAME).write_text("{}")
    assert resolve_puzzle_dir(start_dir=tmp_path, allow_default=True) == tmp_path


# --- infer_puzzle_dir -------------------------------------------------------------------------


def _make_puzzle_dir(root: Path) -> Path:
    data_dir = root / DATA_SUBDIR_NAME
    data_dir.mkdir(parents=True)
    (root / PUZZLE_IDENTITY_FILE_NAME).write_text("{}")
    (data_dir / SOLUTION_FILE_NAME).write_text("print('hi')\n")
    return root


def test_infer_from_solution_src_directly(tmp_path: Path) -> None:
    puzzle_dir = _make_puzzle_dir(tmp_path / "puzzle")
    assert infer_puzzle_dir(puzzle_dir / DATA_SUBDIR_NAME / SOLUTION_FILE_NAME) == puzzle_dir


def test_infer_from_symlink_inside_puzzle_dir(tmp_path: Path) -> None:
    """The working directory's own solution.<ext> convenience symlink."""
    puzzle_dir = _make_puzzle_dir(tmp_path / "puzzle")
    link = puzzle_dir / "solution.py"
    link.symlink_to(Path(DATA_SUBDIR_NAME) / SOLUTION_FILE_NAME)
    assert infer_puzzle_dir(link) == puzzle_dir


def test_infer_from_symlink_entirely_outside_the_puzzle_dir(tmp_path: Path) -> None:
    """The only two guarantees about the debugged file: breakpoints bind to wherever it was
       opened from (which might not be anywhere near the puzzle dir), and resolving it always
       lands on data/solution.src. A symlink living in some unrelated directory must still work--
       this is *why* inference is based on the resolved target, not on walking up from wherever
       the symlink itself happens to live."""
    puzzle_dir = _make_puzzle_dir(tmp_path / "puzzle")
    elsewhere = tmp_path / "some" / "unrelated" / "place"
    elsewhere.mkdir(parents=True)
    link = elsewhere / "my_solution.py"
    link.symlink_to(puzzle_dir / DATA_SUBDIR_NAME / SOLUTION_FILE_NAME)
    assert infer_puzzle_dir(link) == puzzle_dir


def test_infer_through_a_chain_of_symlinks(tmp_path: Path) -> None:
    puzzle_dir = _make_puzzle_dir(tmp_path / "puzzle")
    hop1 = tmp_path / "hop1.py"
    hop1.symlink_to(puzzle_dir / DATA_SUBDIR_NAME / SOLUTION_FILE_NAME)
    hop2 = tmp_path / "hop2.py"
    hop2.symlink_to(hop1)
    assert infer_puzzle_dir(hop2) == puzzle_dir


def test_infer_refuses_a_file_not_named_solution_src(tmp_path: Path) -> None:
    puzzle_dir = _make_puzzle_dir(tmp_path / "puzzle")
    other_file = puzzle_dir / DATA_SUBDIR_NAME / "not-the-solution.txt"
    other_file.write_text("irrelevant")
    with pytest.raises(CgPuzzleDirInferenceError):
        infer_puzzle_dir(other_file)


def test_infer_refuses_without_puzzle_json_at_the_inferred_root(tmp_path: Path) -> None:
    data_dir = tmp_path / "not-a-puzzle" / DATA_SUBDIR_NAME
    data_dir.mkdir(parents=True)
    solution_file = data_dir / SOLUTION_FILE_NAME
    solution_file.write_text("print('hi')\n")
    with pytest.raises(CgPuzzleDirInferenceError):
        infer_puzzle_dir(solution_file)


# --- active ("current") puzzle directory --------------------------------------------------------
#
# `cg puzzle import`/`activate` record what you're working on right now, distinct from the standing
# `puzzleDir` preference. Without that, configuring `puzzleDir` and then importing somewhere else
# would send every following command to the configured directory instead -- the one place the user
# isn't looking.


def _settings_with_dirs(
            tmp_path: Path, *, puzzle_dir: str | None = None, current_puzzle_dir: str | None = None,
        ) -> CgSettings:
    config = CgConfig(config_file=tmp_path / "config.yaml", raw_data=CgConfigData())
    return CgSettings(
            settings_file=tmp_path / "settings.json",
            raw_data=CgSettingsData(puzzle_dir=puzzle_dir, current_puzzle_dir=current_puzzle_dir),
            config=config,
        )


def test_current_puzzle_dir_used_when_set(tmp_path: Path) -> None:
    settings = _settings_with_dirs(tmp_path, current_puzzle_dir="active")

    assert find_puzzle_dir(settings=settings, start_dir=tmp_path) == (tmp_path / "active").resolve()


def test_current_puzzle_dir_outranks_the_configured_default(tmp_path: Path) -> None:
    """The whole point of the setting: a standing preference must not hijack the directory you just
       imported into."""
    settings = _settings_with_dirs(tmp_path, puzzle_dir="configured", current_puzzle_dir="active")

    assert find_puzzle_dir(settings=settings, start_dir=tmp_path) == (tmp_path / "active").resolve()


def test_configured_default_used_once_deactivated(tmp_path: Path) -> None:
    settings = _settings_with_dirs(tmp_path, puzzle_dir="configured", current_puzzle_dir=None)

    assert find_puzzle_dir(settings=settings, start_dir=tmp_path) == (tmp_path / "configured").resolve()


def test_explicit_and_env_still_outrank_the_active_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings_with_dirs(tmp_path, current_puzzle_dir="active")
    explicit = tmp_path / "explicit"

    assert find_puzzle_dir(explicit, settings=settings, start_dir=tmp_path) == explicit.resolve()

    monkeypatch.setenv(CG_PUZZLE_DIR_ENV_VAR, str(tmp_path / "from-env"))
    assert find_puzzle_dir(settings=settings, start_dir=tmp_path) == (tmp_path / "from-env").resolve()


def test_discovery_matches_the_contribution_resolver(tmp_path: Path) -> None:
    """The two resolvers are meant to be the same algorithm with different filenames. Asserted
       because they're separate modules and have drifted apart before."""
    from codingame_tools.contribution_manager.resolver import find_contribution_dir
    from codingame_tools.contribution_manager.schema import CONTRIBUTION_IDENTITY_FILE_NAME

    for identity, subdir, finder in (
                (PUZZLE_IDENTITY_FILE_NAME, "puzzle", find_puzzle_dir),
                (CONTRIBUTION_IDENTITY_FILE_NAME, "contribution", find_contribution_dir),
            ):
        root = tmp_path / f"{subdir}-case"
        # cwd holding the identity file wins...
        (root / subdir).mkdir(parents=True)
        (root / identity).write_text("{}")
        (root / subdir / identity).write_text("{}")
        assert finder(start_dir=root) == root.resolve()
        # ...and the conventional subdirectory is the fallback.
        (root / identity).unlink()
        assert finder(start_dir=root) == (root / subdir).resolve()
