# Languages and toolchains

A working directory has exactly one language at a time, recorded alongside its content and reflected
in the solution file's own extension — `data/solution.py`, `data/solution.cpp`, and so on. Switching
language renames the file.

```bash
cg puzzle set-language C++
cg contribution set-language Python3
```

## Puzzles remember every language you've used

CodinGame stores your most recent source **per language** for a puzzle. So switching a puzzle's
language is reversible: anything you previously wrote in the target language comes back, and a
language you've never used gets a placeholder.

`cg puzzle set-language` refuses if the solution holds work the server doesn't have — submit
it first, or pass `--force` to discard it. It needs the network even though it only changes local
state, because restoring your previous code means fetching it.

## Contributions remember exactly one

A contribution stores a single reference solution with no per-language history. So
`cg contribution set-language` is **destructive by design**: it replaces the solution with a starter
stub, and the next `cg contribution push` overwrites the last durable copy. There is nothing to
switch back to. It refuses unless the current solution is still the stub `cg` generated, and
`--force` is required to discard real work — save it somewhere outside the working directory first.

There's a wrinkle worth knowing: only Python3 ships a starter stub that actually passes the seeded
test cases, so switching to any other language leaves the solution file **empty**. That's
required, not a shortfall. `updateContribution` skips solution validation entirely when the solution
is null, but validates any non-null one against every test case — so a comment-only placeholder in
another language would fail validation and block your push.

## Running and building locally

```bash
cg puzzle play          # run against downloaded test cases, no network at all
cg contribution play    # same, against data/tests/
cg puzzle build         # compile without running (no-op for interpreted languages)
```

`play` builds first when the language needs it, so `build` is only for compiling without running, or
for warming a cold container image before you start.

Local comparison of your output against the expected output is **exactly as strict as CodinGame's**
— everything compared byte-for-byte, except a difference of one trailing newline in either
direction. That equivalence is measured, not assumed, and the measurements are in
[output comparison](../design/final-newlines.md#output-comparison). It matters because a local pass
that fails on submission is the worst outcome the runner can produce.

## Compiled languages run in Docker

You do not need a local toolchain. For a compiled language, `cg` builds and runs inside a container:

- One long-lived container per (working directory × language). Change language and the old one is
  replaced, not orphaned.
- Your working directory is bind-mounted **read-only**; all build artifacts live inside the
  container, so nothing appears in `data/` that you didn't put there.
- Images are content-addressed, so an unchanged source rebuilds in roughly no time.
- The image definition is a Dockerfile you can edit, in a shared per-user location, so tweaking a
  language's toolchain once applies everywhere.

```bash
cg docker clean   # tear down every container and image this tool created
```

That's always safe. No user work ever lives in a `cg`-managed container or image — the source is
mounted from disk and artifacts are disposable — so there's no prompt and no `--force`.

## Which languages are supported

Every language CodinGame accepts can be *selected*, and the client knows each one's file extension
and comment syntax. How much more than that works varies:

- **Python3** — fully supported: local run, local debug (in-process, breakpoints in your solution).
- **C++** — fully supported via Docker: build, run, and attach-style debugging with gdbserver.
- **Everything else** — selectable and submittable, with local execution depending on whether a
  toolchain module exists for it.

Adding a language is a self-contained module under `codingame_tools/language/languages/`; the
registry discovers it automatically.
