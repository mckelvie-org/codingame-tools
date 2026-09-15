"""Unit tests for codingame_tools.contribution_manager.resolver: contribution working directory
   discovery precedence (explicit > CG_CONTRIBUTION_DIR > settings > cwd > ./contribution).

These are pure/local tests--no network--so they run under the default `pdm run test` invocation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codingame_tools.config.cg_config import CgConfigData
from codingame_tools.config.resolver import CgConfig
from codingame_tools.contribution_manager.layout import DATA_SUBDIR_NAME, solution_file_name
from codingame_tools.contribution_manager.resolver import (
    CG_CONTRIBUTION_DIR_ENV_VAR,
    CgContributionDirInferenceError,
    CgContributionDirNotFoundError,
    find_contribution_dir,
    infer_contribution_dir,
    resolve_contribution_dir,
)
from codingame_tools.contribution_manager.schema import CONTRIBUTION_IDENTITY_FILE_NAME
from codingame_tools.settings import CgSettings, CgSettingsData

SOLUTION_FILE_NAME = solution_file_name("py")
"""These tests all write Python solutions, so the solution file is `solution.py`. The name is no
   longer fixed: it carries the language's extension and is renamed when the language changes."""


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CG_CONTRIBUTION_DIR_ENV_VAR, raising=False)


def _settings_with_contribution_dir(value: str | None, tmp_path: Path) -> CgSettings:
    config = CgConfig(config_file=tmp_path / "config.yaml", raw_data=CgConfigData())
    return CgSettings(
            settings_file=tmp_path / "settings.json",
            raw_data=CgSettingsData(current_contribution_dir=value),
            config=config,
        )


def test_explicit_wins_even_without_a_manifest_file(tmp_path: Path) -> None:
    target = tmp_path / "fresh-empty-dir"
    assert find_contribution_dir(target) == target.resolve()


def test_env_var_used_when_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CG_CONTRIBUTION_DIR_ENV_VAR, str(tmp_path / "from-env"))
    assert find_contribution_dir() == (tmp_path / "from-env").resolve()


def test_explicit_overrides_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CG_CONTRIBUTION_DIR_ENV_VAR, str(tmp_path / "from-env"))
    explicit = tmp_path / "explicit"
    assert find_contribution_dir(explicit) == explicit.resolve()


def test_cwd_used_when_it_holds_the_identity_file(tmp_path: Path) -> None:
    """The last step, and the only one that looks at where you are standing: this directory, and
       only this directory. No walk upward and no conventional subdirectory."""
    (tmp_path / CONTRIBUTION_IDENTITY_FILE_NAME).write_text("{}")

    assert find_contribution_dir(start_dir=tmp_path) == tmp_path.resolve()


def test_cwd_is_not_used_when_it_holds_no_identity_file(tmp_path: Path) -> None:
    """Guarded rather than taken at face value, so running a command from somewhere unrelated
       reports "no working directory" instead of failing further in with a confusing complaint
       about this directory's missing data/."""
    assert find_contribution_dir(start_dir=tmp_path) is None


def test_a_nested_working_directory_is_not_found_from_its_parent(tmp_path: Path) -> None:
    """No searching: a puzzle under `puzzles/<name>/` is found by being active, not by being
       nearby. Otherwise a project root holding several would have to pick one."""
    nested = tmp_path / "puzzles" / "something"
    nested.mkdir(parents=True)
    (nested / CONTRIBUTION_IDENTITY_FILE_NAME).write_text("{}")

    assert find_contribution_dir(start_dir=tmp_path) is None


def test_returns_none_when_nothing_found(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert find_contribution_dir(start_dir=empty) is None


def test_resolve_raises_not_found_without_allow_default(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(CgContributionDirNotFoundError):
        resolve_contribution_dir(start_dir=empty)


def test_resolve_allow_default_falls_back_to_start_dir(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert resolve_contribution_dir(start_dir=empty, allow_default=True) == empty.resolve()


def test_resolve_allow_default_still_prefers_a_real_match(tmp_path: Path) -> None:
    (tmp_path / CONTRIBUTION_IDENTITY_FILE_NAME).write_text("{}")
    assert resolve_contribution_dir(start_dir=tmp_path, allow_default=True) == tmp_path


# --- infer_contribution_dir -------------------------------------------------------------------


def _make_contribution_dir(root: Path) -> Path:
    data_dir = root / DATA_SUBDIR_NAME
    data_dir.mkdir(parents=True)
    (root / CONTRIBUTION_IDENTITY_FILE_NAME).write_text("{}")
    (data_dir / SOLUTION_FILE_NAME).write_text("print('hi')\n")
    return root


def test_infer_from_solution_src_directly(tmp_path: Path) -> None:
    contribution_dir = _make_contribution_dir(tmp_path / "contribution")
    assert infer_contribution_dir(contribution_dir / DATA_SUBDIR_NAME / SOLUTION_FILE_NAME) == contribution_dir


def test_infer_from_symlink_inside_contribution_dir(tmp_path: Path) -> None:
    contribution_dir = _make_contribution_dir(tmp_path / "contribution")
    link = contribution_dir / "solution.py"
    link.symlink_to(Path(DATA_SUBDIR_NAME) / SOLUTION_FILE_NAME)
    assert infer_contribution_dir(link) == contribution_dir


def test_infer_from_symlink_entirely_outside_the_contribution_dir(tmp_path: Path) -> None:
    contribution_dir = _make_contribution_dir(tmp_path / "contribution")
    elsewhere = tmp_path / "some" / "unrelated" / "place"
    elsewhere.mkdir(parents=True)
    link = elsewhere / "my_solution.py"
    link.symlink_to(contribution_dir / DATA_SUBDIR_NAME / SOLUTION_FILE_NAME)
    assert infer_contribution_dir(link) == contribution_dir


def test_infer_refuses_a_file_not_named_solution_src(tmp_path: Path) -> None:
    contribution_dir = _make_contribution_dir(tmp_path / "contribution")
    other_file = contribution_dir / DATA_SUBDIR_NAME / "not-the-solution.txt"
    other_file.write_text("irrelevant")
    with pytest.raises(CgContributionDirInferenceError):
        infer_contribution_dir(other_file)


def test_infer_refuses_without_contribution_json_at_the_inferred_root(tmp_path: Path) -> None:
    data_dir = tmp_path / "not-a-contribution" / DATA_SUBDIR_NAME
    data_dir.mkdir(parents=True)
    solution_file = data_dir / SOLUTION_FILE_NAME
    solution_file.write_text("print('hi')\n")
    with pytest.raises(CgContributionDirInferenceError):
        infer_contribution_dir(solution_file)
