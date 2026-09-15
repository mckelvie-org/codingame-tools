# Profiles

A profile is a named identity plus its own stored [credentials](authentication.md). Every command
takes `--profile NAME`; without it, the default profile is used.

```bash
cg --profile dev login
cg --profile dev whoami
```

Two reasons this exists, and the second matters more than it looks:

- **More than one CodinGame account.** A personal one and a test one, say.
- **Somewhere safe to experiment.** Several operations in this project are irreversible against
  real content — pushing a contribution overwrites its only stored solution, submitting a puzzle
  creates a permanent graded submission. A separate profile is the sanctioned way to try those
  without touching anything you care about.

```bash
cg settings set default-profile dev     # make it the default
cg settings delete default-profile      # go back
```

## Configuration vs. settings

Two files, deliberately distinct, and mixing them up causes confusion later:

| | `config.yaml` | `settings.json` |
| --- | --- | --- |
| Owned by | you | the app |
| Edited by | hand | `cg settings set` |
| Holds | how you want things configured | state the app remembers for you |
| Under version control? | reasonably, for a project-local one | no |

```bash
cg config init            # create a project-local config.yaml here
cg config init --global   # create the shared per-user one instead
cg config where           # which config.yaml is in play, and where its data directory resolves
cg config dump            # the fully resolved configuration, as JSON
cg settings dump          # the fully resolved settings, as JSON
```

## Resolution order

Configuration resolves in three tiers, **field by field**, most specific first:

1. `settings.json` (app-managed state)
2. the project-local `config.yaml` (nearest one at or above the working directory)
3. the global `config.yaml`

Field-by-field is the part worth internalising. A project-local config that sets one key does not
mask the global config's other keys — earlier versions merged whole files, and a project config with
a single setting in it would silently blank everything else. If a value isn't behaving as you
expect, `cg config dump` shows you what actually won.

## Default working directories

Both managers look for a working directory rather than taking one as an argument every time, and
those defaults are per-profile settings:

```bash
cg settings set project-dir ~/cg
```

`projectDir` names where the `puzzles/` and `contributions/` trees live; without it, that is the
directory holding `.cg/`, or the current one. `cg puzzle where` and `cg contribution where` answer
"which directory would this
command actually use", which is usually faster than reasoning about it. They print **only** the
resolved path, so they compose: `cd "$(cg puzzle where)"`.

## Active working directories

A configured default says *where your work usually lives*. The **active** directory says *what
you're working on right now*, and it wins:

```bash
cg puzzle import ./temperatures temperatures   # activates ./temperatures
cg puzzle activate ./other                     # switch
cg puzzle activate                             # ...or activate the current directory
cg puzzle deactivate                           # back to the configured default
```

`cg contribution create`, `cg contribution import` and `cg contribution activate`/`deactivate` work
identically.

This exists because the two would otherwise fight. Set `puzzle-dir` to `~/cg/puzzle`, then import a
puzzle into `./scratch`, and without an active directory every following command would quietly
operate on `~/cg/puzzle` instead — the one place you weren't looking. Import and create therefore
record what they just built.

`delete` deactivates too, but only if the directory being deleted is the active one — deleting some
*other* working directory must not silently change what you're working on.

Stored in `settings.json` as `currentPuzzleDir`/`currentContributionDir`, and deliberately *not*
readable from `config.yaml`: it's state the app maintains for you, not a preference you declare, and
a config file pinning it would defeat `activate`/`deactivate` entirely.

## Full resolution order

Most specific first, for both puzzles and contributions:

1. `--puzzle-dir` / `--contribution-dir`
2. `CG_PUZZLE_DIR` / `CG_CONTRIBUTION_DIR`
3. the **active** directory (above)
4. the current directory, if it is itself a working directory

That's the whole list — no searching. Step 4 checks that one directory: not its parents, not a
`puzzles/` beneath it. So `cd puzzles/temperatures && cg puzzle play` works when nothing is active,
while the same command one directory up does not. The active directory outranks where you are
standing, so if one is set, `cd` changes nothing; `cg puzzle activate .` switches to the one you're
in.

`import` and `create` don't take a directory: they compute one from the puzzle's pretty id or the
contribution's title, under the project root — the directory holding `.cg/`, or the current one if
there is none. `--puzzle-dir`/`--contribution-dir` override that.
