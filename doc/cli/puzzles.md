# Solving puzzles

Pull a puzzle into a local directory, solve it in your own editor, run its tests without touching
the network, and submit when you're happy.

## The loop

```bash
cg puzzle import temperatures                   # -> puzzles/temperatures, and makes it active
$EDITOR "$(cg puzzle where)/data/solution.py"   # solve it
cg puzzle play                                  # run every test case, locally
cg puzzle submit                                # graded submission
```

That's the whole thing. The rest of this page is what to do when it isn't that simple.

## Importing

```bash
cg puzzle import temperatures
cg puzzle import 10075
cg puzzle import "Temperatures"
```

`PUZZLE` is resolved in this order: numeric puzzle ID, exact pretty ID, exact title,
case-insensitive title.

The working directory is created at **`puzzles/<pretty-id>`** under the project root — the directory
holding `.cg/`, or the current one if there is none. `--puzzle-dir` overrides it:

```bash
cg puzzle --puzzle-dir ./scratch import temperatures
```

If you've attempted the puzzle before, your saved answer is imported in whatever language you last
used; otherwise you get a placeholder in `--language` (default Python3).

### Starting from your own template

Most solutions start the same way. `--template` seeds the solution file from one of yours instead
of the one-line placeholder:

```bash
cg puzzle import --template ./templates/solution.py temperatures
cg puzzle import -t solution.py --template-path ~/cg-templates temperatures
cg puzzle import --template-path ~/cg-templates temperatures   # picks solution.<ext>
```

`--template-path` is a search path: directories separated like `PATH`, and the option may be
repeated. Rather than passing it every time, set it once in `.cg/config/config.yaml`:

```yaml
templatePath: ../../templates    # or a "PATH"-separated string, or a YAML list
```

Relative entries resolve against the config file's own directory, the same as `dataDir` — so from
`.cg/config/`, the project's own `templates/` is `../../templates`. `--template-path` then *extends*
the configured path rather than replacing it, and is searched first. A configured directory that
doesn't exist is reported, since it was named deliberately. A `--template` containing a path separator is used as given; a bare filename is looked up
there. With no `--template` at all, `solution.<ext>` for the solution language is looked up, so a
directory of per-language templates works with no flag.

Two tokens are expanded inside the template:

| token | expands to |
| --- | --- |
| `${PUZZLE_DETAILS}` | a plain-text rendering of the puzzle — goal, rules, input and output descriptions, constraints, and the first test case as a worked example — indented to match where the token sits |
| `${CG_URL}` | the puzzle's CodinGame IDE link, `https://www.codingame.com/ide/puzzle/<pretty-id>` |

```python
#!/usr/bin/env python3
# ${CG_URL}
"""
${PUZZLE_DETAILS}
"""
import sys
```

Anything that would *end* a block comment is defused first (`*/` becomes `* /`, runs of quotes are
shortened), so a statement containing one can't close your docstring halfway through. Nothing else
is substituted — `$` and `{}` elsewhere in your code are left alone.

> **A template never overwrites your own code.** If CodinGame already has a solution saved for this
> puzzle and language, that is imported and the template is ignored.

The same options work on `cg puzzle set solution-language`, which is the other moment a solution is
created from nothing:

```bash
cg puzzle set solution-language C++ --template-path ~/cg-templates
```

The default lookup uses the language you're switching **to**, so a directory of per-language
templates picks `solution.cpp` here. As with `import`, your own saved code for that language wins if
CodinGame has any.

For an interactive puzzle the example is omitted, with a note saying why: its stored test case is
the referee's starting configuration, not the input your program reads.

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

### Starting over

`reset` throws the current solution away and writes a fresh one, with the same
`--language`/`--template`/`--template-path` options as `import`:

```bash
cg puzzle reset                              # back to the placeholder
cg puzzle reset --template-path ~/cg-templates
cg puzzle reset --language C++               # and switch language
```

