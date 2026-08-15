# CHANGELOG

## {{UNRELEASED}}

- **A documentation site, with a generated API reference.** Published to GitHub Pages and versioned:
  `dev` tracks `main`, and every release gets its own `X.Y` with `latest` following the newest. The
  guides stay in `doc/` as plain Markdown and remain readable on GitHub exactly as before -- the site
  renders those same files rather than replacing them.

  The **API reference is new, and site-only**: the protocol dataclasses, the client services, and the
  puzzle and contribution manager APIs, generated from the source at build time. It is not committed,
  and that is the point. These docstrings carry 643 backticked cross-references and 729 attribute
  docstrings -- a bare string after a field annotation, which does not exist on the runtime object at
  all. Committed Markdown would render both as dead text; mkdocstrings and autorefs turn them into
  real links. Exactly one reference failed to resolve on the first build.

  A strict build runs in CI, so an unresolved cross-reference fails the build rather than rotting
  quietly -- which matters once a renamed symbol can silently break links nothing checks.

- **Fixed: the API reference sidebar was unreadable.** Entries used full dotted module paths, and
  Material's sidebar neither wraps, scrolls horizontally, nor resizes by dragging — so every
  protocol module rendered as `codingame_tools.client.common`, truncated at 29 characters, exactly
  where the names begin to differ. All 74 entries were indistinguishable.

  Labels are now relative to their area (`achievement.schema`, `clash_of_code_description.schema`),
  putting the distinguishing part where it survives. Not the bare leaf name: 18 modules are called
  `schema`. Page headings keep the full path. A small stylesheet lets any label that is still too
  wide wrap instead of vanishing, so a narrow window degrades to two lines rather than to nothing.
  Measured before and after in a real browser: 74 of 74 truncated, then 0 of 74.

- **PyPI's "Documentation" sidebar link points at the rendered site.** It pointed at
  `blob/main/doc/index.md` -- raw Markdown, on `main`, from the page of a version released months
  earlier. It now tracks `dev` and is pinned to the release's own `X.Y` by `bin/cut-rc`/`bin/cut-prod`,
  the same treatment README's links get. An rc is deliberately left on `dev`, since that is the code
  it ships.

- **The CLI command reference is generated at build time and no longer committed.** It joins the API
  reference in mkdocs' virtual file tree (`scripts/gen_cli_pages.py`), so 32 generated files leave
  the repository and PR diffs.

  It was committed for one reason -- the README linked readers straight at those Markdown files on
  GitHub -- and once the README started linking at the rendered site, only the downside was left:
  nothing in CI checked the committed copy was current, so a changed help string could publish a
  site documenting the previous one. Now it cannot go stale, because between builds it does not
  exist.

  Consequently `bin/gen-cli-reference` and `bin/cut-rc-prep` are gone: the release hook existed
  solely to regenerate and commit that directory. `bin/gen-docs` now just builds the site.

- **README links at the rendered documentation, not raw Markdown.** Every guide link now points at
  the site, which renders those same files with search and with live cross-links into the API
  reference; pointing at the Markdown sent readers to a strictly worse copy of the page they wanted.

  The links track the README they are in: `dev` on `main`, and that release's own `X.Y` once the
  release rewrite runs -- so a PyPI page documents the version you installed rather than whatever
  shipped afterwards. The rewriter pins `dev` as well as `latest` for this reason, and the three-way
  "this version / latest release / in development" link set is gone, since the site's own version
  selector does that job better.

- **New: `cg doc`** opens the documentation in a dedicated browser window -- the same wrapped
  Chromium `cg login` uses, with a throwaway profile that cannot see the saved session.

  It opens the docs for the version you are *running*, not the newest ones: the published site keeps
  every release side by side, so `2.0.1` opens `/2.0/` while a pre-release build (`2.1.0.dev1`,
  `2.1.0rc1`) opens `/dev/`, because its own series has not shipped yet and would 404.

  In a source checkout it serves that tree's own documentation instead, uncommitted edits included,
  and stops the server when the window closes. It builds into a **per-user cache**
  (`~/.cache/codingame/cg/docs/<checkout>-<hash>` on Linux/macOS), never into the checkout: `cg doc`
  is package functionality a user runs from anywhere, not a contributor tool operating on a tree
  they are working in. Both `cg doc` and `cg doc --no-rebuild` use that one directory, so the second
  serves exactly what the first produced. The cache is keyed by the checkout's path, so two clones
  never overwrite each other.

  The contributor tools keep the checkout's `site/`: `bin/gen-docs` builds it, `bin/docs -q` serves
  it, and `mike` deploys from it. (`bin/docs` itself keeps the live-reloading server, which serves
  from memory and writes nothing -- what you want while *writing* docs.) `--online` overrides that, `--version` shows another
  release's docs, `--no-rebuild` trades live-reload for opening about ten times faster, and `--url`
  prints the address rather than opening anything, for use over SSH or in a container.

- **`bin/docs -q` skips the rebuild** and serves the existing `site/` immediately -- 1.1s to a
  readable page against 9.8s, because a normal start re-runs mkdocstrings over the whole package
  before answering anything. It serves under the same base path as a real build, so every internal
  link resolves identically. Without `-q` the behaviour is unchanged, including live reload, which
  is what you want while writing docs rather than reading them.

- **The site title carries its version** -- `codingame-tools 2.0.1.dev1` locally, and the deployed
  alias (`codingame-tools 2.0`, `codingame-tools dev`) on a `mike` deploy. The version selector only
  helps while you are looking at it; the title travels with the browser tab, the bookmark and the
  search hit, which is where "which version is this?" actually gets asked.

- **The site is built with ProperDocs, not MkDocs.** MkDocs 2.0 removes the plugin system with no
  migration path, and this site is made of plugins -- mkdocstrings alone generates the 125-page API
  reference -- so 2.0 is not an upgrade for it. ProperDocs is the maintained continuation of MkDocs
  1.x and builds the existing `mkdocs.yml` unchanged; the plugins had already moved, requiring it
  and capping `mkdocs<=1.6.1` themselves, and `mike` detects it automatically, so the deploy workflow
  needed no change.

  Zensical, the other successor, was measured rather than assumed: at 0.0.54 it builds this site to
  53 pages instead of 178, silently dropping the entire generated API reference while `--strict`
  reports "No issues found". [`doc/design/docs-toolchain.md`](doc/design/docs-toolchain.md) records
  the reasoning and the re-test recipe for when that changes.

- **Fixed: malformed docstrings that the documentation build surfaced.** Two `Args:` blocks in
  `CgContributionServiceHelper` listed several parameter names on one line sharing a description,
  which Google style has no syntax for, so the whole block failed to parse. One continuation line in
  `cg_credentials` was indented seven spaces where eight were needed. And the `debug` entry points'
  usage lines put `[--update-expected]` in prose, where Markdown reads it as a reference link.

- _Add release notes here._

## 2.0.0 (2026-08-11)

