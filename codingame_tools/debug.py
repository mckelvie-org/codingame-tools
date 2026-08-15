"""python -m codingame_tools.debug TARGET_FILE [--update-expected]

Debug the solution that TARGET_FILE belongs to, against the working directory's currently selected
test case. One entry point for both puzzles and contributions, and for every language whose debugger
launches the program itself.

**This is what makes a VS Code launch configuration static.** The per-kind entry points it wraps
(`codingame_tools.puzzle_manager.debug`, `codingame_tools.contribution_manager.debug`) each need to
be told which kind they are and which test to run, so a configuration using them had to carry a
`pickString` list of that directory's test cases -- making `launch.json` per-directory state that
had to be regenerated after every import, language change, or new working directory. Here both
questions are answered at launch time instead:

- **which kind**, from `codingame_tools.workdir.resolve_working_dir` against TARGET_FILE; and
- **which test**, from `.meta/selected-test.json`, falling back to the first test case (the first
  *local* one for a contribution).

So a single configuration per language -- `args: ["${file}"]` -- serves every working directory in
the workspace, for as long as the workspace exists.

TARGET_FILE is passed through untouched to the debugger, never resolved: VS Code's `${file}` is the
path of whichever tab was focused, which is where breakpoints are bound, and resolving it could name
a different path than the one the editor has open. See `codingame_tools.test_runner.debug_stdin`,
which documents the same no-realpath invariant.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .workdir import resolve_working_dir

__all__ = ["main"]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
            prog="python -m codingame_tools.debug",
            description="Debug the solution TARGET_FILE belongs to, against the selected test case.",
        )
    parser.add_argument(
            "target_file", type=Path, metavar="TARGET_FILE",
            help="Any file in the working directory--normally VS Code's ${file}, i.e. the "
                 "solution source file.",
        )
    parser.add_argument(
            "--update-expected", action="store_true",
            help="Overwrite the test case's expected output with the captured output instead of "
                 "comparing against it. Contributions only--a puzzle's test cases are downloaded "
                 "server truth and are never rewritten from a local run.",
        )
    args = parser.parse_args(argv)

    working_dir = resolve_working_dir(args.target_file)

    # Imported here rather than at module scope: each pulls in its whole manager, and a debug launch
    # only ever needs one of them.
    if working_dir.kind == "puzzle":
        if args.update_expected:
            raise SystemExit(
                    "--update-expected is not supported for puzzles: .meta/tests/ holds test cases "
                    "downloaded from CodinGame, which a local run has no business rewriting.")
        from .puzzle_manager.debug import main as puzzle_main
        from .puzzle_manager.manager import CgPuzzleManager

        index = CgPuzzleManager(working_dir.root, None).resolve_debug_test_index()  # type: ignore[arg-type]
        puzzle_main([str(args.target_file), str(index), "--puzzle-dir", str(working_dir.root)])
        return

    from .contribution_manager.debug import main as contribution_main
    from .contribution_manager.manager import CgContributionManager

    test_case = CgContributionManager(working_dir.root, None).resolve_debug_test()  # type: ignore[arg-type]
    delegated = [
        str(args.target_file), test_case.ordinal, test_case.side,
        "--contribution-dir", str(working_dir.root),
    ]
    if args.update_expected:
        delegated.append("--update-expected")
    contribution_main(delegated)


if __name__ == "__main__":
    main()
