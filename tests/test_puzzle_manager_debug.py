"""Unit tests for codingame_tools.puzzle_manager.debug: the VS Code debugger launcher CLI
   (`python -m codingame_tools.puzzle_manager.debug`).

These are pure/local tests--no network--so they run under the default `pdm run test` invocation.
`main()` runs the "solution" in-process (that's the whole point of the module it wraps), so stdout
is captured via pytest's capsys rather than a subprocess.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codingame_tools.puzzle_manager.debug import main
from codingame_tools.puzzle_manager.layout import DATA_SUBDIR_NAME, META_SUBDIR_NAME, solution_file_name
from codingame_tools.puzzle_manager.resolver import CgPuzzleDirInferenceError
from codingame_tools.puzzle_manager.schema import PUZZLE_IDENTITY_FILE_NAME
from codingame_tools.puzzle_manager.test_cases_dir import TEST_META_FILE_NAME, TESTS_SUBDIR_NAME, CgPuzzleTestCaseMeta
from codingame_tools.test_runner.debug_stdin import CgDebugStdinOutputMismatchError

SOLUTION_FILE_NAME = solution_file_name("py")
"""These tests all write Python solutions, so the solution file is `solution.py`. The name is no
   longer fixed: it carries the language's extension and is renamed when the language changes."""


def _make_puzzle_dir(root: Path) -> Path:
    data_dir = root / DATA_SUBDIR_NAME
    data_dir.mkdir(parents=True)
    (root / PUZZLE_IDENTITY_FILE_NAME).write_text("{}")
    (data_dir / SOLUTION_FILE_NAME).write_text("n = int(input())\nprint(n * 2)\n")
    return root


def _add_test_case(puzzle_dir: Path, index: int, label: str, input_text: str, output_text: str) -> None:
    named_dir = puzzle_dir / META_SUBDIR_NAME / TESTS_SUBDIR_NAME / str(index).zfill(2) / label
    named_dir.mkdir(parents=True)
    CgPuzzleTestCaseMeta(label=label).save(named_dir / TEST_META_FILE_NAME)
    (named_dir / "input.txt").write_text(input_text)
    (named_dir / "output.txt").write_text(output_text)


def test_main_runs_against_matching_test_index(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    puzzle_dir = _make_puzzle_dir(tmp_path / "puzzle")
    _add_test_case(puzzle_dir, 1, "Case-A", "21\n", "42\n")
    target_file = puzzle_dir / DATA_SUBDIR_NAME / SOLUTION_FILE_NAME

    main([str(target_file), "1"])

    assert capsys.readouterr().out == "42\n"


def test_main_raises_on_mismatch(tmp_path: Path) -> None:
    puzzle_dir = _make_puzzle_dir(tmp_path / "puzzle")
    _add_test_case(puzzle_dir, 1, "Case-A", "21\n", "999\n")
    target_file = puzzle_dir / DATA_SUBDIR_NAME / SOLUTION_FILE_NAME

    with pytest.raises(CgDebugStdinOutputMismatchError):
        main([str(target_file), "1"])


def test_main_unknown_test_index_exits(tmp_path: Path) -> None:
    puzzle_dir = _make_puzzle_dir(tmp_path / "puzzle")
    _add_test_case(puzzle_dir, 1, "Case-A", "21\n", "42\n")
    target_file = puzzle_dir / DATA_SUBDIR_NAME / SOLUTION_FILE_NAME

    with pytest.raises(SystemExit):
        main([str(target_file), "99"])


def test_main_explicit_puzzle_dir_overrides_inference(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    puzzle_dir = _make_puzzle_dir(tmp_path / "puzzle")
    _add_test_case(puzzle_dir, 1, "Case-A", "21\n", "42\n")
    # target_file lives outside any puzzle dir--only --puzzle-dir can make this resolve.
    elsewhere = tmp_path / "elsewhere.py"
    elsewhere.write_text("n = int(input())\nprint(n * 2)\n")

    main([str(elsewhere), "1", "--puzzle-dir", str(puzzle_dir)])

    assert capsys.readouterr().out == "42\n"


def test_main_infers_puzzle_dir_from_symlink_elsewhere(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The scenario that motivated inference in the first place: a symlink living nowhere near
       the puzzle working directory, pointing (however indirectly) at data/solution.src."""
    puzzle_dir = _make_puzzle_dir(tmp_path / "puzzle")
    _add_test_case(puzzle_dir, 1, "Case-A", "21\n", "42\n")
    elsewhere = tmp_path / "somewhere" / "solution.py"
    elsewhere.parent.mkdir(parents=True)
    elsewhere.symlink_to(puzzle_dir / DATA_SUBDIR_NAME / SOLUTION_FILE_NAME)

    main([str(elsewhere), "1"])

    assert capsys.readouterr().out == "42\n"


def test_main_inference_failure_propagates(tmp_path: Path) -> None:
    not_a_puzzle = tmp_path / "random.py"
    not_a_puzzle.write_text("print('hi')\n")

    with pytest.raises(CgPuzzleDirInferenceError):
        main([str(not_a_puzzle), "1"])