- **BREAKING: the solution is one real file, `data/solution.<ext>`, renamed when the language
  changes.** The `solution.<ext>` symlink at the working directory root is gone, and so is the fixed
  `data/solution.src` behind it. Existing working directories migrate on the next
  `cg puzzle repair` / `cg contribution repair`: the file is renamed and the stale symlink removed.

  The symlink existed so editors would syntax-highlight a language-neutral `.src` file. It cost a
  day of debugging to find out what else it did. A debugger reports *two* paths for a stop location
  -- `file` from the debug info and `fullname`, its own `realpath` of it -- and the editor navigates
  by `fullname`. Compiling the symlink made them disagree, so a breakpoint bound correctly and then
  yanked the editor to `data/solution.src`. Adding a `sourceFileMap` to fix the navigation broke the
  *binding* instead, because that mapping applies in **both** directions: the editor translated the
  breakpoint back to the real path before sending it, the debug info named the symlink, and gdb
  could not place it. Hollow breakpoints.

  Every language server we add would have hit some version of that, because they all resolve the
  file and walk up from wherever it really is -- and `.src` is an extension none of them recognize.
  One real file with its language's own extension makes the two paths identical and deletes the
  problem rather than balancing it. `sourceFileMap` is now absent from the generated launch
  configuration entirely. Symlink support is also no longer required of the filesystem, which
  Windows grants only in developer mode.

  For a contribution the rename lands in `data/`, a git work tree, so git sees it on both `main` and
  the `server` mirror (which derives the same extension from the server's own `solutionLanguage`).
  That is deliberate: git's similarity detection then does the right thing at both extremes. A
  Python solution replaced by a C++ one is dissimilar, so it surfaces as a structural add/delete
  conflict rather than a meaningless line-by-line merge of two languages; a C solution edited into a
  C++ one is similar enough to be tracked as a rename and carry server-side edits across.

- **`cg contribution push` does nothing when nothing has changed.** `--force` overrides; exit status
  stays 0 either way, since nothing needing doing is not a failure.

  `updateContribution` has no notion of an empty update -- it increments the version and re-runs
  moderation whether or not anything differs -- so republishing identical content costs a review
  cycle and buries the history of real changes behind no-op versions. Observed in the wild as two
  such pushes twelve seconds apart.

  "Nothing to push" compares the working tree against `server`'s tip tree, which is exactly what the
  last push or fetch recorded. That covers every file under `data/`, cover image included, without a
  hand-maintained list of fields to check, and it runs *before* the cover upload, so an unchanged
  cover is not uploaded only to discover there was no update to make.

- **Fixed: `cg puzzle diff` compared against the wrong language.** It asked `TestSession/startTestSession`
  for the current answer and diffed against that -- whatever language the session happened to hold.
  CodinGame stores a puzzle's code *per language*, so a local C++ file could be diffed against saved
  Python, producing a whole-file diff that meant nothing. It now reads
  `getPreviousCodeByLanguageId` for the language the working directory is actually in. `cg puzzle
  status`'s `local_dirty` is computed from the same comparison and was wrong in the same way.

- **Fixed: HTTP errors threw away the server's explanation.** A failed `cg contribution push`
  reported exactly `Error: CodingGame HTTP Error: 422 Unprocessable Entity` and nothing else.

  The response body was decoded and stored, but only *rendered* when it was a JSON object carrying a
  `"code"` key -- the one shape `CgClientErrorResponse` parses. A dict without `code`, a JSON array,
  a bare string, an HTML error page: all left the message as a bare status line with the explanation
  sitting unused in `content`. The body now reaches the message whatever its shape, whitespace
  collapsed onto one line and truncated at 600 characters with the true length reported, so a
  cut-off body cannot be mistaken for the whole story. Also corrects `CodingGame` to `CodinGame` in
  the message text.

- **One container image now carries every language, composed from dependency-ordered fragments.**
  Previously each language had its own image (`cg-cpp:<hash>`, `FROM gcc:14`) and its own container,
  so a workspace with two languages ran two containers, each bind-mounting the whole workspace. Now
  there is one `cg-toolchain:<hash>` and one container per workspace.

  The per-language design failed three ways. Two images can't both be `FROM`, so they could never
  compose. Putting the compiler version in an image tag hid it: we were on **gcc 14** while
  CodinGame runs **11.2.0**, which drifted two major releases without anything failing — C++20
  constructs compiled locally and were rejected on submission, the exact failure a pinned toolchain
  exists to prevent. And some languages need two conflicting toolchains at once: CodinGame runs Java
  on JDK 21.0.4 but Clojure, Groovy and Scala on JVM 1.8, keeping four JDKs installed side by side.

  So a fragment installs its toolchain under its own prefix and ships an activation script at
  `/opt/cg/env.d/<slug>.sh`; nothing goes on the global `PATH`. A *subsystem* fragment installs a
  toolchain (`gcc11`, `node20`, `jdk21`); a *language* fragment usually installs nothing and only
  declares a dependency plus flags, which is what lets C and C++, or JavaScript and TypeScript,
  share one install. Verified on the built image: `java`, `dotnet`, `node`, `npm` and `tsc` are
  absent from `PATH` entirely. This is the same shape CodinGame uses, arrived at independently.

  Fragments sort topologically with slug tiebreaks, so ordering is deterministic and a subset's
  Dockerfile is a literal *prefix* of a superset's — `C++` is 25 body lines, `C++`+`Python3` is 40
  with the same first 25 — which is what makes their images share layers. See
  [doc/design/toolchain-images.md](doc/design/toolchain-images.md).

- **C++ now compiles at `-O0` and links `-lm -lpthread -ldl -lcrypt`, matching CodinGame.** The
  build you *test* with used `-O2` (debug builds were already `-O0`), and the asymmetry ran the
  dangerous way: a solution fast enough locally could exceed the time limit on submission, having
  just told you it passed. There is deliberately no setting to change it — puzzles are designed to
  be solvable in every supported language, so limits are set by the slowest and C++ has orders of
  magnitude of headroom. It also means the build you time and the build you step through are the
  same one.

  This and the versions in every fragment were **measured** by running probe solutions on CodinGame
  rather than taken from their published table, which turned out to be wrong about every library
  version and silent on the optimization level. Their documented NumPy 1.20.2 is not merely stale
  but impossible: it publishes no wheels for Python 3.11. See
  [doc/design/codingame-runtime.md](doc/design/codingame-runtime.md).

- **New: `cg docker toolchain list`, `show` and `build`.** `list` shows which languages can go into
  an image and the subsystems beneath them; `show` prints the composed Dockerfile and its image tag
  without building or writing anything; `build` builds ahead of time.

  With no options `build` produces exactly the image a first run would build, under the same
  content-addressed tag, so the run finds it already there — it goes through the same code path
  rather than a parallel one that could disagree.

  `--platform` cross-builds via buildx. More than one platform requires `--push`: a multi-platform
  image is a manifest list, which `--load` cannot put in the local daemon. That's checked before
  Docker is invoked, because buildx's own failure for it is obscure and the fix isn't guessable.

- **New settings: `toolchainLanguages` and `toolchainImage`.** The default image carries all eight
  supported languages at about 1.9 GB — far less than the sum of its parts, since the large
  toolchains share one Debian base, which is why the default is everything rather than a curated
  subset. `toolchainLanguages: ["C++"]` narrows it to roughly 400 MB; `toolchainImage` skips
  building entirely in favour of a prebuilt tag. Both follow the usual global-config → project-config
  → `settings.json` merge.

- **`launch.json` never needs regenerating again.** The generated VS Code configuration used to be
  per working directory, so it went stale constantly: a `pickString` of every test case on disk
  (wrong the moment tests changed), an absolute `--puzzle-dir`, a container named after the
  directory. Switching puzzles, changing language, or importing anything meant re-running
  `cg … vscode`.

  Now there is **one configuration per language** for the whole workspace, and it contains nothing
  specific to any working directory. Both questions a debug launch has to answer moved to launch
  time: *which directory* from VS Code's `${file}`, and *which test* from that directory's
  `.meta/selected-test.json` (`cg puzzle select-test` / `cg contribution select-test`, defaulting to
  the first test — the first *local* one for a contribution).

  New kind-agnostic commands make that possible, each taking `--file` instead of a directory:
  `cg play`, `cg debug start`, `cg debug stop`, plus a `python -m codingame_tools.debug` entry point.
  They work out puzzle-vs-contribution from the file they are handed. The per-kind commands remain
  for naming a test explicitly.

- **`cg puzzle vscode` and `cg contribution vscode` are replaced by `cg vscode install`.** Once the
  generated configuration stopped depending on the kind of working directory — `CgVsCodeRequest` no
  longer even has a `kind` field — the two commands were provably identical for a given language,
  and keeping both implied a distinction that no longer existed.

  With no arguments it sets up *every* working directory it can find (the one you're standing in,
  plus the active puzzle and the active contribution) rather than making you pick, which is well
  defined now that entries are per language and languages are independent. `--file` limits it to
  one. `cg vscode` is a group so later integrations have somewhere to go.

  This is a breaking CLI change: the two old spellings are gone rather than deprecated.

- **Generated VS Code entries have a three-level name**: `CG C++: Debug solution` — a managed prefix
  stable across every version, then the language, then a well-known action.

  Each level does a job. The prefix makes every entry cg has *ever* written identifiable as a set,
  so an entry from a version whose naming we didn't anticipate can still be recognised and cleaned
  up rather than becoming permanent clutter in someone's `launch.json`. The language partitions
  ownership, so provisioning a C++ working directory can't disturb the Python entry in the same
  workspace — a real bug in the first cut of this, where whichever language you provisioned last was
  the only one that survived. The action comes from a fixed vocabulary, so re-provisioning replaces
  an entry instead of adding a second one.

  A provisioning run therefore replaces everything in its own language's namespace whatever it was
  called, and removes managed entries whose middle segment names no known language — which is how
  1.0.x's per-directory names (`CG puzzle: …`) and their orphaned `pickString` inputs are swept up
  without a dated special case per scheme. `retired_names` remains for the rarer change that moves
  an entry out of its namespace entirely.

- **`cg vscode install --check`** reports what would change and exits
  non-zero if anything would, without writing — so you can tell whether an upgrade changed the
  generated configuration, or gate it in a pre-commit hook. There is no version stamp to compare:
  the generated content *is* the version.

  Provisioning also no longer rewrites a file whose content wouldn't change, and replaces entries
  *in place* rather than moving them to the end. Re-running when everything is current is now a
  genuine no-op on disk: no diffs, no timestamps, no editor reload prompts.

- **C++ debugging no longer uses gdbserver; gdb launches the program itself.** Your program's
  stdout and stderr now appear in the Debug Console, like any ordinary debug session, and its stdin
  is the selected test case.

  gdbserver exists for targets that can't run gdb. Here gdb is already *on* the target — it has to
  be, since a macOS host can't debug a Linux binary — so gdbserver was a second debugger-side
  process in the same container, reached over a socket, to debug a program both could see. It cost
  the thing that matters: whoever `exec`s the program owns its descriptors, so the program's output
  went to gdbserver's terminal and never reached the editor, and its stdin had to be arranged
  separately. Worse, the adapter turned out to be launching its own inferior and ignoring our
  gdbserver entirely — which is why a solution reading input hung forever while a `cg debug start`
  from a shell read the same input every time.

  gdb now owns the process, `pipeTransport` still runs it in the container, and the test case is fed
  with gdb's own redirection. The program's `stderr` is merged into its `stdout` for the same
  reason the rest of this changed: the adapter reads only the debugger's stdout, so an unmerged
  `stderr` is dropped -- `cerr` diagnostics, precisely what you use while debugging, went missing
  while `cout` arrived. Merging also restores ordering between the two. This is the arrangement VS
  Code's Dev Containers support uses, which also has no gdbserver.

  Gone with it: the `postDebugTask`, the detached-server plumbing (`setsid`, and the
  `/build/gdbserver.log` your program's output used to disappear into), and the separate terminal
  the debuggee ran in. `cg debug start` is now a plain prepare-and-exit `preLaunchTask` that builds
  and stages the input.

- **C++ debug builds compile `data/solution.src`, not the `solution.<ext>` symlink**, and the launch
  configuration carries one `sourceFileMap` entry that shows you the symlink anyway.

  A debugger reports two paths per stop location -- the one in the debug info, and its own `realpath`
  of it -- and the editor navigates by the second. Compiling the symlink made them disagree, so
  breakpoints bound correctly and then jumped the editor to `data/solution.src`. The mapping that
  fixes navigation applies in both directions, so it then broke *binding*: the editor translated
  breakpoints back to the real path before sending them, the debug info named the symlink, and they
  went hollow. Compiling the real file makes both paths agree, which is what lets the mapping do its
  job. The mapping is written with `${fileDirname}`, so one configuration still serves every C++
  working directory.

- **Fixed: a C++ build could be silently reused when the compiled path changed.** The build stamp
  hashed the source's contents and the compiler flags, but not its path -- and the path is recorded
  in the debug info, so it is part of what the build *is*. Switching between two identical-content
  paths (the real file and the symlink pointing at it) hashed the same, left the old binary in place,
  and breakpoints silently failed to bind against its stale debug info.

- **Containerized languages mount the workspace at its own path.** `/home/me/work` on the host is
  `/home/me/work` inside the container, so the paths the compiler records in the debug info are
  already paths the host debugger can open — verified against real DWARF output. Previously the
  working directory was mounted at `/src` and the launch configuration carried a `sourceFileMap` to
  undo that, which only worked for the one directory it named. That mapping is gone; the one that
  remains exists for an unrelated reason, described above.

  A consequence worth knowing: containers are now per *workspace* rather than per (working directory
  × language), which is what lets a static configuration name one. A workspace pays for one
  container instead of one per puzzle per language. Containers from the old naming are orphaned
  rather than swept — `cg docker clean` removes them.

  Assumes host paths are valid Linux paths, which holds on macOS and Linux.

- **Generated files that moved are now cleaned up.** C++ wrote `devcontainer.json` at the working
  directory root through 1.0.x; it lives under `.meta/` now (generated, gitignored, not the user's to
  maintain), and the next `cg vscode install` deletes the old one. Only that file, and its directory only
  if deleting it leaves the directory empty.

- **A contribution's `.meta/` is now always at the working directory root, never inside `data/`.**
  `data/` is the git work tree, it's what gets pushed to CodinGame, and it's the only part worth
  backing up — generated, disposable, `repair()`-rebuildable state has no business in it.

  A single flag in `contribution.json` (`gitDirInData`) decides where the git repository goes, and
  it was moving `.meta/` along with it: a contribution created *outside* any git project got
  `data/.meta/`, with the repository at `data/.meta/.contribution-git/`. Only the repository should
  ever have moved. It now does, and its standalone location is a plain `data/.git`, so `data/` is an
  ordinary git working directory you can drive with bare `git` commands. Contributions created
  inside an existing git project are unaffected: still `.meta/.contribution-git/` at the root, still
  no `.git` marker anywhere for the outer project to trip over.

  This also removes a special case. With the repository at `data/.meta/`, every tree committed to
  the `server` branch had to carry a synthetic `.gitignore` protecting it, or a checkout landing on
  a commit without one, followed by `git clean -fd`, would delete the repository out from under
  itself. Git excludes its own `data/.git` inherently, so `server` is now a faithful mirror of
  contribution content and nothing else.

- **`contribution.json` now holds identity and nothing else.** The git-dir location moved out of it
  into a new `.meta/contribution-meta.json` (`gitRepo`, a root-relative path), because it is state
  this client chooses and maintains rather than a fact about the contribution.

  The rule behind it, now written down in the docs and the manager's module docstring:
  **`contribution.json` + `data/` are the *exportable* state.** Copy those two anywhere — another
  machine, an outer git repo, a backup — run `repair()`, and you get a consistent working directory.
  So they may hold only facts true of the contribution *wherever it is*; `.meta/` holds facts true of
  this checkout on this machine.

  The git-dir layout is the second kind, and putting it in `contribution.json` was actively wrong,
  not just untidy: exporting a standalone contribution into a colleague's repo carried
  `gitDirInData: true` along with it, so their copy would come up wanting an embedded `.git` inside
  their project. The destination now decides for itself.

  Nothing depends on the new file surviving, since a freshly exported directory has no `.meta/` at
  all: the recorded location is a cached answer, and `git_dir` falls back to finding an existing
  repository on disk, then to deriving from the local environment. That fallback also means deleting
  `.meta/` can never orphan a `data/.git` — which the old code, having recorded the fact durably,
  never had to worry about. A stale `gitDirInData` is dropped from `contribution.json` on the next
  write.

  **Migration:** working directories created standalone by 1.0.x are detected and refused with
  instructions rather than silently re-initialized — the new path doesn't exist there, so `repair()`
  would otherwise build a second, empty repository beside the real one. In short: review anything
  local you'd lose, `rm -rf data/.meta`, `cg contribution repair`. `data/` is never touched. Working
  directories created inside a git project need no migration.

## 1.0.5 (2026-08-05)

- **PyPI now has a `Documentation` link, and `Changelog` points somewhere that exists.** `Changelog`
  previously pointed at GitHub Releases, which is empty because this project doesn't publish
  releases; both now point at the release's own files, so the sidebar links on a given version's
  PyPI page describe *that* version.

  `bin/cut-rc` and `bin/cut-prod` pin them at release time, the way they already pinned `Source`.
  Pinning requires two conditions, so project-scoped links are never version-stamped: the key must
  be one PyPI treats as version-scoped (`Documentation`/`Changelog`), **and** the value must look
  like a GitHub `blob`/`tree` URL. `Homepage` and `Bug Tracker` are left alone even when they point
  at a `blob/main` URL, and a `Documentation` pointing at Read the Docs matches nothing and stays
  unpinned.

  Not switched to real GitHub Releases: a version's PyPI page wants *that version's* notes, and the
  `/releases` index isn't version-aware, so a pinned `CHANGELOG.md` is a better answer to the click.
  Creating releases is also a step that runs after the PyPI upload has already succeeded, so its
  failure mode is a half-published release. If they're added later, repoint `Changelog` at
  `/releases/tag/vX.Y.Z`, which beats both.

- **`bin/cut-prod` no longer requires a clean working tree, a particular branch, or a checkout of
  the rc.** Promotion is built entirely from the rc tag in a throwaway worktree — it never reads
  your working tree, your current branch, or `main` — so those gates blocked releases for reasons
  that cannot affect what ships. They existed only to protect the optional post-publish rebase of
  local `main`, which is now best-effort: it skips with a note if the tree is dirty or the rebase
  won't apply, and can never fail a release that has already been published.

  `CONTRIBUTING.md` updated to match: the "check out the rc tag first" flow is gone (`cut-prod`
  promotes `rc-latest` by default), as is the stale claim that `cut-rc` merely *warns* about an
  unfilled changelog, and `cut-rc`'s `--no-changelog` flag is now documented alongside `--force`.

- **`bin/cut-rc` refuses to cut a release candidate with no release notes**, since `cut-prod`
  promotes the rc snapshot verbatim and never consults `main` — notes written afterwards can never
  reach the release. Checked before anything is pushed or tagged, so a doomed run doesn't first burn
  an rc number and a TestPyPI publish. `--no-changelog` asserts there genuinely are none and records
  "No notable changes." rather than shipping the placeholder as the permanent record.

  `cut-prod`'s matching check now says the notes were needed before the *release candidate*, and
  that reaching it at all means `cut-rc`'s gate was bypassed.

## 1.0.3 (2026-08-05)

- No notable changes.

## 1.0.0 (2026-08-04)

- **`cg contribution create` now seeds every editable file**, not just the statement:
  `input_description.cgmd`, `output_description.cgmd`, `constraints.cgmd` and
  `stub_generator.cgstub` are written too, so an author can list, open and diff them instead of
  having to know which filenames to conjure. (`cover.png` is still absent — no sensible
  placeholder.)

  `data/cover.png` is seeded as well, with a deliberately garish 1920×1080 "UNDER CONSTRUCTION"
  image (traffic cones, hard hat, hazard stripes) — it's the one seeded placeholder that becomes
  *visible*, since `push` uploads whatever is there, and a tasteful title card would go unnoticed
  and get published. Shipped as package data rather than rendered at runtime, so no imaging
  dependency reaches users; `bin/gen-default-cover-image` regenerates it and owns the only Pillow use, as a
  dev dependency.

  The seeded content is deliberately self-consistent: statement, descriptions, constraints, stub
  generator, reference solution and test pair all describe the same trivial "read one integer,
  print it back" puzzle, so `cg contribution play` passes on a freshly created directory. The stub
  generator is the one seeded file that isn't inert — CodinGame runs it to produce the starter code
  every solver begins from — so `read n:int` is matched to the seeded one-line/one-integer test
  pair, and a test asserts the two keep agreeing.

- **Consistent `import`/`create` syntax.** All three now take the target **directory first, and
  require it**:

  | | before | now |
  |---|---|---|
  | `cg puzzle import` | `PUZZLE` (directory inferred) | `DIRECTORY PUZZLE` |
  | `cg contribution import` | `CONTRIBUTION-ID DIRECTORY` | `DIRECTORY CONTRIBUTION-ID` |
  | `cg contribution create` | `DIRECTORY [TITLE]` | unchanged |

  **Breaking**: `cg puzzle import temperatures` becomes `cg puzzle import ./puzzle temperatures`,
  and `cg contribution import` has its two arguments swapped.

- **Active working directories.** `cg puzzle import`, `cg contribution import` and
  `cg contribution create` now record what they just built as the *active* working directory
  (`currentPuzzleDir`/`currentContributionDir` in `settings.json`), and it outranks the configured
  `puzzleDir`/`contributionDir` default. Without it, setting a default and then importing somewhere
  else sent every following command to the configured directory instead — the one place you weren't
  looking.

  New `cg puzzle activate [DIRECTORY]` / `cg puzzle deactivate` (and the `cg contribution`
  equivalents) switch and clear it; `activate` defaults to the current directory and refuses one
  that isn't a working directory. `delete` deactivates too, but only when the directory being
  deleted is the active one.

  Deliberately not readable from `config.yaml`: it's state the app maintains, not a preference you
  declare, and a config file pinning it would defeat `activate`/`deactivate`.

- **`cg puzzle where` / `cg contribution where` now print only the resolved path**, so they compose:
  `$EDITOR "$(cg puzzle where)/solution.py"`. Explanatory text goes to stderr, and "not found" is a
  non-zero exit rather than a line of prose a shell would substitute into a path. **Breaking** for
  anything parsing the old `Puzzle directory: /path` format.

- The README now offers three documentation links rather than two: **this version** (pinned to the
  release tag), **the latest release** (`prod-latest`), and **in-development code** (the tip of
  `main`). The last is for anyone reading a published page who wants to see what's landed since.
  Both unpinned links are absolute and survive the release-time rewrite untouched.

- **Fixed: a promoted release's README linked back at the release candidate it came from.** 1.0.0
  shipped with every "documentation for this version" link pointing at `v1.0.0-rc.2`. `cut-prod`
  builds its release commit on top of the *rc commit*, whose README has already had its links
  rewritten and pinned by `cut-rc` — and the rewriter deliberately leaves absolute URLs alone, so
  re-running it changed nothing.

  `bin/cut-prod` now restores `README.md` from the commit the rc was cut from before patching, so
  the links are regenerated from the pristine source rather than re-derived from a previous
  derivation. Everything else it patches (`version=`, `Source=`, the badges) was already immune,
  because it replaces whole lines rather than transforming what it finds.

  Not fixed by re-pointing any URL that mentions this repo: the README also carries a deliberately
  unpinned link to the moving `prod-latest` tag. Regenerating from source is unambiguous; pattern
  matching would not be.

- **Removed the git-URL dependency on a private dataclass-wizard fork; the package is publishable
  again.** PyPI rejects any distribution whose metadata contains a direct URL dependency, so the
  `git+https://` pin could never be released — and publishing the fork under its own name would have
  meant owning a package on PyPI forever, since names can't be reclaimed and a yank doesn't help
  anyone who already pinned it.

  The fork turned out to be **two one-line changes**: dataclass-wizard 1.0.0's load/dump codegen
  does `field_to_aliases.pop(CATCH_ALL, None)` on a dict that *is* its shared per-class cache, so
  the first codegen pass permanently strips the marker and the class silently loses every unknown
  field in any later context (nested rather than top-level, say). `common/dataclass_wizard_x.py`
  now works around that against stock `dataclass-wizard==1.0.0` from PyPI, by installing a
  `pop`-resistant mapping under **our own classes' cache keys only**.

  Deliberately scoped rather than monkeypatching the library's functions, which would have been
  shorter and fixed the bug process-wide: this is a library, and silently altering dataclass-wizard's
  behaviour for every other package in an importing application isn't ours to decide.

  The pin is exact because the workaround uses private API. `tests/test_dataclass_wizard_catch_all.py`
  describes the *behaviour* rather than the workaround, so it keeps passing unchanged once upstream
  ships a fix — which is what will tell you the workaround can be deleted. Removal instructions are
  in the module docstring.

- **Documentation.** Everything beyond `README.md`/`CONTRIBUTING.md` now lives under `doc/`: an
  overview, concepts (authentication, profiles, working directories, languages), workflow guides for
  puzzles/contributions/debugging, the programmatic client and managers, and design notes recording
  the decisions that aren't obvious from the code.

  The 148-command CLI reference is **generated from the parser** by `pdm run gen-docs` (one page per
  command group, one per API service endpoint), and regeneration prunes pages whose commands were
  renamed or removed. It's committed — Python has no build step, so an uncommitted file wouldn't
  exist for anyone browsing GitHub — but treated as a *build artifact* rather than source: `main`'s
  copy may lag between releases and nothing in CI enforces otherwise. `bin/cut-rc-prep` regenerates
  it and pushes it to `main` as its own commit at the start of every `cut-rc`, so drift is bounded
  by one release cycle and the release tag inherits the same files. The cover placeholder is
  governed the opposite way (source, kept in sync by whoever edits its generator);
  `CONTRIBUTING.md` documents both, and the new `bin/cut-rc-prep` hook contract that keeps
  `bin/cut-rc` itself project-agnostic and reusable.

  Two offline tests keep the *hand-written* docs honest: every `cg ...` must resolve against the
  real parser, and every relative link must point at a file that exists. Command renames are the
  drift that actually happens here — `cg puzzle push` became `cg puzzle submit`, `revert` became
  `discard-local` — and each would otherwise have silently invalidated every guide mentioning it.

  `CHANGELOG.md` deliberately stays at the repo root: `bin/cut-rc` and `bin/cut-prod` rewrite and
  `git add` it by path.

  **README links now survive PyPI.** PyPI renders `README.md` as the project front page but resolves
  relative links against `pypi.org`, so every `[docs](doc/...)` would 404 there. `bin/cut-rc` and
  `bin/cut-prod` now rewrite them to absolute URLs pinned to the release tag, in the same throwaway
  worktree they already patch `pyproject.toml`/`README.md`/`CHANGELOG.md` in. `main` keeps ordinary
  relative links (checked by the test suite); the tagged commit the package is built from — the one
  PyPI displays — gets fully-qualified ones, pointing at the docs as they were for *that* version.
  A second, deliberately unpinned link to the moving `prod-latest` tag gives readers of an old
  version's page a route to current docs.

- **`outputs_match` no longer accepts output CodinGame rejects.** It previously normalized away
  per-line trailing whitespace, and (via `splitlines`) CRLF line endings — both of which the server
  treats as failures. That's the dangerous direction: a solution passed locally and then failed on
  submission. Its rule is now equivalence with the server's: everything compared exactly, except a
  difference of **one** trailing newline in either direction.

  Mapped live against `CgPlayResult.comparison.success` rather than guessed, across two puzzles
  (one whose stored expected output ends in a newline, one whose doesn't — the rule only falls out
  of seeing both):

  | actual, relative to the stored expected output | server |
  |---|---|
  | verbatim | pass |
  | ± one trailing newline | pass |
  | ± two trailing newlines | fail |
  | trailing whitespace added to every line | fail |
  | per-line trailing whitespace stripped | fail |
  | leading space added to every line | fail |
  | CRLF line endings | fail |
  | a leading blank line | fail |

  The one-newline allowance isn't optional: a test's expected output usually has no final newline
  (it was typed into a textarea) while every language's `print` supplies one. The tolerance is a
  *difference*, not a cap — `expected + "\n\n"` fails even when the expected value itself ends in a
  newline.

- **Fixed: the debugger fed contribution solutions one extra byte of stdin.** `cg contribution
  debug` bound the test-case file directly to stdin, while `cg contribution play` goes through
  `list_local_test_cases`, which decodes — so the terminator this client adds reached the solution
  only under the debugger, and the expected-output comparison ran against a window shifted by one
  newline. `run_debug_stdin` now takes `final_newline_added`, which the contribution wrapper passes
  and the puzzle wrapper deliberately doesn't (`.meta/tests/` holds byte-exact downloads).

  The same one-byte deviation existed on the attach-style (C++/gdbserver) path, which redirected the
  container's stdin straight from the test-case file. **Breaking, for `CgLanguage` implementors:**
  `start_debug_session` now takes `stdin_text: str` rather than `input_file: Path`, and the
  implementation materializes its own copy (`<meta_dir>/debug-stdin`) to redirect from. Redirecting
  from the caller's file cannot be made correct — the file is the *rendering* of a value for a
  contribution and the value itself for a puzzle — so the caller, which is the only party that knows
  which, now supplies the bytes. This also drops the old requirement that the input file live inside
  the working directory, along with the error path for when it didn't.

  Confirmed against the real thing: CodinGame's runner feeds stored test input **verbatim** and
  appends nothing — a probe reading `sys.stdin.buffer.read()` on a community puzzle whose stored
  input is the single unterminated byte `"7"` reported `bytes=1 repr=b'7'`. An unterminated final
  line of stdin is real, and solutions have to handle it. (It's a community-contribution
  phenomenon, incidentally: official CodinGame puzzles' test files are properly terminated.)

- **Fixed: `cg contribution` required a configured git identity, and said so obscurely.** Every
  command touching `.meta/`'s git repository failed with `Author identity unknown` for anyone who
  had never run `git config --global user.email` — and on CI, where a runner's hostname has no
  domain (`runner@fv-az123.(none)`), git's usual `user@host` auto-detection can't rescue it either.

  `git commit` now falls back to a synthetic `codingame-tools <codingame-tools@localhost>` identity
  **only when git can't resolve one of its own**, probed once per repository via `git var`. The
  conditional part matters: `-c` outranks every config file, so applying it unconditionally would
  stamp that name over the user's real one in the `git log` they read while resolving a merge
  conflict. `.meta/`'s commits are local scaffolding that is never pushed anywhere, so the identity
  on them carries no meaning — it just has to exist.

- Schema fixes for fields that are **omitted entirely** (not null) in real responses, all found by
  decoding the whole pending community-review queue rather than one report at a time:
  - `CgTopic` — only `label_map` is guaranteed. A topic can arrive as nothing but its localized
    label (e.g. `{"labelMap": {"2": "Logic Gates"}}`) with every catalogue field absent; `id`,
    `handle`, `category`, `puzzle_count` and `parent_topic_id` are now optional. Observed on 10 of
    80 topic objects.
  - `CgContribution.avatar` / `CgPersonalContribution.avatar` — now optional, for codingamers who
    never set an avatar (3 of 54 contributions). `CgPendingContribution` already allowed this;
    the three classes had simply drifted.
  - `CgLastActivityPuzzle.cover_binary_id` — now optional (absent for 7 of 30 puzzles from a single
    `Puzzle/findProgressByIds` call).

- New `cg contribution set-language LANGUAGE [--force]` — deliberately **stricter** than the puzzle
  equivalent, because a contribution stores exactly one solution with no per-language history.
  There is nothing to restore and nothing to switch back to: the existing solution is replaced by a
  starter stub, and the next `cg contribution push` overwrites the last durable copy. So it refuses
  unless `data/solution.src` is still exactly the stub cg generated (recorded in
  `.meta/solution-snapshot.json` by `create()` and by each switch). Notably, *matching what the
  server currently has* does **not** count as safe here — unlike `cg puzzle set-language`, where
  per-language recall makes switching reversible — since the server copy is precisely what the next
  push destroys. Purely local: no network call, as there's no per-language code to fetch.

  Only Python3 offers a create-stub that genuinely passes the seeded test cases, so switching to
  any other language leaves `data/solution.src` **empty** rather than writing a placeholder. That's
  required, not a shortfall: `updateContribution` skips solution validation entirely when
  `solutionSource` is null, but validates any non-null one against every test case — and `create()`
  seeds a real test/validator pair. Python3's stub echoes its input specifically so it passes them;
  a comment-only placeholder for another language would be non-null, fail validation, and block
  `push()`.

- **Fixed: local text no longer erodes a newline on every fetch/push cycle.** Server-side text and
  the local files holding it are now converted through one place, `codingame_tools.common.
  text_files`, unconditionally in both directions — append a terminator on the way in (unless the
  value is zero-length), strip up to one on the way out. That makes the pair exact inverses, so an
  untouched import-then-push submits byte-identical text.

  Previously the terminator was appended only when missing, which cannot be inverted: the reader
  can't tell whether the newline it sees belonged to the content or to the writer. Composed with the
  strip applied at submission, any value that genuinely ended in a newline lost one per cycle, with
  no user edit, until it ran out. Surveying the pending community-review queue and published
  community puzzles (1686 real values) shows why that mattered more than its 0.8% rate suggests:
  the trailing-newline habit is per-*author*, so this eroded **every** test case of roughly 1 in 12
  contributions, not the occasional stray one. All 1686 now round trip exactly, and keep doing so
  across repeated cycles.

  **Breaking, for `cg api` callers:** `strip_test_final_eols` is gone from
  `CgContributionServiceHelper.update_contribution`/`create_contribution`, along with the
  `--no-strip-test-final-eols` flags on `cg api helper contribution update-contribution`/
  `create-contribution`. The service layer no longer rewrites submitted data at all — normalization
  belongs with the file conversion, not half of it at the transport layer. Callers building a
  `CgContributionData` by hand now control their own text exactly.

  Puzzle test cases under `.meta/tests/` are deliberately untouched by this: they're byte-exact
  `fileservlet` downloads, read-only and never pushed, so the bytes on disk are already the bytes
  CodinGame feeds a solution's stdin remotely. A contribution's test cases need the conversion for
  the opposite reason — there the server holds a *string* and the file is this client's rendering
  of it.

- An **empty `data/solution.src` now means "no reference solution"** and is pushed as a null
  `solutionSource` — anything that decodes to the empty string, i.e. a zero-length file or one
  holding just a terminator. That second case is the single point where the conversion above isn't
  injective, and it lands usefully here: an editor with "insert final newline" enabled can't quietly
  turn "no reference solution" into a one-blank-line program. Nothing weaker qualifies — a
  whitespace-only file stays a real (broken) program that the server will reject rather than being
  silently reinterpreted as no solution. This
  replaces deleting the file: `create()` and `set-language` now always leave a `solution.src`
  present, so the `solution.<ext>` symlink resolves instead of dangling and there's a file to type
  into straight away. It conflates a server-side solution that is genuinely the empty string with a
  null one — accepted deliberately, since an empty program passes no test cases and so could never
  have been an accepted solution.

- **You can now switch a puzzle's language, and get your own saved code back.** CodinGame keeps
  your most recent source *per language* for a puzzle, not just one; a previously-unknown API
  (`TestSession/getPreviousCodeByLanguageId`, now wrapped as
  `CgTestSessionService.get_previous_code_by_language_id` and `cg api test-session
  get-previous-code-by-language-id`) reaches the languages the session isn't currently on.

  New `cg puzzle set-language LANGUAGE [--force]` switches `data/solution.src`,
  `data/puzzle-data.json` and the `solution.<ext>` symlink, seeding the file with whatever you'd
  previously written in that language — a placeholder only when you've genuinely never used it
  there. It refuses when the current file holds work the server doesn't have (switching would
  discard it); `--force` overrides.

  "Has the user edited this?" is answered from a **recorded snapshot** of what cg last wrote
  (`.meta/solution-snapshot.json`, written by every path that touches `solution.src`), not by
  regenerating a placeholder and comparing. Regeneration would break silently the moment
  placeholder output stopped being byte-identical across releases — a template tweak or an embedded
  timestamp would be enough — and an untouched working directory would start claiming unsaved
  changes. A missing snapshot (fresh clone, or a directory from an older version) falls back to
  comparing against the server, which errs toward refusing rather than discarding. Comparisons
  ignore a trailing newline, which the server's copy routinely differs by.

  `cg puzzle import --language` changes meaning to match: it now *switches to* that language
  (restoring saved code for it) instead of being silently ignored whenever any answer existed.
  Omit it to get whichever language you last used, as before. `import --language X` is now exactly
  `import` followed by `set-language X`, sharing one code path.

  Two API semantics confirmed live and documented, since both are easy to assume wrongly: fetching
  code for a language is a **pure read** that does *not* make it the session's current language
  (only running a test or submitting does), and a language you've never attempted returns **null**
  rather than a generated stub.

- Fix: **official CodinGame puzzles couldn't be imported at all.** A puzzle the site provides
  itself was never a community contribution, so `TestSession/startTestSession` omits `contributor`
  and `contribution` **entirely** (not null) — and both were required fields, so `cg puzzle import
  Temperatures` failed to parse the response outright. The same omission broke
  `LastActivities/getLastActivities` via `CgLastActivityPuzzle.contributor`, which is what
  `communityCreation: false` marks. All three are now optional. A puzzle's `contributionType` is
  consequently unknowable for an official puzzle, so `import_` treats its absence as a standard
  in/out puzzle and records `puzzleType: null` rather than refusing — failing closed there would
  have blocked every official puzzle on the site. A type that *is* present and unsupported is still
  rejected as before.

- Containers are now strictly **one per working directory**. Container names are per-language, so
  changing a working directory's `solution_language` previously orphaned the old language's
  container--still running, still bind-mounted, never referenced again. Creating a container now
  sweeps away any other one bound to the same directory first, and `cg puzzle delete` /
  `cg contribution delete` already removed theirs (matched by label, so every language's is caught).
  Toolchain state is decided entirely from labels read back off Docker (`cg.root`/`cg.spec` on
  containers, `cg.managed` on images) rather than from any cache beside them, so removing a
  container or image out-of-band--`docker rm`, Docker Desktop, `cg docker clean`--just works and cg
  rebuilds on the next command. Speed comes from asking once instead of remembering: the common
  path is a single `docker ps` that answers existence, spec match, running state, and strays
  together, and a container that passes vouches for its image still existing. `cg puzzle play` over
  20 C++ test cases went from 2.6s to 1.7s.

- New `cg docker clean`: remove every container and image cg created, across all working
  directories. Deliberately never prompts and has no `--force`--a container holds nothing but build
  artifacts and an image is rebuilt from Dockerfiles on disk, so there is no user work in either and
  the next build recreates whatever is needed. Useful for reclaiming disk space or forcing a clean
  rebuild. Images are now labelled `cg.managed` when built, so they're identified by label rather
  than guessed from tag names (an unrelated image that happens to be called `cg-*` is never
  touched); containers are found by the `cg.root` label they already carry, which also catches ones
  for working directories that no longer exist.

- **Real C++ debugging in VS Code, with no local toolchain and no local debugger.** `cg puzzle
  vscode` now generates a `cppdbg` configuration that breaks at the first statement, binds
  breakpoints to the file you actually have open, and feeds the solution a test case's input on
  stdin. The host needs nothing but Docker.

  Both `gdbserver` and `gdb` run inside the container--VS Code reaches gdb through `pipeTransport`
  shelling out to `docker exec`, and gdb then dials the container's own localhost, so no port is
  published. Feeding stdin from a file is why a debug session is set up by `cg puzzle debug start`
  rather than left to the debug adapter: doing the redirection in a shell we control sidesteps
  cppdbg's stdin handling entirely. The debug profile compiles the `solution.<ext>` symlink
  specifically so the path recorded in the debug info maps back to the file in your editor, and
  `sourceFileMap` uses an absolute host path because the workspace root is usually a *parent* of the
  working directory. A `devcontainer.json` is generated too, for IntelliSense over the container's
  headers--optional, and not on the run/debug path.

  New: `cg puzzle debug start|stop` and `cg contribution debug start|stop` (normally invoked by the
  generated tasks, not by hand). Containers now run with `--init` and the ptrace allowances gdb
  needs; the drift check that decides whether to reuse a container covers all creation flags, not
  just the image, so changes like these recreate it automatically.

- **C++ solutions now build and run, entirely inside Docker--no local toolchain required.** The
  first non-Python language to go beyond a file extension. `cg puzzle play` / `cg contribution play`
  work on a C++ solution exactly as they do on a Python one, and `cg puzzle build` /
  `cg contribution build` compile without running.

  How it works: one long-lived container per (working directory x language), with the working
  directory bind-mounted **read-only** at `/src` and build artifacts living at `/build/` inside the
  container--so the solution source stays the only durable state outside it. Rebuilds are skipped
  when the source is unchanged (hashing the source file alone, never a tree, since `/src` contains a
  contribution's whole git object database); failed builds are cached too, so a repeat replays the
  same diagnostics instantly rather than recompiling. Runs enforce their timeout *inside* the
  container, because killing the local `docker exec` client doesn't stop the process in it, and
  force unbuffered output so a C++ solution streams progressively like Python3 does.

  The toolchain image is defined by two files under `<cg data dir>/docker/<lang>/`: a cg-owned
  `base.dockerfile` carrying a template version, and a `custom.dockerfile` that is **yours, appended
  verbatim, and never touched by cg**. That split is what makes upgrades safe--adding a library is
  purely additive, so cg can ship a new base without ever needing to merge with your edits (an
  unmodified stale base upgrades silently; an edited one is left alone with a warning). Image tags
  are content-addressed, so any change to either file rebuilds automatically, and a per-working-
  directory `.meta/docker/<lang>/` overrides the global files when one puzzle needs something
  different.

  Deleting a working directory now also removes its containers--orphaning one would matter, not just
  be untidy, since container names derive from the directory path and a future working directory at
  the same path would otherwise silently attach to the stale container and its stale artifacts.
  (`CgPuzzleManager.delete()` is `async` as a result.)

  Docker-requiring tests are marked `docker` and excluded by default; run them with
  `pdm run pytest -m docker`.

- New `cg puzzle vscode` / `cg contribution vscode`: generate this working directory's VS Code
  run/debug configuration instead of hand-maintaining it. The test-case dropdown is built from the
  test cases actually on disk, so it can't go stale--the hand-written configuration this replaces
  carried a note telling you to regenerate its 25-entry list by hand after every `cg puzzle import`.
  Languages describe what they need via the new `CgLanguage.build_vscode_provisioning`; where it
  goes and how it merges is `codingame_tools.language.vscode`'s job. Three things worth knowing:
  configuration is written to the **workspace root**'s `.vscode/` (VS Code never reads `launch.json`
  from a subdirectory, and a working directory is usually a subdirectory of the real workspace),
  with `--workspace-dir` to override; re-running replaces only the entries owned by that working
  directory, so your own configurations and other working directories' configurations survive; and
  since `launch.json` is really JSONC, a file with comments is **refused rather than rewritten**
  (`--force` overrides). This repo's own `.vscode/launch.json` is now generated.

- Groundwork for Docker-backed language toolchains: building a solution is now an explicit step
  separate from running it. `CgLanguage` gains `build()` (returning a `CgBuildResult`--never
  raising, since a compile error is a routine outcome to display, not a crash) and takes a new
  `CgLanguageContext` (working-directory root, solution file, `solution.<ext>` symlink, meta dir,
  toolchain dir) in place of a bare solution path. Both managers gain `language_context()` and
  `build_solution()`; `play_local()`/`run_local_tests()` build once before looping, while
  `play_local_one()`/`run_local_test()` deliberately do not build. `cg puzzle play` and
  `cg contribution play` build up front and report a build failure as such, instead of letting a
  compile error surface once per test case (or, for `cg puzzle play`, as a traceback), and both
  gain `--timeout` / `--build-timeout` (the build budget is far more generous, since a cold build
  may pull a container image and compile from scratch). No behavior change for Python3, which needs
  no build. `CgContributionManager` also gains a public, never-raising `meta_dir` property--unlike
  `git_dir`, it works on a directory that was never imported, which `language_context()` requires.

- New `codingame_tools.language` package: centralizes all per-language behavior (local execution,
  file extension, comment syntax, contribution-create starter stub) behind a single `CgLanguage`
  abstract interface, discovered by walking `language/languages/`'s flat modules at load time
  (`get_language()`/`get_language_by_extension()`/`list_language_cg_ids()`). Every
  CodinGame-supported language has its own real module (27 total, one file each--e.g.
  `languages/python3.py`, `languages/java.py`)--`Python3` is the only one with local
  execution/stub generation implemented so far; the other 26 currently implement only
  `extension`. `CgDefaultLanguage` is now a pure catch-all, used only for a `cg_id` CodinGame
  might add in the future that this client has never seen.

  Local execution is now actually async local execution, not just command-building:
  `CgLanguage.run_streaming()` runs a solution as a subprocess and yields its stdout/stderr
  progressively, chunk by chunk, as they're produced (tagged by stream--stdout and stderr are
  two independent, separately-buffered OS pipes, and this deliberately doesn't attempt to
  guarantee "correct" interleaving between them, only real-time delivery of each); `run()` is a
  convenience wrapper for a caller that just wants the final aggregated result. Contribution
  starter-stub generation (`build_contribution_create_stub_source()`) is likewise now an async
  method a plugin builds, not a static property.

  Replaces `test_runner.runner`'s `run_solution_locally`/`CgLocalRunResult`/
  `CgLocalRunUnsupportedLanguageError` (both managers' single-test methods are now `async def`
  and call `codingame_tools.language` directly; `CgLanguageOperationNotSupportedError` propagates
  with no manager-specific translation wrapper) and `client.common.protocol.schema`'s
  `cg_extension_to_solution_language`/`cg_solution_language_to_extension` (removed outright).
  Fixes a bug along the way: `cg puzzle import`'s placeholder stub for a puzzle with no existing
  answer used to emit an unconditional `# TODO: ...` line regardless of language, which is
  invalid syntax for any language whose single-line comments aren't `#`-prefixed--it now uses the
  language's own comment syntax where known, or an empty file otherwise, rather than guessing
  wrong.

- Fix `cg puzzle play` (the local one) missing the final `N/M passed` summary line that `cg
  puzzle play-server` already had--lost when it was restructured to stream results one at a time.
  Also added the same summary line to `cg contribution play`, which never had one.

- `cg puzzle play` (the local one) gets the same streaming treatment as `cg puzzle play-server`:
  displays each test's result as it finishes instead of running the whole batch first.
  `CgPuzzleManager.play_local()` is split the same way `play()` was: `resolve_play_local_test_cases()`
  (sync, resolves the given/default test case list) and `play_local_one()` (runs a single
  downloaded test case, never raising just because it failed)--`play_local()` itself is now just
  a loop over those two, kept as a convenience for callers that want the whole batch (and still
  raises `CgPuzzleLocalTestFailedError` if any failed, as before). `cg contribution play` already
  worked this way (it was never restructured to batch-collect-then-display), so no change was
  needed there.

- `cg puzzle play-server` now displays each test's result as soon as it's available, instead of
  running every requested test first and only then printing anything--a multi-test run no longer
  looks stalled while the server works through earlier tests. `CgPuzzleManager.play()`'s single
  batch call is split into two pieces a caller can use directly for this: `resolve_play_indices()`
  (sync, resolves the given/default index list, no network) and `play_one()` (one `TestSession/
  play` call for a single index)--`play()` itself is now just `[await play_one(i) for i in
  resolve_play_indices(...)]`, kept as a convenience for callers that do want the whole batch.

- **Breaking**: renamed CLI play commands for consistency--the entirely-local, no-network variant
  is now plain `play` in both working-directory types, freeing up the old `play`/`play-local`
  names' asymmetry:
  - `cg puzzle play-local` -> `cg puzzle play`
  - `cg puzzle play` (the real server-side `TestSession/play` call) -> `cg puzzle play-server`
  - `cg contribution play-local` -> `cg contribution play` (unchanged behavior/output--
    contribution has no server-side "play" equivalent, so no swap was needed there)

- `cg contribution play-local`'s output now matches `cg puzzle play-local`'s format: a single
  colored `[PASS]`/`[FAIL] ordinal side: title` line per test (folding in the ordinal/side/title
  that used to be a separate `=== ... ===` announcement before each run), printed as each test
  finishes rather than deferred to a separate `=== Summary ===` section at the end (removed). On
  failure, shows a diff (via the same `show_diff` helper, instead of an unconditional raw dump)
  for a genuine output mismatch, or the exception/timeout/crash reason otherwise, then `---
  stderr ---` (also now colored) if there's any. `--show-stdout`/`--update-expected` still print
  the raw captured output as before.
- `cg puzzle play-local` now accepts one or more 1-based TEST-INDEX arguments (previously just
  one, or none for "all downloaded")--matching `cg puzzle play`'s already-normalized argument
  style. `CgPuzzleManager.play_local()`'s signature changed to match: `test_indices: list[int] |
  None = None`, run in the order given.

- Fix `cg puzzle play`/`play-local` and `cg contribution play-local` running captured stdout
  straight into whatever gets printed next (the following test's `[PASS]`/`[FAIL]` header, or the
  shell prompt) when the program under test didn't itself end its output with a newline--`print(
  ..., end="")` preserved the output byte-for-byte but assumed it already ended in `\n`, which
  isn't guaranteed. New `_print_captured_output()` helper prints the captured text verbatim and
  then guarantees exactly one trailing newline regardless.

- `cg puzzle play`, `cg puzzle play-local`, and `cg contribution play-local` no longer dump a
  passing test's captured stdout by default--only a failing (or errored) test's output is shown,
  same as before. Pass `--show-stdout` to always print it regardless of pass/fail.
  `cg contribution play-local --update-expected` implies `--show-stdout` (the point of that flag
  is to review the new output being accepted as the baseline).
- `cg puzzle play`'s output now matches `cg puzzle play-local`'s format: `[PASS]`/`[FAIL] test N
  (label)` per test (bold blue, like the section headers below it) instead of the old `--- test N
  ---`/`success: True`/`expected:`/`found:` lines. On failure, shows a unified diff (via the same
  `show_diff` play-local uses) when the server's comparison data has both `expected`/`found`, then
  the raw combined output under a `--- output ---` header (bold blue)--the closest remote analog
  to play-local's `--- stderr ---` section. `cg puzzle play-local`'s own `[PASS]`/`[FAIL]`/
  `--- stderr ---` lines are now colored the same way (previously plain).

- **Breaking**: collapsed the never-built sync/async client split. `codingame_tools.client.sync`
  (an empty placeholder) is deleted; `codingame_tools.client.async_` is flattened up to
  `codingame_tools.client` (e.g. `codingame_tools.client.async_.client` -> `codingame_tools.
  client.client`, `...async_.service...` -> `...client.service...`); every `CgAsync*` class drops
  the `Async` infix (`CgAsyncClient` -> `CgClient`, `CgAsyncRawClient` -> `CgRawClient`,
  `CgAsyncContributionService` -> `CgContributionService`, etc.--51 classes total across the raw
  client, the top-level facade, all 19 service/service-helper pairs, and both servlet/
  servlet-helper pairs). `CgRawClient`/`CgClientHttpError` (previously abstract-ish bases in
  `client/common/raw_client.py`, split from their concrete `CgAsync*` counterparts purely to leave
  room for a future sync HTTP backend) are merged into single concrete classes--`set_cookie` is no
  longer `abstractmethod`. Also fixes a pre-existing gap where `client/service/services/__init__.py`
  never imported/exported the `vote` service pair. No behavior change otherwise; every consumer
  (CLI, contribution/puzzle managers, tests) updated to the new names/paths.

- Fix `cg puzzle submit` crashing on a puzzle with no prior submission: `CgSubmissionReport.
  best_score` was assumed always-present (based on one earlier partial-report example) but
  confirmed live absent too when there's no historical "best" yet--now Optional like every other
  field except `validator_shareable`, the only one confirmed present in every case seen so far.
  Also fixes a real `dataclass_wizard` CatchAll mis-binding introduced by that same edit
  (`extra_data` wasn't the first defaulted field, corrupting `best_score` into `{}` instead of
  `None`)--see this project's established "extra_data must be first among defaulted fields" rule.
- Rename `cg puzzle push` to `cg puzzle submit` (`CgPuzzleManager.push()` -> `.submit()`), unlike
  `cg contribution push`'s git vocabulary--confirmed live (2026-08-01) that a puzzle working
  directory has two independent server-side persistence phases, not one: the test session's
  current answer (durably updated by *any* `TestSession/play` call, not just a real submission--
  see below) and this method's actual graded submission via `TestSession/submit`. "Push" would
  ambiguously suggest either; "submit" (matching the underlying API method's own name) is
  unambiguous.
- Document (in `CgPuzzleManager.play()`'s docstring) a confirmed-live side effect of
  `TestSession/play`: the server durably persists whatever code was sent as the test session's
  current answer--the same answer visible in the web IDE from any browser--even though `play()`
  itself is not a grading/submission event. There's no separate "just save" API; running at
  least one test case is, in effect, this project's puzzle-working-directory autosave.
- Fix `cg config init`'s freshly-created project-local `config.yaml` showing an absolute
  `#dataDir:` example (resolved for the specific `--at` directory at creation time)--if the
  project directory is later renamed or moved, that absolute path would silently stop matching
  the real default. Now shows the literal relative `"../data"` instead, which keeps meaning "the
  sibling data dir next to wherever this config file actually lives" regardless. `--global` is
  unaffected--still shows the actual resolved absolute path, since there's no comparable sibling
  relationship to express relatively for the global (per-user) location. `default_config_template()`
  now takes the example value as a plain string rather than a `Path`, so the caller can pass
  either form.
- Fix `cg config dump` (and `default_profile`/`contribution_dir`/`puzzle_dir` resolution
  generally) silently masking the global (per-user) `config.yaml` whenever a project-local one
  existed, even if the project file never mentioned the field in question--previously
  `resolve_config()`'s single-file "first found wins" discovery meant a project config missing
  `defaultProfile` entirely still shadowed the global file's `defaultProfile`, discarding it. Fix:
  `CgConfigData` gains a `settings` sub-object (`CgConfigData.settings`), identical in shape to
  settings.json's own `CgSettingsData` (`defaultProfile`/`contributionDir`/`puzzleDir`), resolved
  field-by-field, base to most refined: the global config file's `settings`, then a project
  config file's own `settings` (if a different one resolved), then settings.json itself. Config
  files remain hand-edited only (no `cg config set`--only `cg settings set` exists, and only ever
  touches settings.json). `CgConfigData`'s old top-level `default_profile` field is removed
  (moved into `settings.defaultProfile`)--an existing config.yaml using the old top-level key
  needs a one-time manual edit to nest it under `settings:` (`cg config dump`/`cg config where`
  won't do this automatically; the field just silently stops being read otherwise, landing in
  `extra_data` instead). `CgConfig` gains `.settings`/`.contribution_dir`/`.puzzle_dir` alongside
  the existing `.default_profile`. `cg config dump`'s output nests `defaultProfile`/
  `contributionDir`/`puzzleDir` under a `"settings"` key (mirroring `CgConfigData.settings`'s own
  shape) rather than flattening them onto the config object directly--only `dataDir` (which isn't
  part of the merge--see below) stays top-level alongside `configFile`/`rawConfig`.
  `contribution_dir`/`puzzle_dir` previously had no config.yaml-level fallback at all (settings.json
  or nothing)--they now participate in the same 3-tier chain as
  `default_profile`.
- Fix `contribution_dir`/`puzzle_dir` (settings.json and config.yaml's `settings` alike) resolving
  a relative value against the *current working directory at read time* instead of a fixed base--
  meaning the effective directory silently moved around depending on where `cg` happened to be run
  from. `cg settings set contribution-dir`/`puzzle-dir` now converts whatever path was typed
  (resolved against cwd *at set time*, the natural way to type a path at the CLI) into one stored
  relative to settings.json's own directory (`CgSettings.settings_file.parent`, i.e. `data_dir`);
  absolute input is stored as-is. Reading back (`CgSettings.contribution_dir`/`puzzle_dir`,
  `CgConfig.contribution_dir`/`puzzle_dir`) now resolves a relative value against that same
  `data_dir`, never cwd. New `codingame_tools.settings.resolve_settings_dir`/
  `relativize_settings_dir` implement this pair. The real project `.cg/data/settings.json` here
  had old-style values (`"contribution"`/`"puzzle"`, implicitly relative to the project root) that
  would have started resolving to `.cg/data/contribution`/`.cg/data/puzzle` under the new rule--
  re-set via `cg settings set contribution-dir ./contribution`/`puzzle-dir ./puzzle` to fix (now
  stored as `"../../contribution"`/`"../../puzzle"`, correctly relative to `.cg/data`).
- Add `cg contribution status`: a human-friendly summary of a contribution's submission,
  review/approval, and server-sync status (`--refresh` to fetch fresh first, top-level `--json`
  for machine-readable output).
- Add the `Vote` service (`client.services.vote.find_votable_values_by_id`, `cg api vote
  find-votable-values-by-id`): CodinGame's generic community up/down-vote tally for a votable
  (e.g. a contribution's `votableId`).
- Add `Contribution/findContributionModerators` (`client.services.contribution.
  find_contribution_moderators`, `cg api contribution find-contribution-moderators`): the
  privileged moderator approve/reject gate that actually decides a PENDING contribution's
  outcome (3 `"validate"`/`"deny"` votes either way)--distinct from the ungated community vote
  above. `cg contribution status` now shows it (`Approvals`/`Rejections`, with named moderators).
- Add `.meta/contribution-status.json` (`CgContributionStatusCache`): an offline cache of every
  piece of server metadata that isn't tied to a content version (score/votes/comment count/
  views/moderator approve-reject tallies/etc.), refreshed unconditionally by
  `CgContributionManager.fetch()`/`import_()`/`repair()` on every real `findContribution` call
  (even when the content version hasn't changed, since none of this is tied to it)--not
  git-tracked. `cg contribution status` reads it by default (no network access); `--refresh`
  forces a fetch first, which also updates the cache for next time.
- Add `Contribution/getPersonalContributions` (`client.services.contribution.
  get_personal_contributions`, `cg api contribution get-personal-contributions`): every
  contribution (any status) authored by a codingamer--unlike `get_all_pending_contributions`,
  genuinely filtered to just that codingamer's own.
- Add `cg contributions`: a one-line-per-contribution listing (handle/id/status/type/title) of
  all pending contributions community-wide by default, or just your own with `--personal`;
  top-level `--json` for the raw underlying list.
- Add `cg puzzle status`: a human-friendly summary of a puzzle working directory (title, pretty
  id, puzzle type, difficulty, language, local-edit status vs. the server's last-submitted
  answer). By default entirely local (no network access, unlike `cg contribution status` there
  is no local cache to refresh either); `--refresh` also checks for local edits and fetches live
  progress/score (level/solved/score/solved-by/attempts/XP/last activity). Top-level `--json`
  for machine-readable output.
- Add `puzzle_type`/`difficulty` to `.meta/puzzle-server-data.json` (cached at `import_()`/
  `repair()` time, alongside `title`/`puzzle_pretty_id`)--`None` for a cache written by an older
  version until the next `cg puzzle repair`. Add `difficulty` to `cg contribution status` too
  (`local_difficulty`, from `data/contribution-data.json`, same as `puzzle_type`/language).
- Add `cg status`: a session-wide summary (login status, profile, points/rank stats, achievement
  count)--always hits the network (no cached/local mode, unlike the other `status` commands).
  Top-level `--json` for machine-readable output (`rankHistory`--thousands of dated snapshots--
  trimmed out, not appropriate for a status summary). Points/rank/per-category numbers are
  grouped under one "Gamer stats" label (informational only, not a breakdown of one
  another--rationale lives in `CgCodingamePointsRankingDto`'s docstring, not printed every run).
  `XP` shows progress toward the next level (e.g. `34019   (1855/2250 to level 37)`), derived
  from the already-fetched per-level `xp_thresholds` table--no separate formula/lookup needed.
- Correct `CgCodingamePointsRankingDto`/`CgCodingamePointsStats` docstrings: two previously
  documented "duplicate"/"sum of categories" relationships between `codingamer_points`,
  `codingame_points_total`, and the seven `codingame_points_*` category fields are disproven by
  live data (e.g. category fields summed to 43469 against a `codingame_points_total` of 2800).
- Add `cg puzzle delete`: removes the local puzzle working directory only--there is no
  server-side counterpart, since a puzzle already exists on the server before you can solve it.
  Destructive--prompts for confirmation unless `--force` is given; requires `--force` outright
  if stdin/stdout aren't a terminal (same pattern as `cg contribution delete`).
- `cg puzzle import` now accepts a general puzzle reference instead of requiring an exact pretty
  ID--tries, in order: a numeric puzzle ID, an exact pretty ID, an exact-matching title, a
  case-insensitive-matching title (the latter two via `Search/search`). `CgPuzzleManager.
  import_()`'s parameter is renamed `puzzle_ref` to match.
- Fix `cg puzzle import` crashing on puzzles whose TestSession has no recorded submission yet
  (found live via the new title-search path): `CgLastActivityContributor.pseudo`,
  `CgTestSessionQuestion.last_submission_id`, `CgTestSessionQuestionSummary.score` are now all
  correctly Optional (each confirmed live to be entirely absent, not just `null`, in that case),
  and `CgTestSessionAnswer`'s `code`/`programming_language_id` are Optional too--`answer` itself
  can be present as an empty placeholder object rather than `null`/absent when nothing's been
  submitted, which `puzzle_manager` now checks for correctly instead of mistaking it for a real
  saved answer.
- Add `cg puzzle description`: renders the cached `.meta/statement.html` (no network access) as
  readable text--section headers and the Example's input/output test data are color-highlighted
  when writing to a real terminal (auto-detected, via `rich`), plain elsewhere (piped/redirected
  output, or `--json`, which emits the parsed `[{kind, text}, ...]` blocks instead). New
  `codingame_tools.puzzle_manager.statement_render` module (`parse_statement_html`,
  `CgStatementBlock`)--a small purpose-built parser for CodinGame's specific statement HTML
  shape (confirmed live), not a general HTML-to-text converter.
- `cg puzzle play` now runs every downloaded test case (`.meta/tests/`) by default instead of
  just test 1, or one or more explicit 1-based test indices given as positional arguments (e.g.
  `cg puzzle play 2 4`--need not be locally downloaded, the server runs by index alone). Exits 1
  if any run errored or didn't match the expected output. `CgPuzzleManager.play()`'s signature
  changed to match: `test_indices: list[int] | None = None`, returning
  `list[CgPuzzleRemoteTestResult]` (index/label/result) instead of a single `CgPlayResult`.
- `cg puzzle push` now calls `Report/findReportBySubmission` right after submitting and prints a
  summary (score/best score, achievements-completed, per-validator pass/fail), instead of just
  the bare new submission ID. `CgPuzzleManager.push()`'s return type changed to match:
  `CgSubmissionReport` (also now re-exported from `codingame_tools.puzzle_manager`) instead of
  `int`--its `.submission_id` is the same numeric ID `TestSession/submit` itself returns. `--json`
  prints the raw report.
- Fix `cg puzzle push` crashing on a fresh submission: confirmed live that
  `findReportBySubmission` called immediately after `TestSession/submit` can race server-side
  grading--every `CgSubmissionReport` field but `best_score`/`validator_shareable` was entirely
  absent in one observed case, not just `null`. Those fields are now all Optional, with a new
  `CgSubmissionReport.is_ready()` and `CgAsyncReportServiceHelper.find_report_by_submission_when_ready`
  (`client.services.report.helper...`)--polls every 3s, up to 60s by default--that `push()` now
  uses instead of the plain `find_report_by_submission`. `find_report_by_submission_when_ready`
  also takes an optional async `on_poll` callback, awaited with each not-yet-ready report--
  currently no real progress info to report (the API gives no partial/percentage signal we've
  found), but it doubles as a cancellation hook: raise from it (or from an `await` inside it) to
  abort the wait before `max_wait_seconds`.
- `CgAsyncContributionServiceHelper.update_contribution`'s existing HTTP-524 retry/polling (for
  contributions whose test-suite re-validation is slow enough that Cloudflare's edge disconnects
  the request) gets the same `on_poll` pattern: an optional async callback awaited with each
  still-stale `CgContribution` observed while polling `find_contribution` after a 524--unlike the
  Report helper's, this one always carries real (if stale) data. Never called if no 524 occurs;
  same cancellation-hook behavior (raise to abort early).
