# Debugging solutions

Set a breakpoint in your solution, run a test case, and step through it — including for compiled
languages you have no local toolchain for.

## Generate the VS Code configuration

```bash
cg vscode install
```

One command for puzzles and contributions alike — with no arguments it sets up every working
directory it can find: the one you're standing in, plus your active puzzle and active contribution.
Pass `--file` to limit it to one.

It writes into your **workspace root's** `.vscode/`, merging with what's already there. Workspace
root, not the working directory, because VS Code only reads `launch.json` from the workspace root —
a `.vscode/` inside a subdirectory is ignored.

**Run it once per language, not once per working directory.** The generated entries contain nothing
specific to a puzzle or contribution, so a single `CG C++: Debug solution` serves every C++ working
directory in the workspace, now and in the future. You do not need to re-run it after an `import`, a
`repair`, a `set-language`, or when you start a new puzzle.

Nothing is written unless you ask: `import`, `create` and `repair` never touch `.vscode/`.

That works because both questions a debug launch has to answer are deferred to launch time:

| Question | Answered by |
| --- | --- |
| Which working directory? | VS Code's `${file}` — whichever tab you have focused |
| Which test case? | that directory's [selected test](#choosing-which-test-to-debug) |

### What it will and won't touch

Generated entries are named in three parts:

```
CG C++: Debug solution
└┬┘ └┬┘  └─────┬─────┘
 │   │         └─ a well-known action name
 │   └─────────── the language
 └─────────────── marks the entry as cg-managed
```

Each level earns its place:

- **`CG `** identifies every entry `cg` has ever written, in any version, so none of them can become
  permanent clutter you'd have to find and delete by hand.
- **The language** keeps languages independent. Provisioning your C++ puzzle will never disturb the
  Python entry in the same workspace.
- **The action** comes from a fixed vocabulary, so re-provisioning replaces an entry rather than
  adding a second one beside it.

Everything else in the file is yours and is left exactly as it was — including anything of your own
that merely starts with `CG`, since only the full `CG …: ` shape counts. Entries are replaced *in
place*, so re-provisioning doesn't reshuffle your file, and a file whose content wouldn't change
isn't rewritten at all. If it can't safely merge — a `launch.json` with comments, say — it says so
rather than mangling your file.

Upgrading picks up after older versions automatically: anything in the `CG …: ` namespace that this
version doesn't generate is removed, whatever it was called. That covers 1.0.x's one-per-working-
directory entries (`CG puzzle: …`) and their `pickString` test-case inputs.

### Checking whether it's current

```bash
cg vscode install --check
```

Reports what would change and exits non-zero if anything would, without writing. Useful after
upgrading `cg`, or in a pre-commit hook if you keep `.vscode/` in version control. There's no
version stamp to compare against — the generated content *is* the version, so "would rewriting
change anything?" is exactly the right question.

## Choosing which test to debug

Running executes every test case; debugging needs exactly one, because there's only one stdin.

```bash
cg puzzle select-test 3
cg contribution select-test 03 local
```

The choice is recorded in that working directory's `.meta/`, so it's per-directory and survives
until you change it. Without one, debugging uses the first test case — the first *local* test for a
contribution, since validators are the hidden scoring ones and landing in one would be surprising.

## Python

The debugger launches your solution in-process, so a breakpoint set directly in `data/solution.py` is
hit. Stdin is bound to the selected test's input.

The target runs at exactly the path you had focused, so there is no path mismatch to work around.

## C++ (and other containerised languages)

No local compiler, no local gdb, no local anything except Docker.

`gdb` runs *inside the container*, reached through `docker exec`, and launches your program itself —
exactly as it would for a local target. Breakpoints are wired before a single instruction runs, and
**your program's output goes to the Debug Console**, like any ordinary debug session. Stdin is fed
from the selected test case, so a solution that reads input never waits on the terminal.

Both output streams appear there, in order. `stderr` is merged into `stdout` deliberately: the debug
adapter reads only the debugger's stdout, so an unmerged `stderr` would vanish — `cerr` diagnostics
being exactly what you reach for while debugging. Both are unbuffered, so they appear as they happen
rather than in a lump when the program exits.

