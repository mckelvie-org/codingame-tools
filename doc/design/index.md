# Design notes

Decisions that aren't obvious from the code, and the measurements behind them. Written down because
each one looks wrong at first glance, and each was arrived at by getting it wrong first.

- **[Final newlines](final-newlines.md)** — why server text and local files convert unconditionally
  in both directions, why puzzles and contributions differ, and how CodinGame's output comparison
  actually behaves.
- **[What CodinGame actually runs](codingame-runtime.md)** — measured compiler, interpreter and
  library versions, and the `-O0` finding their docs don't mention.
- **[Composable toolchain images](toolchain-images.md)** — why one image carries every language,
  and how fragments compose so a subset shares layers with a superset.
- **[The documentation toolchain](docs-toolchain.md)** — why the site is built with ProperDocs
  rather than MkDocs, why MkDocs 2.0 is not a destination, and the page-count test that decides
  when Zensical becomes one.

## What belongs here

Anything a future reader would otherwise re-derive, re-argue, or "fix" back to the broken version.
Not a substitute for docstrings: if it explains one function, it belongs on that function. These
pages are for decisions that span modules, or that rest on evidence living outside the codebase.

Where a claim was established by probing the live service, these pages say when and how, so it can
be re-checked rather than trusted indefinitely. CodinGame is free to change any of it without
notice.