It's the counterpart to [`set solution-language`](#switching-language), and deliberately different:
that one *restores* whatever CodinGame has saved, while `reset` always regenerates. That makes it
the one command here that can destroy work with no copy anywhere, so it asks first:

```
About to discard this solution and write a fresh one. It is neither saved on CodinGame nor
untouched since cg wrote it, so there is no other copy:
  file:     .../data/solution.py (46 lines)
  language: Python3
  new from: ~/cg-templates/solution.py
Type RESET (all caps) to confirm, or anything else to cancel:
```

No prompt when there's nothing to lose — the solution is untouched since cg wrote it, or matches
what you've submitted. `--force` skips the prompt, and is required when stdin/stdout aren't a
terminal.

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

## Puzzles that can't be tested locally

Two kinds of puzzle can't be run against `cg puzzle play`, for different reasons. Both are detected
at import and refuse with an explanation rather than reporting a meaningless result.

**Interactive puzzles** (`gameloop` in the stub — Code vs Zombies, Mars Lander episodes 2 and 3)
trade moves with a **referee** turn by turn: your solution reads the turn's state, writes a move,
and the referee computes the next state from it. Only CodinGame has the referee, and the downloaded
test case is its *world configuration* rather than the input your solution reads — Code vs Zombies
supplies human positions with no ids, while the stub reads an id per human. So nothing local works:
no `play`, no debugging.

**Optimization puzzles** (`optim` — Travelling Salesman) are scored by how good your answer is, not
by matching a fixed one, so their test cases carry real inputs and **empty** expected outputs. A
local pass/fail would invert: a genuine answer never matches the empty file, while a solution that
printed nothing would pass every test.

The two are independent. Travelling Salesman is an optimization puzzle that is *not* interactive —
one input, one answer — so it runs and debugs locally perfectly well; it just can't be scored there.
Mars Lander is interactive but not an optimization puzzle.

| | `play` | debug | `play-server` | `submit` |
| --- | --- | --- | --- | --- |
| standard | yes | yes | yes | yes |
| optimization, one-shot | no | **yes** | yes | yes |
| interactive | no | no | **yes, turn by turn** | yes |

```bash
cg puzzle play-server              # runs it on CodinGame's servers, referee included
cg puzzle play-server --no-stdout  # drop the moves, keep your debug output
cg puzzle play-server --summary    # just the verdict, without what the run printed
cg puzzle submit                   # graded run
```

For an interactive puzzle, `play-server` reports the game turn by turn and the referee's score
rather than a pass/fail, laid out as CodinGame's own console is:

```
[DONE] test 1 (Simple) -- 9 turns, score 10
  start
    Game information:
      The dead are coming...
  turn 9/9
    Standard Error Stream:
      > Ash is at (7016,3827)
      > Playing move (7893, 4305)
    Standard Output Stream:
      > 7893 4305
    Game information:
      You held off the wave of zombies and scored 10 points.
```

The **first frame is the game's setup**, before your solution has moved, so turns are numbered from
the second — `turn 9/9` here is the turn the web IDE also calls 9/9. In a game where the referee
acts several times per move of yours, the frames it produced without reading anything are flagged
`(no input read)`; single-stepping in the web IDE pauses only at the others. Standard error is coloured red
as the IDE colours it, and CodinGame's own console colours are rendered in every stream. Every
stream is shown by default, as in the web IDE. `--no-stdout` and `--no-stderr` drop one stream and
keep the other — useful when your solution logs its own move, making the stdout section redundant —
and `--summary` trims to the verdict and the referee's narration.

To see the response itself rather than a rendering of it:

```bash
cg --json puzzle play-server 1
```

That prints every frame in full, including the referee's `view` — its serialized world state for
that turn, in the puzzle-specific format its browser viewer draws — and anything CodinGame sends
that this client doesn't model yet.

A run takes one request per test case, and the server plays the whole game before answering, so a
multi-test run pauses between test cases. Frames within a test case all arrive together.

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
cg puzzle set                       # the puzzle's editable fields
cg puzzle set solution-language     # just the current language
cg puzzle set solution-language C++
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
