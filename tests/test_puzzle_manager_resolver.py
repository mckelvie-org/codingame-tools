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
    PUZZLES_SUBDIR_NAME,
    CgPuzzleDirInferenceError,
    CgPuzzleDirNotFoundError,
    default_puzzles_dir,
    find_puzzle_dir,
    infer_puzzle_dir,
    project_root,
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


def _settings_with_active_dir(value: str | None, tmp_path: Path) -> CgSettings:
    config = CgConfig(config_file=tmp_path / "config.yaml", raw_data=CgConfigData())
    return CgSettings(
            settings_file=tmp_path / "settings.json",
            raw_data=CgSettingsData(current_puzzle_dir=value),
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


def test_cwd_used_when_it_holds_the_identity_file(tmp_path: Path) -> None:
    """The last step, and the only one that looks at where you are standing: this directory, and
       only this directory. No walk upward and no conventional subdirectory."""
    (tmp_path / PUZZLE_IDENTITY_FILE_NAME).write_text("{}")

    assert find_puzzle_dir(start_dir=tmp_path) == tmp_path.resolve()


def test_cwd_is_not_used_when_it_holds_no_identity_file(tmp_path: Path) -> None:
    """Guarded rather than taken at face value, so running a command from somewhere unrelated
       reports "no working directory" instead of failing further in with a confusing complaint
       about this directory's missing data/."""
    assert find_puzzle_dir(start_dir=tmp_path) is None


def test_a_nested_working_directory_is_not_found_from_its_parent(tmp_path: Path) -> None:
    """No searching: a puzzle under `puzzles/<name>/` is found by being active, not by being
       nearby. Otherwise a project root holding several would have to pick one."""
    nested = tmp_path / "puzzles" / "something"
    nested.mkdir(parents=True)
    (nested / PUZZLE_IDENTITY_FILE_NAME).write_text("{}")

    assert find_puzzle_dir(start_dir=tmp_path) is None


def test_returns_none_when_nothing_found(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert find_puzzle_dir(start_dir=empty) is None


def test_resolve_raises_when_nothing_is_found(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(CgPuzzleDirNotFoundError):
        resolve_puzzle_dir(start_dir=empty)


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
# would send every following command somewhere the user isn't looking.


def _settings_with_dirs(
            tmp_path: Path, *, current_puzzle_dir: str | None = None,
        ) -> CgSettings:
    config = CgConfig(config_file=tmp_path / "config.yaml", raw_data=CgConfigData())
    return CgSettings(
            settings_file=tmp_path / "settings.json",
            raw_data=CgSettingsData(current_puzzle_dir=current_puzzle_dir),
            config=config,
        )


def test_current_puzzle_dir_used_when_set(tmp_path: Path) -> None:
    settings = _settings_with_dirs(tmp_path, current_puzzle_dir="active")

    assert find_puzzle_dir(settings=settings, start_dir=tmp_path) == (tmp_path / "active").resolve()


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

    for identity, kind, finder in (
                (PUZZLE_IDENTITY_FILE_NAME, "puzzle", find_puzzle_dir),
                (CONTRIBUTION_IDENTITY_FILE_NAME, "contribution", find_contribution_dir),
            ):
        root = tmp_path / f"{kind}-case"
        nested = root / "inner"
        nested.mkdir(parents=True)
        (root / identity).write_text("{}")
        # The current directory counts when it is itself a working directory...
        assert finder(start_dir=root) == root.resolve()
        # ...and nothing above or below it does.
        assert finder(start_dir=nested) is None


class TestActivateArgumentResolution:
    """`cg puzzle activate` / `cg contribution activate` resolve their argument the same way a
       template name is resolved: a path is a path, a bare name is looked up in a known place.

       The point is being able to type `cg puzzle activate temperatures` from anywhere in the
       project without spelling out where the tree lives."""

    def test_no_argument_means_the_current_directory(self, tmp_path: Path) -> None:
        from codingame_tools.cli.main import _resolve_working_dir_argument
        assert _resolve_working_dir_argument(None, tmp_path / "puzzles") == Path.cwd()

    def test_a_bare_name_resolves_under_the_parent(self, tmp_path: Path) -> None:
        from codingame_tools.cli.main import _resolve_working_dir_argument
        parent = tmp_path / "puzzles"
        assert _resolve_working_dir_argument("temperatures", parent) == (parent / "temperatures").resolve()

    def test_a_relative_path_resolves_against_the_current_directory(
                self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """`./name` is how you reach a working directory that is not under the tree."""
        from codingame_tools.cli.main import _resolve_working_dir_argument
        monkeypatch.chdir(tmp_path)
        resolved = _resolve_working_dir_argument("./elsewhere", tmp_path / "puzzles")
        assert resolved == (tmp_path / "elsewhere").resolve()

    def test_an_absolute_path_is_used_as_given(self, tmp_path: Path) -> None:
        from codingame_tools.cli.main import _resolve_working_dir_argument
        target = tmp_path / "anywhere"
        assert _resolve_working_dir_argument(str(target), tmp_path / "puzzles") == target.resolve()

    def test_a_bare_name_is_never_also_tried_as_a_relative_path(
                self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """One spelling, one meaning. A directory of the same name in the current directory must
           not shadow the tree, or which one you got would depend on where you were standing."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "temperatures").mkdir()
        from codingame_tools.cli.main import _resolve_working_dir_argument
        parent = tmp_path / "puzzles"
        assert _resolve_working_dir_argument("temperatures", parent) == (parent / "temperatures").resolve()


class TestProjectRoot:
    """Where `puzzles/` and `contributions/` hang off."""

    @staticmethod
    def _settings(tmp_path: Path, *, project_dir: str | None = None) -> CgSettings:
        config_file = tmp_path / ".cg" / "config" / "config.yaml"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        return CgSettings(
                settings_file=tmp_path / ".cg" / "data" / "settings.json",
                raw_data=CgSettingsData(project_dir=project_dir),
                config=CgConfig(config_file=config_file, raw_data=CgConfigData()),
            )

    def test_the_resolved_config_selects_the_project(self, tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
        """`--config` selects a whole project, not merely its settings. Deriving the root from the
           current directory instead meant pointing at another project's config read that
           project's settings while creating working directories in this one -- and disagreed with
           `sdk_dir`, which has always followed the resolved config."""
        elsewhere = tmp_path / "elsewhere"
        (elsewhere / ".cg").mkdir(parents=True)
        monkeypatch.chdir(elsewhere)
        project = tmp_path / "project"
        settings = self._settings(project)

        assert project_root(settings) == project
        assert default_puzzles_dir(settings) == project / PUZZLES_SUBDIR_NAME

    def test_an_explicit_project_dir_wins(self, tmp_path: Path) -> None:
        target = tmp_path / "somewhere"
        target.mkdir()
        settings = self._settings(tmp_path / "project", project_dir=str(target))

        assert project_root(settings) == target

    def test_without_settings_it_walks_up_for_a_cg_marker(self, tmp_path: Path) -> None:
        (tmp_path / ".cg").mkdir()
        deep = tmp_path / "a" / "b"
        deep.mkdir(parents=True)

        assert project_root(start_dir=deep) == tmp_path

    def test_falls_back_to_the_start_directory(self, tmp_path: Path) -> None:
        assert project_root(start_dir=tmp_path) == tmp_path
