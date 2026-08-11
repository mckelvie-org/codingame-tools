# codingame-tools

[![CI](https://img.shields.io/badge/CI-passing-brightgreen.svg)](https://github.com/mckelvie-org/codingame-tools/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/badge/pypi-v2.0.0-blue.svg)](https://pypi.org/project/codingame-tools/2.0.0/)
[![Python versions](https://img.shields.io/badge/python-3.10%20|%203.11%20|%203.12%20|%203.13%20|%203.14-blue.svg)](https://pypi.org/project/codingame-tools/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/mckelvie-org/codingame-tools/blob/v2.0.0/LICENSE)

Solve [CodinGame](https://www.codingame.com/) puzzles and author CodinGame contributions from your
own editor, under version control — instead of in the browser IDE.

## Highlights

- **[Browser login](https://github.com/mckelvie-org/codingame-tools/blob/v2.0.0/doc/concepts/authentication.md)** — CodinGame has no API tokens, so `cg login`
  drives a real browser and captures the session. Works with any sign-in method, including
  third-party identity providers. Credentials are stored per
  [profile](https://github.com/mckelvie-org/codingame-tools/blob/v2.0.0/doc/concepts/profiles.md); a headless `--manual` path exists for CI.

- **[Async client with structured API wrappers](https://github.com/mckelvie-org/codingame-tools/blob/v2.0.0/doc/client/index.md)** — `CgClient` exposes 22
  service endpoints as typed methods, with dataclasses for every request and response, plus a helper
  layer that handles retries and the CDN timeouts that heavy operations provoke.

- **[Puzzle manager](https://github.com/mckelvie-org/codingame-tools/blob/v2.0.0/doc/tools/puzzle-manager.md)** — a local working directory for solving a
  puzzle: import it, edit one file, run its test cases, submit. Language switching restores your own
  previous code, since CodinGame stores your latest source per language.

- **[Contribution manager](https://github.com/mckelvie-org/codingame-tools/blob/v2.0.0/doc/tools/contribution-manager.md)** — a local working directory for a
  puzzle you're *writing*. `data/` is a real git working tree with `main`/`server`/`version-data`
  branches, so syncing with CodinGame is a genuine fetch/rebase/merge workflow — conflicts included
  — rather than a one-shot overwrite.

- **[A CLI exposing all of it](https://github.com/mckelvie-org/codingame-tools/blob/v2.0.0/doc/cli/index.md)** — 148 commands, from the two workflow groups down
  to one subcommand per raw API method. Nothing is library-only.

- **[Local validation](https://github.com/mckelvie-org/codingame-tools/blob/v2.0.0/doc/cli/puzzles.md#running-tests)** — run your solution against real test
  cases with **no network access at all**. Output comparison reproduces CodinGame's own rule
  exactly, measured against the live service, so a local pass predicts a remote one.

- **[VS Code integration, including debugging](https://github.com/mckelvie-org/codingame-tools/blob/v2.0.0/doc/cli/debugging.md)** — generated run/debug
  configuration, breakpoints in your solution, and a test-case picker. Compiled languages build,
  run and debug inside Docker, so C++ works with no local toolchain at all.

- **[The protocol, documented](https://github.com/mckelvie-org/codingame-tools/blob/v2.0.0/doc/client/services.md)** — CodinGame publishes no API spec. Every
  endpoint here was reverse-engineered, wrapped in dataclasses, and documented with what it actually
  does — including the parts that contradict what the name suggests. Where behaviour was confirmed
  by probing the live service, the docstring says so.

- **Fully typed** — `mypy` clean across the package and its tests, `py.typed` shipped.

## Installation

```bash
pip install codingame-tools
```

## Quick start -- solve a puzzle

```bash
cg login                          # opens a browser, saves credentials
cg whoami

# pull a puzzle into ./puzzle and make it the working puzzle
cg puzzle import --language Python3 ./puzzle temperatures

# implement a solution
$EDITOR "$(cg puzzle where)/data/solution.py"

cg puzzle play                    # Test locally against downloaded test cases -- no network
cg puzzle submit                  # graded submission
```

## Quick start -- author a contribution

```bash
# Create a new contribution and make it the working contribution
# (purely local; nothing exists server-side until pushed)
cg contribution create -t PUZZLE_INOUT --language Python3 ./contribution "My Puzzle"
cd "$(cg contribution where)"

$EDITOR data/statement.cgmd
$EDITOR data/input_description.cgmd
$EDITOR data/output_description.cgmd
$EDITOR data/constraints.cgmd
$EDITOR data/stub_generator.cgstub
cp $COVER_ART_1920x1080_PNG data/cover.png

# Create a reference solution
$EDITOR data/solution.py

# Create a suite of test cases (both local and validator)
$EDITOR data/tests/**/*

# Validate the reference solution locally against all test cases
cg contribution play

# Push the contribution to the server
cg contribution push

# now it exists on server
```

## Quick start -- use the async client directly:

```python
import asyncio
from codingame_tools.client import CgClient

async def main() -> None:
    async with CgClient() as client:
        for c in await client.services.contribution.get_all_pending_contributions():
            print(c.public_handle, c.title)

asyncio.run(main())
```

## Documentation

- **[Documentation for this version](https://github.com/mckelvie-org/codingame-tools/blob/v2.0.0/doc/index.md)** — concepts, workflow guides, the client
  library, and a [command reference](https://github.com/mckelvie-org/codingame-tools/blob/v2.0.0/doc/cli/reference/index.md) generated from the CLI itself.
- **[Documentation for the latest release](https://github.com/mckelvie-org/codingame-tools/blob/prod-latest/doc/index.md)**
  — if you're reading an older version's page and want current docs.
- **[Documentation for in-development code](https://github.com/mckelvie-org/codingame-tools/blob/main/doc/index.md)**
  — the tip of `main`, describing work that hasn't been released yet.

The first link is relative in the repository and is rewritten to an absolute, tag-pinned URL when a
release is cut, so it resolves both on GitHub and on PyPI, and always points at the docs as they
were for *that* version. The other two are absolute and deliberately unpinned, so they keep tracking
`prod-latest` and `main` no matter which version's page you found them on.

## Caveats

This talks to a private API by imitating the web client. It will occasionally break when CodinGame
changes something, usually as a clear error naming the field that moved. And some operations are
irreversible or publicly visible — submitting creates a permanent graded submission, pushing updates
real content, and running a server-side test durably overwrites your saved code. Commands that do
any of that say so.

## Supported Python versions

Python 3.10 through 3.14.

## License

MIT. See [LICENSE](https://github.com/mckelvie-org/codingame-tools/blob/v2.0.0/LICENSE).

---

For development and release workflow, see [CONTRIBUTING.md](https://github.com/mckelvie-org/codingame-tools/blob/v2.0.0/CONTRIBUTING.md).
