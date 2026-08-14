# Contributing

This project uses [PDM](https://pdm-project.org/) for dependency management, linting, type checking, and testing.

### 1. GitHub environments

Run script `bin/install-github-invironments` to set up GitHub for building and publishing.
This will create two GitHub environments:

- **`testpypi`** — used by `publish-test.yml`
- **`pypi`** — used by `publish.yml`

No secrets or protection rules are required; the environment names are what
the workflows reference.

### 2. TestPyPI trusted publisher

On [test.pypi.org](https://test.pypi.org), go to **Account → Publishing → Add a
new pending publisher** (use "pending" because the project won't exist there yet):

| Field | Value |
|---|---|
| PyPI project name | `codingame-tools` *(or your fork's package name)* |
| Owner | `mckelvie-org` *(or your fork's GitHub owner) |
| Repository | `codingame-tools` |
| Workflow | `publish-test.yml` |
| Environment | `testpypi` |

### 3. PyPI trusted publisher

On [pypi.org](https://pypi.org), same flow — **Account → Publishing → Add a new
pending publisher**:

| Field | Value |
|---|---|
| PyPI project name | `codingame-tools` *(or your fork's package name)* |
| Owner | `mckelvie-org` *(or your fork's GitHub owner) |
| Repository | `codingame-tools` |
| Workflow | `publish.yml` |
| Environment | `pypi` |

### Trigger sequence

Once the above is in place:

1. (optional) `bin/bump-dev [patch|minor|major]` — for a deliberate semantic version bump on `main`
2. `bin/cut-rc` — pushes a `v*-rc.*` tag → triggers `publish-test.yml` → TestPyPI, then waits for that workflow and reports its outcome
3. `bin/cut-prod` — promotes the most recent published rc (`rc-latest`) → pushes a `v*.*.*` tag → triggers `publish.yml` → PyPI, then waits for that workflow

Run both from wherever you like: `cut-prod` builds entirely from the rc tag and never reads `main`
or your working tree, so there's no branch to check out first and no need for a clean tree.

---

## Development setup

```bash
bin/install        # first-time setup (installs PDM and dependencies)
pdm install -G dev # subsequent dependency updates
pdm run lint       # ruff check
pdm run typecheck  # mypy
pdm run test       # pytest
pdm build
```

## Generated files

Two files in the tree are produced by tools rather than written by hand. Both are committed --
Python has no build step, so an uncommitted file simply doesn't exist for anyone who clones or
installs -- but they are governed differently, on purpose.

| | `doc/cli/reference/` | `codingame_tools/contribution_manager/assets/cover-placeholder.png` |
|---|---|---|
| Regenerate with | `bin/gen-cli-reference` | `bin/gen-default-cover-image` |
| Derived from | the `cg` argparse tree, i.e. all of `cli/main.py` | `scripts/gen_cover_placeholder.py` alone |
| Treated as | a **build artifact** | **source** |
| May drift on `main`? | yes, but bounded -- see below | no |
| Kept in sync by | `bin/cut-rc-prep`, at each release candidate | you, when you edit the generator |

**The CLI reference** changes whenever any command's arguments or help text change, which is often
and rarely on the mind of whoever is changing it. Rather than fail builds over that, `main` carries
a cached copy that is allowed to lag between releases -- nothing in CI enforces otherwise -- and
`bin/cut-rc-prep` regenerates it, commits it and pushes it to `main` as its own commit at the start
of every `cut-rc`. So drift is bounded by one release cycle rather than unbounded, and because
`cut-rc` builds its release worktree from `HEAD` afterwards, the tagged commit inherits exactly the
same files: `main` and the tag agree by construction, not coincidence. Refresh yours whenever you
like -- `bin/gen-cli-reference`, then `git status` shows the diff.

(`bin/gen-cli-reference` and `bin/gen-default-cover-image` are thin wrappers over the `pdm run` scripts of
the same names, so that `ls bin/` lists everything you can do to this project in one place. Either
spelling works.)

**`bin/docs` previews the site**: it serves it locally and opens it in a dedicated Chromium
window with no address bar, shutting the server down when you close the window. That coupling
is the point -- a bare `mkdocs serve` outlives the tab you were reading and squats on a port
until you remember it. It reuses the Playwright `cg login` already depends on, in a throwaway
profile that cannot see saved credentials, and picks a free port rather than fighting over 8000.

**`bin/gen-docs` runs both documentation generators**: the CLI reference above, then the
documentation site (`mkdocs build --strict`). The site's API reference is generated at build time
and never committed -- its value is the cross-links, which only resolve once mkdocstrings and
autorefs have run, so a checked-in copy would be both stale and dead. `bin/gen-docs --serve` runs a
live-reloading local server instead, which is what you want while writing docs.

Regeneration also *prunes*: a page whose command was renamed or removed is deleted, not left behind.

### The `cut-rc-prep` hook

`bin/cut-rc` is meant to be reusable across projects and deliberately contains no policy of its own.
Anything a specific project must do on `main` before a release is tagged goes in `bin/cut-rc-prep`,
which `cut-rc` runs if it exists and is executable. A project needing none simply doesn't have the
file.

The contract: **`cut-rc-prep` may commit and push to `main`; when it returns, the working tree must
be clean and in sync with `origin/main` again.** `cut-rc` verifies that both before and after
calling it, rather than trusting it -- everything downstream tags whatever `HEAD` happens to be, so
a hook that left work uncommitted would ship a release built from a tree nobody can reproduce.

**The cover placeholder** has exactly one input, changes almost never, and its output is a
non-reproducible binary (PNG bytes vary with Pillow and zlib versions), so an automated sync check
would be flakier than the problem it guards. If you edit the generator, run `bin/gen-default-cover-image` and
commit both. Pillow is a dev-only dependency and never reaches users.

## Release workflow

Releases follow a three-channel model:

| Channel | Tag format             | Moving tag   | Index    |
|---------|------------------------|--------------|----------|
| dev     | —                      | —            | —        |
| rc      | `v<x.y.z>-rc.<n>`     | `rc-latest`  | TestPyPI |
| prod    | `v<x.y.z>`             | `prod-latest`| PyPI     |

The `main` GitHub branch is the only relevant branch, and always carries `X.Y.Z-dev.N` as the package version.
It is never necessary to check out any other branch. Releases are driven entirely by tags on branchless commits —
no `rc` or `prod` branches are created. When a release is cut, a custom tagged commit with only the modified version
information is created--that commit is not on any branch, and the only thing keeping the commit alive is
the tag that labels it. This approach ensures that prod/rc version labels never leak into development code, and that
dev versions always apear "newer" than rc or prod versions they are based on, and older than the prod/rc versions they become.

### Bump the dev version

Cutting an rc doesn't require bumping the dev release number first -- the rc counter
(`X.Y.Z-rc.N`) is unique on its own, independent of whatever `N` `main`'s own `X.Y.Z-dev.N`
currently carries. Use `bin/bump-dev` when you want a deliberate version bump ahead of time,
most commonly a semantic one (a new `X.Y.Z`):

```bash
bin/bump-dev [dev|patch|minor|major]   # edits pyproject.toml, does not commit
```

| `bump_type` | Example |
|-------------|---------|
| `dev`       | `1.0.0-dev.1` → `1.0.0-dev.2` |
| `patch`     | `1.0.0-dev.2` → `1.0.1-dev.1` |
| `minor`     | `1.0.0-dev.2` → `1.1.0-dev.1` |
| `major`     | `1.0.0-dev.2` → `2.0.0-dev.1` |

This will modify pyproject.toml to reflect the version change.
Commit and push to `main` before cutting a release.

### Update the changelog

Add (or revise) a `CHANGELOG.md` entry for your in-progress work, using this exact heading:

```markdown
## {{UNRELEASED}}

- Your release notes here.
```

`{{UNRELEASED}}` is a version-independent sentinel, not a version number you fill in yourself --
`cut-prod` (and the post-release sync back to `main`) stamp in the actual version and date when the
release actually happens, so there's no version string here that can ever drift out of sync with
`pyproject.toml`'s. `cut-rc` and `cut-prod` parse this literally:

- `cut-rc` checks whether `## {{UNRELEASED}}` already exists in `CHANGELOG.md`. If not, it inserts
  a placeholder (`- _Add release notes here._`) so a release never goes out with zero changelog
  entry -- but you should normally have already written the real one yourself before cutting.
- `cut-prod` rewrites that heading to `## X.Y.Z (<date>)` (filling in both the version it's
  promoting and today's date), finalizing it, when promoting an rc. This happens on the frozen
  release commit only -- it's a historical record of exactly what shipped, so it doesn't add
  anything beyond that.
- The `Publish` workflow triggered by `cut-prod` mirrors that same finalization back onto `main`
  itself as part of its post-release sync (see below), so `main`'s copy doesn't stay stuck reading
  `{{UNRELEASED}}` forever once the release has actually shipped -- and, unlike `cut-prod`, it also
  leaves a fresh, empty `## {{UNRELEASED}}` heading in place on `main`, ready for the next round of
  notes, so you never have to remember to re-add it yourself.

**`cut-rc` refuses to cut an rc whose `## {{UNRELEASED}}` entry is missing or still the
placeholder.** The notes have to exist *before* the rc, because `cut-prod` promotes the rc snapshot
verbatim and never consults `main` -- anything written afterwards can never reach the release. The
check runs before anything is pushed or tagged, so a refused run doesn't first burn an rc number and
a TestPyPI publish discovering it.

If there genuinely is nothing to report, `bin/cut-rc --no-changelog` says so explicitly and records
`- No notable changes.`, rather than shipping the placeholder as the permanent record. It commits
that to `main` too, so `main` and the rc agree.

`cut-prod` keeps the same check as a backstop. Reaching it means `cut-rc`'s gate was bypassed (or
the rc predates it), and by then it can only refuse -- there is no `main` for it to pick notes up
from.

You're free to revise the entry and cut additional rc's as many times as you like -- each `cut-rc`
re-snapshots whatever `main` currently looks like -- just make sure the changelog reflects what you
actually want released *before* the rc you end up promoting with `cut-prod`. Editing `main`'s
changelog *after* your last `cut-rc` and then running `cut-prod` directly will not pick up that
edit: `cut-prod` only ever reads the frozen `CHANGELOG.md` content of the specific rc commit you're
promoting, never live `main`.

### Cut a release candidate on TestPyPi

Run from `main`:

```bash
bin/cut-rc [--force] [--no-changelog]
```

- `--force` — overwrite an existing rc tag (for retrying a failed publish).
- `--no-changelog` — assert there are no reportable changes for this release; see the changelog
  section above. Without it, `cut-rc` refuses when the unreleased entry is missing or unfilled.

`cut-rc` first requires a clean working tree and local `main` to be exactly in sync with
`origin/main` (any mismatch -- ahead, behind, or diverged -- is an error; pull/rebase and/or push
to fix). From `main`'s current `X.Y.Z-dev.N`, it finds the next unused rc counter from existing
`v<x.y.z>-rc.*` tags, sets the version to `X.Y.Z-rc.N` in a worktree, tags the commit
`v<x.y.z>-rc.<n>`, and pushes the tag — triggering the `Publish TestPyPI` workflow.

`cut-rc` then waits for that workflow and shows its live status. The workflow itself polls
TestPyPI until the version is actually visible there before reporting success (the upload API call
succeeding doesn't mean the index has caught up yet -- there's a brief propagation window), and
updates the `rc-latest` tag. If the workflow fails, `cut-rc` reports it and exits non-zero.

Use `--force` to overwrite an existing tag and retry a failed publish.

### Cut a production release

`cut-prod` promotes an rc that has *already been published to TestPyPI* -- it isn't just working
from a version string, it verifies the rc is really there. Normally you just run it:

```bash
bin/cut-prod [--force]
```

No checkout, no clean working tree, no particular branch. The release is built entirely from the rc
tag in a throwaway worktree, so **nothing about your local state can affect what ships** -- and
requiring a clean tree would block a release for reasons that cannot influence its content. `main`
is neither read nor modified; the publish workflow updates it afterwards.

`RC_REF` (`bin/cut-prod [--force] [RC_REF]`) is optional. Resolution order:
1. Explicit argument (tag, sha, or bare version like `1.0.5-rc.1`).
2. `HEAD`, if it is itself tagged with a `v<x.y.z>-rc.<n>` tag (i.e. you deliberately checked one out).
3. The `rc-latest` tag -- the most recently *successfully published* rc, and the normal case.

`cut-prod` requires exactly one thing:
- The resolved rc version must actually exist on TestPyPI (checked directly against
  `test.pypi.org`, with a short retry to ride out any residual index-propagation lag) --
  promoting an rc that never successfully published is refused.

It also warns (without blocking) if `origin/main` has moved since the rc was cut, in case that
matters to you.

Strips the rc qualifier, commits to a worktree, tags the commit `v<x.y.z>`, and pushes the tag —
triggering `Publish`, which (like `Publish TestPyPI`) polls PyPI until the version is actually
visible before reporting success, updates `prod-latest`, and then syncs `main`: finalizes
`CHANGELOG.md`'s `## {{UNRELEASED}}` entry to `## X.Y.Z (<date>)` if `main` still has one (leaving a
fresh `## {{UNRELEASED}}` heading in its place, ready for the next round of notes), and ensures
`main` carries a dev version strictly ahead of the one just published, bumping
to `X.Y.(Z+1)-dev.1` if needed. Either, both, or neither may apply on a given run -- e.g. `cut-rc`
already bumped `main` itself, or a concurrent release already synced the changelog. This is safe
with other developers landing commits on `main`, or another release racing to sync it at the same
time: every attempt re-fetches the live tip of `main` and re-evaluates from scratch what's still
needed, rather than trusting a stale snapshot, and retries a few times if its push loses a race
(the winner's sync may already satisfy the requirement, in which case there's nothing left to do).

After pushing the prod tag, `cut-prod` waits for the triggered `Publish` workflow to finish and
shows its live status. On failure it reports the error and exits non-zero.

On success it *tries* to sync your local `main` with `origin/main` (which the workflow may have just
bumped), as a pure convenience -- rebasing in place if `main` is your current branch and your tree
is clean, otherwise just fast-forwarding the local `main` ref. Every part of that is best-effort and
prints a note if it's skipped: the release is already published by then, so nothing here is allowed
to fail the command or touch your working tree. Sync by hand whenever you prefer.

Use `--force` to overwrite an existing tag and retry a failed publish.

### Guards

Both publish workflows validate that:

- The version in `pyproject.toml` matches the expected format for the target index.
- The version does not already exist on the target index.
- Lint, type checks, and tests pass.

### Smoke test

Use the **Install Smoke Test** workflow to verify an install without publishing:

```bash
# From GitHub source
gh workflow run install-smoke.yml --field source=github --field git_ref=main

# From TestPyPI
gh workflow run install-smoke.yml --field source=testpypi --field version=1.0.0rc1
```

## Forking this repository

If you fork this project and want the full release pipeline to work, you need
to wire up OIDC trusted publishing on both PyPI indexes.
