"""Unit tests for CgContributionManager's local test-running additions: `list_local_tests`,
   `run_local_test`, `run_local_tests`--entirely local (no network, no git), so these construct a
   manager directly against a plain `data/` directory rather than going through `import_()`.

These are pure/local tests--no network--so they run under the default `pdm run test` invocation.
They spawn real `sys.executable` subprocesses (via `codingame_tools.language.get_language(...).
run()`), same as `tests/test_language.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codingame_tools.client.common.protocol.contribution import CgContributionData, CgTestCase
from codingame_tools.contribution_manager.layout import solution_file_name
from codingame_tools.contribution_manager.manager import (
    CgContributionLocalTestFailedError,
    CgContributionManager,
    CgContributionManagerError,
)
from codingame_tools.contribution_manager.schema import CgContributionView
from codingame_tools.contribution_manager.test_cases_dir import import_test_cases
from codingame_tools.language import CgLanguageOperationNotSupportedError, get_language


def _tc(title: str, test_in: str, test_out: str, *, is_test: bool, is_validator: bool) -> CgTestCase:
    return CgTestCase(
            title=title, test_in=test_in, test_out=test_out,
            is_test=is_test, is_validator=is_validator, need_validation=True,
        )


def _setup(
            tmp_path: Path, test_cases: list[CgTestCase], *,
            solution_code: str = "n = int(input())\nprint(n * 2)\n",
            solution_language: str | None = "Python3",
        ) -> CgContributionManager:
    manager = CgContributionManager(tmp_path, object())  # type: ignore[arg-type]
    manager.save(CgContributionView(data=CgContributionData(title="T", solution_language=solution_language)))
    # Named from the language, the way every production writer does it. `manager.solution_file`
    # reports whatever is on disk and falls back to the neutral name when nothing is--which is right
    # for reading, but would write `solution.src` for a Python contribution.
    extension = get_language(solution_language).extension if solution_language else None
    solution_path = manager.data_dir / solution_file_name(extension)
    solution_path.parent.mkdir(parents=True, exist_ok=True)
    solution_path.write_text(solution_code)
    import_test_cases(test_cases, manager.tests_dir)
    return manager


# --- list_local_tests --------------------------------------------------------------------------


def test_list_local_tests_no_filter_returns_both_sides_both_ordinals(tmp_path: Path) -> None:
    manager = _setup(tmp_path, [
            _tc("A", "1", "2", is_test=True, is_validator=False),
            _tc("A", "3", "4", is_test=False, is_validator=True),
            _tc("B", "5", "6", is_test=True, is_validator=False),
            _tc("B", "7", "8", is_test=False, is_validator=True),
        ])

    tests = manager.list_local_tests()

    assert [(t.ordinal, t.side) for t in tests] == [
            ("01", "local"), ("01", "validator"), ("02", "local"), ("02", "validator"),
        ]


def test_list_local_tests_filters_by_ordinal_numeric_equivalence(tmp_path: Path) -> None:
    manager = _setup(tmp_path, [
            _tc("A", "1", "2", is_test=True, is_validator=False),
            _tc("B", "3", "4", is_test=True, is_validator=False),
        ])

    tests = manager.list_local_tests(["2"])

    assert [t.ordinal for t in tests] == ["02"]


def test_list_local_tests_filters_by_side(tmp_path: Path) -> None:
    manager = _setup(tmp_path, [
            _tc("A", "1", "2", is_test=True, is_validator=False),
            _tc("A", "3", "4", is_test=False, is_validator=True),
        ])

    local_only = manager.list_local_tests(local=True, validator=False)
    validator_only = manager.list_local_tests(local=False, validator=True)

    assert [t.side for t in local_only] == ["local"]
    assert [t.side for t in validator_only] == ["validator"]


# --- run_local_test: compare mode ---------------------------------------------------------------


async def test_run_local_test_compare_mode_pass(tmp_path: Path) -> None:
    manager = _setup(tmp_path, [_tc("A", "21", "42", is_test=True, is_validator=False)])
    test_case = manager.list_local_tests()[0]

    result = await manager.run_local_test(test_case, "Python3")

    assert result.passed
    assert not result.updated
    assert result.actual_output == "42\n"
    assert test_case.output_file.read_text() == "42\n"  # untouched


async def test_run_local_test_compare_mode_mismatch(tmp_path: Path) -> None:
    manager = _setup(tmp_path, [_tc("A", "21", "999", is_test=True, is_validator=False)])
    test_case = manager.list_local_tests()[0]

    result = await manager.run_local_test(test_case, "Python3")

    assert not result.passed
    assert result.returncode == 0
    assert result.actual_output == "42\n"
    assert result.expected_output == "999"


async def test_run_local_test_compare_mode_crash_fails(tmp_path: Path) -> None:
    manager = _setup(
            tmp_path, [_tc("A", "", "anything", is_test=True, is_validator=False)],
            solution_code="raise ValueError('boom')\n",
        )
    test_case = manager.list_local_tests()[0]

    result = await manager.run_local_test(test_case, "Python3")

    assert not result.passed
    assert result.returncode != 0
    assert "ValueError" in result.stderr


# --- run_local_test: update mode -----------------------------------------------------------------


async def test_run_local_test_update_mode_overwrites_output_file(tmp_path: Path) -> None:
    manager = _setup(tmp_path, [_tc("A", "21", "stale", is_test=True, is_validator=False)])
    test_case = manager.list_local_tests()[0]

    result = await manager.run_local_test(test_case, "Python3", update_expected=True)

    assert result.passed
    assert result.updated
    assert test_case.output_file.read_text() == "42\n"


async def test_run_local_test_update_mode_does_not_overwrite_on_crash(tmp_path: Path) -> None:
    manager = _setup(
            tmp_path, [_tc("A", "", "stale", is_test=True, is_validator=False)],
            solution_code="raise ValueError('boom')\n",
        )
    test_case = manager.list_local_tests()[0]

    result = await manager.run_local_test(test_case, "Python3", update_expected=True)

    assert not result.passed
    assert not result.updated
    assert test_case.output_file.read_text() == "stale\n"


async def test_run_local_test_unsupported_language_raises(tmp_path: Path) -> None:
    manager = _setup(tmp_path, [_tc("A", "1", "2", is_test=True, is_validator=False)])
    test_case = manager.list_local_tests()[0]

    with pytest.raises(CgLanguageOperationNotSupportedError):
        await manager.run_local_test(test_case, "Java")


# --- run_local_tests (batch) ---------------------------------------------------------------------


async def test_run_local_tests_raises_with_all_results_if_any_failed(tmp_path: Path) -> None:
    manager = _setup(tmp_path, [
            _tc("A", "21", "42", is_test=True, is_validator=False),
            _tc("B", "10", "wrong", is_test=True, is_validator=False),
        ])
    test_cases = manager.list_local_tests()

    with pytest.raises(CgContributionLocalTestFailedError) as exc_info:
        await manager.run_local_tests(test_cases, "Python3")

    results = exc_info.value.results
    assert len(results) == 2
    assert results[0].passed
    assert not results[1].passed


async def test_run_local_tests_returns_results_when_all_pass(tmp_path: Path) -> None:
    manager = _setup(tmp_path, [_tc("A", "21", "42", is_test=True, is_validator=False)])
    test_cases = manager.list_local_tests()

    results = await manager.run_local_tests(test_cases, "Python3")

    assert len(results) == 1
    assert results[0].passed


# --- language context / build (infallibility invariants the Docker work depends on) --------------


def test_meta_dir_does_not_require_an_imported_directory(tmp_path: Path) -> None:
    """`meta_dir` must never raise, unlike `git_dir`/`status_cache_file`: `language_context()` needs
       it, and `cg contribution play` works today on a directory holding nothing but
       `data/contribution-data.json`. With no contribution.json to say which layout is in use, it
       reports the non-`data/` default."""
    manager = CgContributionManager(tmp_path, object())  # type: ignore[arg-type]

    assert manager.meta_dir == manager.contribution_dir / ".meta"
    with pytest.raises(FileNotFoundError):
        _ = manager.git_dir  # the contrast: this one *does* require an import


def test_language_context_is_infallible_on_a_bare_directory(tmp_path: Path) -> None:
    manager = CgContributionManager(tmp_path, object())  # type: ignore[arg-type]

    ctx = manager.language_context("Python3")

    assert ctx.root == manager.contribution_dir
    assert ctx.solution_file == manager.solution_file
    assert ctx.meta_dir == manager.contribution_dir / ".meta"


def test_language_context_points_at_the_one_real_solution_file(tmp_path: Path) -> None:
    """One path, carrying the language's own extension, and not a symlink--so a build and a
       debugger cannot disagree about which file they are looking at."""
    manager = _setup(tmp_path, [_tc("A", "1", "1", is_test=True, is_validator=False)])

    ctx = manager.language_context("Python3")

    assert ctx.solution_file == tmp_path / "data" / "solution.py"
    assert ctx.solution_file.is_file()
    assert not ctx.solution_file.is_symlink()


async def test_build_solution_is_a_no_op_success_for_python(tmp_path: Path) -> None:
    manager = _setup(tmp_path, [_tc("A", "1", "1", is_test=True, is_validator=False)])

    result = await manager.build_solution("Python3")

    assert result.ok
    assert result.up_to_date


# --- set_language ---------------------------------------------------------------------------


async def _created(tmp_path: Path, language: str = "Python3") -> CgContributionManager:
    """A contribution as `create()` leaves it: a generated starter stub and nothing else."""
    manager = CgContributionManager(tmp_path, object())  # type: ignore[arg-type]
    stub = await get_language(language).build_contribution_create_stub_source()
    manager.save(CgContributionView(
            data=CgContributionData(title="T", solution_language=language)))
    manager.solution_file.parent.mkdir(parents=True, exist_ok=True)
    if stub is not None:
        manager.solution_file.write_text(stub)
    manager._write_solution_snapshot(language, stub)  # what create() records
    return manager


async def test_set_language_switches_a_freshly_created_contribution(tmp_path: Path) -> None:
    manager = await _created(tmp_path)

    result = await manager.set_language("C++")

    assert result.previous_language == "Python3"
    assert result.language == "C++"
    assert manager.load().data.solution_language == "C++"
    # The file is renamed to follow the language; the previous name is gone.
    assert (tmp_path / "data" / "solution.cpp").is_file()
    assert sorted(q.name for q in (tmp_path / "data").glob("solution.*")) == ["solution.cpp"]


async def test_set_language_leaves_an_empty_solution_when_a_language_has_no_stub(tmp_path: Path) -> None:
    """Only Python3 offers a create-stub that actually passes the seeded test cases, so every other
       language gets an empty solution.src--this client's spelling of a null solutionSource, which
       `updateContribution` accepts without validating. A placeholder would block the push."""
    manager = await _created(tmp_path)

    result = await manager.set_language("C++")

    assert not result.wrote_stub
    assert manager.solution_file.is_file()
    assert manager.solution_file.read_text().strip() == ""


async def test_set_language_refuses_when_a_real_solution_would_be_lost(tmp_path: Path) -> None:
    manager = await _created(tmp_path)
    manager.solution_file.write_text("n = int(input())\nprint(n * 2)\n")  # real work

    with pytest.raises(CgContributionManagerError, match="only ONE solution"):
        await manager.set_language("C++")

    assert manager.solution_file.read_text() == "n = int(input())\nprint(n * 2)\n"
    assert manager.load().data.solution_language == "Python3"  # unchanged


async def test_set_language_force_discards_a_real_solution(tmp_path: Path) -> None:
    manager = await _created(tmp_path)
    manager.solution_file.write_text("n = int(input())\nprint(n * 2)\n")

    await manager.set_language("C++", force=True)

    assert manager.load().data.solution_language == "C++"


async def test_matching_the_server_does_not_make_switching_safe(tmp_path: Path) -> None:
    """The key asymmetry with `cg puzzle set-language`. On a puzzle, "the server has this code" is
       a real escape hatch, because per-language recall brings it back on switching return. A
       contribution has no per-language history, so the server's copy is precisely what the next
       push destroys--it must not count as safe."""
    manager = await _created(tmp_path)
    solution = "n = int(input())\nprint(n * 2)\n"
    manager.solution_file.write_text(solution)
    # Model "already pushed": the server's stored solution is byte-identical to the local file.
    # This must still refuse.
    with pytest.raises(CgContributionManagerError, match="only ONE solution"):
        await manager.set_language("C++")


async def test_set_language_refuses_after_git_rewrote_the_solution(tmp_path: Path) -> None:
    """A snapshot deliberately isn't updated by git-driven writes (merge/discard-local/rebase);
       after one, solution.src holds real content and must no longer look like our stub."""
    manager = await _created(tmp_path)
    manager.solution_file.write_text("// content restored from the server branch\n")

    with pytest.raises(CgContributionManagerError):
        await manager.set_language("C++")


async def test_set_language_rejects_an_unknown_language(tmp_path: Path) -> None:
    manager = await _created(tmp_path)

    with pytest.raises(CgContributionManagerError, match="isn't a language"):
        await manager.set_language("Cobol")


async def test_set_language_rejects_the_current_language(tmp_path: Path) -> None:
    manager = await _created(tmp_path)

    with pytest.raises(CgContributionManagerError, match="already using"):
        await manager.set_language("Python3")


async def test_set_language_updates_the_snapshot_so_it_can_switch_again(tmp_path: Path) -> None:
    manager = await _created(tmp_path)

    await manager.set_language("C++")
    await manager.set_language("Java")  # must not raise: still only our generated stub

    assert manager.load().data.solution_language == "Java"


async def test_an_empty_solution_file_is_pushed_as_no_solution(tmp_path: Path) -> None:
    """The point of keeping an empty file instead of deleting it: `updateContribution` skips
       solution validation when solutionSource is null but validates any non-null value against
       every test case, so an empty file must reach the server as null.

       A file holding just a terminator counts too, since it *decodes* to the empty string--the one
       place `common.text_files`' conversion isn't injective, and useful here: an editor with "insert
       final newline" enabled can't quietly turn "no reference solution" into a one-blank-line
       program. Nothing weaker qualifies: a whitespace-only file is a real (broken) program and is
       pushed as one."""
    from codingame_tools.contribution_manager.manager import _read_local_data

    manager = await _created(tmp_path)
    base = manager.load().data

    for empty in ("", "\n"):
        manager.solution_file.write_text(empty)
        data, _ = _read_local_data(manager.data_dir, base)
        assert data.solution is None, f"{empty!r} must read as no solution"

    for not_empty, expected in (("   \n\t\n", "   \n\t"), ("n = input()\nprint(n)\n", "n = input()\nprint(n)")):
        manager.solution_file.write_text(not_empty)
        data, _ = _read_local_data(manager.data_dir, base)
        assert data.solution == expected, f"{not_empty!r} must decode to {expected!r}"


async def test_writing_an_empty_sidecar_leaves_a_zero_length_file(tmp_path: Path) -> None:
    """The zero-length carve-out in `common.text_files.server_text_to_file`, checked where it
       matters: without it every write would produce a one-newline file, and "no reference
       solution" would have no representation at all."""
    from codingame_tools.contribution_manager.manager import _read_sidecar, _write_sidecar

    path = tmp_path / "solution.py"
    _write_sidecar(path, "")
    assert path.read_bytes() == b""

    _write_sidecar(path, "code")  # non-empty gets its terminator
    assert path.read_bytes() == b"code\n"

    _write_sidecar(path, "code\n")  # a value that ends in a newline keeps it, and gets a terminator
    assert path.read_bytes() == b"code\n\n"

    for value in ("", "code", "code\n"):  # ...and all of it round trips exactly
        _write_sidecar(path, value)
        assert _read_sidecar(path) == value


