# Authoring contributions

A contribution is a puzzle you're writing for other people. Unlike a puzzle, it has a dozen editable
pieces — statement, constraints, descriptions, stub generator, test cases, reference solution — any
of which can change on the server while you're working. So the working directory is backed by a real
git repository and there's a real merge workflow.

## Two ways to start

```bash
cg contribution create ./my-puzzle "My Puzzle"   # brand new, purely local
cg contribution import ./my-puzzle <handle>      # existing server-side contribution
```

`create` makes **no network call** and creates nothing server-side. Nothing exists remotely until
your first `push` — so you can start, change your mind, and delete the directory without ever having
published anything.

It seeds **every** file you'll edit, so they can be listed, opened and diffed rather than conjured
from memory: statement, input/output descriptions, constraints, stub generator, a Python3 starter
solution, and a test/validator pair. All of it is placeholder content describing the same trivial
"read one integer, print it back" puzzle, so the pieces agree with each other until you replace
them — `cg contribution play` passes on a freshly created directory.

That self-consistency matters most for `stub_generator.cgstub`, the one seeded file that isn't
inert: CodinGame runs it to generate the starter code every solver of your puzzle begins from. A
stub generator that disagrees with your test cases hands them a program that reads the wrong thing.
Keep the two in step — nothing checks it for you. See CodinGame's
[stub generator syntax](https://github.com/CodinGame/codingame-game-engine/blob/master/stubGeneratorSyntax.md).

`data/cover.png` is seeded too, with a deliberately garish 1920×1080 "UNDER CONSTRUCTION" image —
traffic cones, hard hat, hazard stripes. That's the one seeded placeholder that becomes *visible*,
since `push` uploads whatever is in that file. A tasteful title card would sail past you unnoticed
and end up published; this can't. New contributions are private drafts, so nobody else sees it in
the meantime.

The image is shipped as package data rather than rendered on demand — it's identical for every
contribution, so generating it at runtime would mean every user of this library carrying a 15 MB
imaging dependency to produce a constant. Regenerate it with `bin/gen-default-cover-image`.

Both take the **directory first**, matching `cg puzzle import`, and both make it the
[active contribution](../concepts/profiles.md#active-working-directories) — so subsequent commands
find it wherever you run them from.

`import` needs the contribution's handle. To find your own:

```bash
cg contributions
```

`cg contribution where` prints just the resolved path, so it composes:

```bash
$EDITOR "$(cg contribution where)/data/statement.cgmd"
cd "$(cg contribution where)"
```

To switch between working directories you already have:

```bash
cg contribution activate ./other    # or with no argument, the current directory
cg contribution deactivate          # back to the configured default
```

## What you edit

Everything under `data/`:

```
data/
    statement.cgmd            the problem statement
    input-description.cgmd
    output-description.cgmd
    constraints.cgmd
    stub-generator.cgstub
    solution.<ext>            the reference solution
    cover.png
    tests/                    ordinal/named/{local,validator}/{input,output}.txt
    contribution-data.json    title, difficulty, topics, language
```

Test cases are directories, not one blob, so they diff and merge sensibly:

```
tests/01/Simple-case/local/input.txt
tests/01/Simple-case/local/output.txt
tests/01/Simple-case/validator/input.txt
tests/01/Simple-case/validator/output.txt
```

Ordinals are a sort key, not an identity — insert `tests/05a/` and it sorts where you'd expect.
Tidy them up afterwards with:

```bash
cg contribution renormalize-tests
```

## Validate before you push

```bash
cg contribution play          # run the reference solution against every local test case
cg contribution play 2        # just ordinal 2
```

Entirely local, no network. This matters more here than for puzzles: `updateContribution` validates
your reference solution against **every** test case server-side and rejects the whole push if any
disagree. Running locally first turns a slow rejection into a fast one.

## Pushing

```bash
cg contribution status        # local summary, no network
cg contribution status --refresh
cg contribution push
```

`push` sends your content, then updates the internal `server`/`version-data` branches to match. On
first push for a `create`d directory it safely creates the server-side contribution.

**A push with nothing to push does nothing**, and says so:

```
$ cg contribution push
…/contribution is already up to date on the server--nothing to push. Use --force to publish a new version anyway.
```

That's deliberate rather than a convenience. CodinGame has no notion of an empty update — it
increments the version and re-runs moderation whether or not anything differs — so republishing
identical content costs you a review cycle and buries your real changes among no-op versions. Pass
`--force` when you want one anyway. The exit status is 0 either way.

Two more things worth knowing:

- **A contribution stores exactly one solution, with no history.** Each push overwrites the last
  durable copy. `.meta/`'s git repo is scaffolding for merges — not a backup.
- **A heavy contribution can take long enough to time out at the CDN.** `push` handles the HTTP 524
  case by polling until the version increments, rather than failing on a request that probably
  succeeded.

## When the server moves under you

```bash
cg contribution rebase
```

Detects drift and resolves it when unambiguous: a no-op if the server hasn't advanced, a fast-forward
if you have no local edits. When it genuinely conflicts, use the merge state machine — which is
ordinary git, on ordinary files:

```bash
cg contribution merge start      # fetch, then a real `git merge server`
# ...resolve conflict markers in data/ with your editor...
cg contribution merge continue   # stage and commit
cg contribution merge abort      # or back out entirely
```

`merge continue` refuses if a file still contains conflict markers, which catches the classic
"resolved" -that-wasn't.

```bash
cg contribution discard-local    # give up on local edits, match the server exactly
```

## Changing language

```bash
cg contribution set-language C++
```

**Destructive by design** — a contribution has one solution and no per-language memory, so this
replaces it with a starter stub and there's nothing to switch back to. It refuses unless the current
solution is still the generated stub; `--force` discards real work. Save it somewhere outside the
working directory first. See [languages](../concepts/languages.md).

## Deleting

```bash
cg contribution delete
```

Deletes the contribution **from the server**, unrecoverably, and by default removes the working
directory too, deactivating it if it was active. Unlike `cg puzzle delete`, this one is not
local-only.

## Recovering

```bash
cg contribution repair
```

Rebuilds the git-dir from scratch without disturbing what's already in `data/` — for a fresh clone
(`.meta/` is gitignored) or a corrupted repo.

## Full reference

Every flag of every command: **[`cg contribution` reference](reference/contribution.md)**.
