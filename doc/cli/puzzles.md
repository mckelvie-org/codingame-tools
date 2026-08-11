# Solving puzzles

Pull a puzzle into a local directory, solve it in your own editor, run its tests without touching
the network, and submit when you're happy.

## The loop

```bash
cg puzzle import ./puzzle temperatures    # pull it down; becomes the active puzzle
$EDITOR "$(cg puzzle where)/data/solution.py"  # solve it
cg puzzle play                            # run every test case, locally
cg puzzle submit                          # graded submission
```

That's the whole thing. The rest of this page is what to do when it isn't that simple.

## Importing

```bash
cg puzzle import ./puzzle temperatures
cg puzzle import ./puzzle 10075
cg puzzle import ./puzzle "Temperatures"
```

The target directory comes **first and is required**, matching `cg contribution import` and
`cg contribution create`. `PUZZLE` is then resolved in order of preference: numeric puzzle ID,
exact pretty ID, exact title, case-insensitive title. If you've attempted the puzzle before, your
existing saved answer is imported in whatever language you last used; otherwise you get a
placeholder in `--language` (default Python3).

Importing also makes that directory the **active** puzzle, so everything afterwards finds it
without `--puzzle-dir` — see [active working directories](../concepts/profiles.md#active-working-directories).

```bash
cg puzzle where           # prints just the resolved path -- built for $(...)
cg puzzle description     # the problem statement, rendered, no network needed
```

`where` writes nothing but the path to stdout, so it composes:

```bash
$EDITOR "$(cg puzzle where)/data/solution.py"
cd "$(cg puzzle where)"
```

It exits non-zero if no working directory can be found, rather than printing prose a shell would
happily substitute into a path.

## Running tests

```bash
cg puzzle play            # every downloaded test case, entirely locally
cg puzzle play 3          # just test 3
cg puzzle play 1 2 5      # a few
cg puzzle play --show-stdout
```

`play` never touches the network. Output is compared exactly as CodinGame compares it, so a pass
here means a pass there — see [output comparison](../design/final-newlines.md#output-comparison).
Captured stdout is only printed for failures unless you ask for it.

There's also a server-side equivalent:

```bash
cg puzzle play-server     # the IDE's "Test" button
```

**This durably saves your code on the server**, as a side effect of running. It's not a submission
and it isn't graded, but it does overwrite what CodinGame has stored for this puzzle and language.
Prefer `cg puzzle play` unless you specifically need the server's own runner.

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
cg puzzle status --refresh  # also check against the server, and fetch live progress/score
cg puzzle diff              # unified diff, local vs the server's last-submitted answer
cg puzzle discard-local     # throw local edits away, take the server's copy
```

There is no merge machinery here, deliberately — a puzzle has exactly one editable file, so there's
nothing three-way to resolve. Look at the diff, then keep one side or the other.

## Recovering

```bash
cg puzzle repair
```

Rebuilds `.meta/` — the test-session handle, downloaded test cases, cached statement — from
`puzzle.json`, without touching `data/`. Use it after a fresh clone (`.meta/` is gitignored) or if
anything in the cache looks wrong. Deleting `.meta/` and repairing is always safe.

```bash
cg puzzle activate ./other-puzzle   # switch which one commands act on
cg puzzle activate                  # ...or activate the current directory
cg puzzle deactivate                # back to the configured default
cg puzzle delete
```

`delete` removes the local directory only, and deactivates it if it was active. There's no server-side counterpart — the puzzle exists
independently of you and isn't yours to remove. Prompts unless `--force`.

## Full reference

Every flag of every command: **[`cg puzzle` reference](reference/puzzle.md)**.