async def test_debug_session_is_fed_the_value_not_the_file(
            tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        ) -> None:
    """An attach-style debugger (C++ via gdbserver) must get the same stdin as `play` and as
       CodinGame--not the test-case file, which carries a final newline this client added.

       Passing the file straight through is the tempting shape (the container already mounts the
       working directory), and it's wrong by exactly one byte. Confirmed live 2026-08-03 that
       CodinGame's runner appends nothing to stored test input, so that byte is a real divergence,
       silent for anything reading line-wise."""
    stored_input = "3\nabc"  # no trailing newline, the usual shape for a real server value
    manager = _setup(tmp_path, [_tc("A", stored_input, "out", is_test=True, is_validator=False)])

    captured: dict[str, object] = {}

    real_get_language = get_language

    class _RecordingLanguage:
        """Delegates everything except the one call under test (`language_context` needs a real
           `extension`), so this stays a probe rather than a reimplementation."""

        def __init__(self, language: str) -> None:
            self._real = real_get_language(language)

        def __getattr__(self, name: str) -> object:
            return getattr(self._real, name)

        async def start_debug_session(
                    self, ctx: object, stdin_text: str, *, timeout: float,
                    verbose: bool = False,
                ) -> object:
            captured["stdin_text"] = stdin_text
            return object()

    monkeypatch.setattr(
            "codingame_tools.contribution_manager.manager.get_language", _RecordingLanguage)

    await manager.start_debug_session("Python3", "01", "local")

    assert captured["stdin_text"] == stored_input
    # ...and that really is one byte fewer than the file holds.
    on_disk = manager.list_local_tests()[0].input_file.read_text()
    assert on_disk == stored_input + "\n"


