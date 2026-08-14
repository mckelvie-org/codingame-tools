"""Command-line entry point.

```
python -m codingame_tools.contribution_manager.debug TARGET_FILE ORDINAL SIDE [--contribution-dir DIR] [--update-expected]
```


VS Code debugger launcher for a contribution working directory's solution--a thin,
contribution_manager-aware wrapper around `codingame_tools.test_runner.debug_stdin.
run_debug_stdin`. TARGET_FILE (VS Code's own `${file}` macro) is used only to *infer* the
contribution working directory (see `codingame_tools.contribution_manager.resolver.
infer_contribution_dir`) when `--contribution-dir` isn't given--ORDINAL/SIDE then select which
`tests/` test case's input/output to run against (see `codingame_tools.contribution_manager.
test_cases_dir.list_local_test_cases`).

`--update-expected` overwrites the selected test case's `output.txt` with the captured output
instead of comparing against it--meaningful here (unlike `codingame_tools.puzzle_manager.debug`)
because `data/tests/` is author-owned content, not downloaded server truth.

Not exported from `codingame_tools.contribution_manager`'s own `__init__.py`--like `test_runner.
debug_stdin`, this is a `python -m` entry point, not a library import.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from ..client.client import CgClient
from ..test_runner import run_debug_stdin
from .manager import CgContributionManager
from .resolver import infer_contribution_dir

__all__ = ["main"]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
            prog="python -m codingame_tools.contribution_manager.debug",
            description="Debug a contribution solution under VS Code against a specific tests/ test case.",
        )
    parser.add_argument("target_file", type=Path, metavar="TARGET_FILE")
    parser.add_argument("ordinal", type=str, metavar="ORDINAL")
    parser.add_argument("side", choices=["local", "validator"], metavar="SIDE")
    parser.add_argument(
            "--contribution-dir", type=Path, default=None, metavar="DIR",
            help="Contribution working directory. Defaults to inferring it from TARGET_FILE.",
        )
    parser.add_argument(
            "--update-expected", action="store_true",
            help="Overwrite the test case's output.txt with the captured output instead of "
                 "comparing against it.",
        )
    args = parser.parse_args(argv)

    contribution_dir = args.contribution_dir if args.contribution_dir is not None \
        else infer_contribution_dir(args.target_file)
    manager = CgContributionManager(contribution_dir, cast(CgClient, None))
    matching = manager.list_local_tests(
            [args.ordinal], local=args.side == "local", validator=args.side == "validator")
    if not matching:
        raise SystemExit(f"No {args.side} test case with ordinal {args.ordinal!r} under {manager.tests_dir}.")
    test_case = matching[0]

    run_debug_stdin(
            test_case.input_file, args.target_file, test_case.output_file,
            update_expected=args.update_expected,
            # A contribution's test files are this client's rendering of a server-side string, so
            # the final newline on disk isn't part of the value--see common.text_files. Without
            # this the debugger feeds one more byte of stdin than `cg contribution play` does.
            final_newline_added=True,
        )


if __name__ == "__main__":
    main()
