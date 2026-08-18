"""Command-line interface for the Codingame Client."""

from __future__ import annotations

import sys

from codingame_tools.cli.main import main

if __name__ == "__main__":
    # `main()` *returns* the exit code rather than raising SystemExit. The `cg` console script
    # wraps it in sys.exit() for us; running the package with `python -m` does not, so without
    # this every failure -- a bad argument, a refused command, a server error -- exited 0.
    sys.exit(main())