# --- debug test selection ------------------------------------------------------------------------


def test_debug_selection_defaults_to_the_first_local_test(tmp_path: Path) -> None:
    """Not merely the first test: validators are the hidden, scoring cases, and landing in a
       debugger on one by default would be a surprising place to start."""
    manager = _setup(tmp_path, [
            _tc("V", "9", "9", is_test=False, is_validator=True),
            _tc("L", "1", "1", is_test=True, is_validator=False),
        ])

    chosen = manager.resolve_debug_test()

    assert chosen.side == "local"


def test_debug_selection_is_explicit_when_set(tmp_path: Path) -> None:
    manager = _setup(tmp_path, [
            _tc("A", "1", "1", is_test=True, is_validator=False),
            _tc("B", "2", "2", is_test=True, is_validator=False),
        ])

    manager.select_test("02", "local")
    assert manager.resolve_debug_test().ordinal == "02"

    manager.clear_selected_test()
    assert manager.resolve_debug_test().ordinal == "01"


def test_selecting_a_missing_test_is_refused(tmp_path: Path) -> None:
    manager = _setup(tmp_path, [_tc("A", "1", "1", is_test=True, is_validator=False)])

    with pytest.raises(CgContributionManagerError, match="No validator test case with ordinal"):
        manager.select_test("01", "validator")
