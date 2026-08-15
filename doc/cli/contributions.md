# Authoring contributions

A contribution is a puzzle you're writing for other people. It has a dozen editable pieces —
statement, constraints, descriptions, stub generator, test cases, reference solution — any of which
can change on the server while you're working, so syncing is a real merge workflow rather than an
overwrite.

## Two ways to start

```bash
cg contribution create ./my-puzzle "My Puzzle"   # brand new, purely local
cg contribution import ./my-puzzle <handle>      # existing server-side contribution
```

`create` makes **no network call** and creates nothing server-side. Nothing exists remotely until
your first `push`, so you can start, change your mind, and delete the directory without ever having
published anything.

It seeds every file you'll edit with placeholder content describing the same trivial "read one
integer, print it back" puzzle. The pieces agree with each other until you replace them, so
`cg contribution play` passes on a freshly created directory.

> **`data/cover.png` is a garish "UNDER CONSTRUCTION" placeholder.** `push` uploads whatever is in
> that file, so replace it before you publish. New contributions are private drafts until then.

Both commands take the directory first, and both make it the
[active contribution](../concepts/profiles.md#active-working-directories), so later commands find it
wherever you run them from.

`import` needs the contribution's handle. To list your own:

```bash
cg contributions
```

## Finding your way around

```bash
cg contribution where               # just the resolved path, for $(...)
cg contribution activate ./other    # or with no argument, the current directory
cg contribution deactivate          # back to the configured default
```

```bash
$EDITOR "$(cg contribution where)/data/statement.cgmd"
cd "$(cg contribution where)"
```

## What you edit

Everything under `data/`:

```
data/
    statement.cgmd            the problem statement
    input_description.cgmd
    output_description.cgmd
    constraints.cgmd
    stub_generator.cgstub
    solution.<ext>            the reference solution
    cover.png
    tests/
    contribution-data.json    title, difficulty, topics, language
```

> **Keep `stub_generator.cgstub` in step with your test cases.** CodinGame runs it to generate the
> starter code every solver begins from, so a stub generator that disagrees with your tests hands
> them a program that reads the wrong thing. Nothing checks this for you. See CodinGame's
> [stub generator syntax](https://github.com/CodinGame/codingame-game-engine/blob/master/stubGeneratorSyntax.md).

### Test cases

Test cases are directories, one per test:

```
tests/01/Test-1/test.json                 {"title": "Test 1"}
tests/01/Test-1/local/input.txt
tests/01/Test-1/local/output.txt
tests/01/Validator-1/test.json
tests/01/Validator-1/validator/input.txt
tests/01/Validator-1/validator/output.txt
```

Each ordinal holds a local test, a validator, or both. A local/validator pair sharing the exact same
title is co-located under one directory, with `local/` and `validator/` side by side.

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

Entirely local, no network. This matters more here than for puzzles: pushing validates your
reference solution against **every** test case server-side and rejects the whole push if any
disagree. Running locally first turns a slow rejection into a fast one.

## Debugging

When the reference solution fails a test case and you want to step through it:

```bash
cg vscode install                    # generates the VS Code launch entries; once per workspace
cg contribution select-test 03 local # which test case to feed the debugger
```

Then press **F5** in VS Code with `data/solution.<ext>` focused. Breakpoints land in your solution
and stdin comes from the selected test case. Compiled languages build, run and debug inside Docker,
so C++ needs no local toolchain.

Full details, including containerised languages and how to pick a test case:
**[Debugging](debugging.md)**.

## Pushing

```bash
cg contribution status        # local summary, no network
cg contribution status --refresh
cg contribution push
```

On the first push from a `create`d directory, `push` creates the server-side contribution for you.
A contribution with heavy test cases can take a while.

A push with nothing to push does nothing, and says so:

```
$ cg contribution push
…/contribution is already up to date on the server--nothing to push. Use --force to publish a new version anyway.
```

CodinGame has no empty update: it bumps the version and re-runs moderation whether or not anything
differs, so republishing identical content costs you a review cycle. Pass `--force` if you want one
anyway. The exit status is 0 either way.

> **A contribution stores exactly one solution, with no history.** Each push overwrites the last
> durable copy. The git repo under `.meta/` is scaffolding for merges, not a backup.

## When the server moves under you

```bash
cg contribution rebase
```

A no-op if the server hasn't advanced, and a fast-forward if you have no local edits. When it
genuinely conflicts, use the merge commands — ordinary git, on ordinary files:

```bash
cg contribution merge start      # fetch, then a real `git merge server`
# ...resolve conflict markers in data/ with your editor...
cg contribution merge continue   # stage and commit
cg contribution merge abort      # or back out entirely
```

`merge continue` refuses if a file still contains conflict markers.

```bash
cg contribution discard-local    # give up on local edits, match the server exactly
```

## Changing language

```bash
cg contribution set-language C++
```

**Destructive** — a contribution has one solution and no per-language memory, so this replaces it
with a starter stub and there's nothing to switch back to. It refuses unless the current solution is
still the generated stub; `--force` discards real work. Save it somewhere outside the working
directory first. See [languages](../concepts/languages.md).

## Deleting

```bash
cg contribution delete
```

Deletes the contribution **from the server**, unrecoverably, and by default removes the working
directory too. Unlike `cg puzzle delete`, this one is not local-only.

## Recovering

```bash
cg contribution repair
```

Rebuilds the git-dir from scratch without disturbing what's already in `data/` — for a fresh clone
(`.meta/` is gitignored) or a corrupted repo.

## Full reference

Every flag of every command: **[`cg contribution` reference](reference/contribution.md)**.