### Why there is no path mapping

You set breakpoints in `data/solution.cpp`, that is what gets compiled, and that is where the editor
stays when they are hit. The generated launch configuration contains **no `sourceFileMap` at all**.

Two things make that possible, and each was learned the hard way. Through 1.x there was a fixed
`data/solution.src` with a `solution.<ext>` symlink beside it, and a debug build had to compile one
or the other. Both choices were wrong: a debugger reports two paths for a stop location — the one in
the debug info, and its own `realpath` of it — and the editor navigates by the second. Compiling the
symlink made them disagree, so a breakpoint bound and then yanked you to `data/solution.src`; adding
a `sourceFileMap` to fix that broke the *binding* instead, because the mapping applies in **both**
directions and the editor began translating breakpoints back to the real path before sending them.
Hollow breakpoints. There is one real file now, so the two paths are identical.

The second is the mount, below.

Your **workspace** is bind-mounted read-only into the container *at its own path* — `/home/me/work`
inside the container is `/home/me/work` on the host. Two things follow. Breakpoints need no path
mapping at all, because the paths the compiler recorded are already the paths your editor has open.
And one container serves every working directory in the workspace, rather than one per puzzle.

One task runs before the session, to build the debug profile and stage the test case. It has nothing
to show unless the build fails, in which case compiler errors appear in the Problems panel and the
launch stops. You can run it by hand:

```bash
cg debug start --file puzzle/data/solution.cpp
```

> **Why no gdbserver?** It exists for targets that can't run gdb — embedded boards, foreign
> architectures, machines reachable only over a network. Here gdb is already *on* the target, so a
> second debugger-side process in the same container would buy nothing and cost the thing that
> matters: whoever launches the program owns its stdin, stdout and stderr. With gdbserver launching
> it, the program's output went to gdbserver's terminal where the editor never saw it. This is the
> same arrangement VS Code's own Dev Containers support uses, which also has no gdbserver.

### First run is slow

One image carries **every** language `cg` can containerize — about 1.9 GB, which is far less than
the sum of its parts because the large toolchains (JDK, .NET, Node) share one Debian base. Building
it the first time takes minutes; afterwards it's cached and effectively instant.

To get it over with before you start:

```bash
cg docker toolchain build
```

That produces exactly the image a first run would build, under the same content-addressed tag, so
the run finds it already there. See [what CodinGame actually runs](../design/codingame-runtime.md)
for how the pinned versions were chosen, and [composable toolchain
images](../design/toolchain-images.md) for why it's one image rather than one per language.

### Trimming it

If you only ever use one or two languages, name them:

```bash
cg docker toolchain list                    # what's available
cg docker toolchain show --languages C++    # what it would build, and the tag
cg docker toolchain build --languages C++   # ~400 MB instead of ~1.9 GB
```

To make that the default, set `toolchainLanguages` in your settings; to skip building altogether and
pull a prebuilt image, set `toolchainImage`. Both also work per project.

### Tweaking the toolchain

The image is defined by a Dockerfile in a shared per-user location, split in two: a `cg`-owned base
that's regenerated when the tool updates, and a `custom.dockerfile` of your own that's appended and
never touched. Put extra packages in the second one. Because it's shared, tweaking it once applies
to every puzzle and contribution.

Anything you add there changes the image tag, so the image rebuilds by itself — there's nothing to
remember to invalidate. `cg docker toolchain show --composed` prints exactly what would be built,
your additions included.

### Cleaning up

```bash
cg docker clean
```

Removes every container and image `cg` created. Always safe, no prompt, no `--force` — no user work
ever lives in one. Your source is bind-mounted read-only from disk and all build artifacts are
disposable, so the worst case is one slow rebuild.

## What gets debugged with what stdin

A detail that matters if you're comparing local behaviour against the server: the debugger feeds
exactly the same bytes as `cg ... play` and as CodinGame itself. For contributions that means the
final newline this client adds to test-case files on disk is stripped back off before it reaches
your program. See [final newlines](../design/final-newlines.md) — this was a real bug, and it's
one byte.
