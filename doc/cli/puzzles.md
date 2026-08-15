# Solving puzzles

Pull a puzzle into a local directory, solve it in your own editor, run its tests without touching
the network, and submit when you're happy.

## The loop

```bash
cg puzzle import ./puzzle temperatures          # pull it down; becomes the active puzzle
$EDITOR "$(cg puzzle where)/data/solution.py"   # solve it
cg puzzle play                                  # run every test case, locally
cg puzzle submit                                # graded submission
```

That's the whole thing. The rest of this page is what to do when it isn't that simple.

## Importing

```bash
cg puzzle import ./puzzle temperatures
cg puzzle import ./puzzle 10075
cg puzzle import ./puzzle "Temperatures"
```

The target directory comes first and is required. `PUZZLE` is resolved in this order: numeric puzzle
ID, exact pretty ID, exact title, case-insensitive title.

If you've attempted the puzzle before, your saved answer is imported in whatever language you last
used; otherwise you get a placeholder in `--language` (default Python3).

Importing makes that directory the **active** puzzle, so later commands find it without
`--puzzle-dir` — see [active working directories](../concepts/profiles.md#active-working-directories).

## Finding your way around

```bash
cg puzzle where           # just the resolved path, for $(...)
cg puzzle description     # the problem statement, rendered; no network
```

`where` prints nothing but the path, so it composes:

```bash
$EDITOR "$(cg puzzle where)/data/solution.py"
cd "$(cg puzzle where)"
```

It exits non-zero if no working directory can be found.

## Running tests

```bash
cg puzzle play            # every downloaded test case, entirely locally
cg puzzle play 3          # just test 3
cg puzzle play 1 2 5      # a few
cg puzzle play --show-stdout
```

`play` never touches the network, and output is compared exactly as CodinGame compares it, so a pass
here means a pass there — see [output comparison](../design/final-newlines.md#output-comparison).
Captured stdout is only printed for failures unless you ask for it.

There's also a server-side equivalent, the IDE's "Test" button:

```bash
cg puzzle play-server
```

> **`play-server` overwrites your saved code.** Running it durably saves your current code on the
> server for this puzzle and language. It isn't a submission and isn't graded, but it does replace
> what CodinGame has stored. Prefer `cg puzzle play` unless you need the server's own runner.

## Debugging

When a test fails and you want to step through it rather than add print statements:

```bash
cg vscode install         # generates the VS Code launch entries; once per workspace
cg puzzle select-test 3   # which test case to feed the debugger
```

Then press **F5** in VS Code with your solution file focused. Breakpoints land in
`data/solution.py`, and stdin comes from the selected test case. Compiled languages build, run and
debug inside Docker, so C++ needs no local toolchain.

Full details, including containerised languages and how to pick a test case:
**[Debugging](debugging.md)**.

## Submitting

```bash
cg puzzle submit
```

A real, permanent, graded submission, validated against the puzzle's hidden validator test cases.
There's no undo. For a puzzle with many heavy validators this can take a while — the server runs
your code once per validator.

## Switching language

```bash
cg puzzle set-language C++
```

CodinGame keeps your latest source per language, so this restores your previous C++ work if you had
any. It refuses if `data/solution.<ext>` holds edits the server doesn't have; submit them first or
pass `--force`. See [languages](../concepts/languages.md).

## When local and server disagree

```bash
cg puzzle status            # local summary; no network
cg puzzle status --refresh  # also check the server, and fetch live progress/score
cg puzzle diff              # unified diff, local vs the server's last-submitted answer
cg puzzle discard-local     # throw local edits away, take the server's copy
```

There's no merge step — a puzzle has one editable file. Look at the diff, then keep one side or the
other.

## Managing working directories

```bash
cg puzzle activate ./other-puzzle   # switch which one commands act on
cg puzzle activate                  # ...or activate the current directory
cg puzzle deactivate                # back to the configured default
cg puzzle delete                    # remove the local directory; prompts unless --force
```

`delete` is local-only — the puzzle itself isn't yours to remove.

```bash
cg puzzle repair
```

Rebuilds `.meta/` — the test-session handle, downloaded test cases, cached statement — from
`puzzle.json`, without touching `data/`. Use it after a fresh clone (`.meta/` is gitignored) or if
anything in the cache looks wrong. Deleting `.meta/` and repairing is always safe.

## Full reference

Every flag of every command: **[`cg puzzle` reference](reference/puzzle.md)**.
