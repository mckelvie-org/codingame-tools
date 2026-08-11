# What CodinGame actually runs

The toolchain images `cg` builds exist to make a local run predict a submission. That only works if
the versions and flags match, so they were **measured** rather than taken from
[CodinGame's published versions page](https://www.codingame.com/playgrounds/40701/help-center/languages-versions)
— which turned out to be wrong about every library version and silent on the thing that matters most.

Measured 2026-08-07 by running probe solutions on CodinGame and reading their stderr back (see
[reproducing](#reproducing-this)).

## C and C++

| | Published | Measured |
| --- | --- | --- |
| Compiler | gcc 11.2.0 | `__VERSION__` = **11.2.0** ✓ |
| Standard | C++20 / C17 | `__cplusplus` = **202002** ✓ |
| libstdc++ | — | `_GLIBCXX_RELEASE` = **11** |
| glibc | — | **2.36**, compile-time *and* run-time |
| Optimization | *not stated* | **`__OPTIMIZE__` undefined, `__NO_INLINE__` defined → `-O0`** |
| Architecture | — | **x86_64** |
| Link libraries | `-lm -lpthread -ldl -lcrypt` | (as published) |

**The optimization level is the important one.** CodinGame compiles at `-O0`, and `cg` previously
used `-O2`. That asymmetry runs the dangerous way: a solution fast enough locally at `-O2` can exceed
the time limit on submission, having just told you it passed. `cg` now uses `-O0` too, and
deliberately offers no setting to change it — puzzles are designed to be solvable in every supported
language, so time limits are set by the slowest of them and C++ has orders of magnitude of headroom
regardless. It also makes single-stepping faithful: at `-O0` the code you step through is the code
you wrote.

## Python3

| | Published | Measured |
| --- | --- | --- |
| Python | 3.11.5 | **3.11.5** ✓ (built with GCC 13.2.0) |
| NumPy | 1.20.2 | **1.23.2** |
| pandas | 1.2.4 | **1.4.2** |
| SciPy | 1.6.3 | **1.9.3** |

The published NumPy isn't merely stale, it's **impossible**: 1.20.2 publishes wheels for cp37–cp39
only and predates 3.11's C-API, so that pairing cannot exist. Pinning the documented versions would
have failed the image build for something nothing runs.

## The platform itself

```
hostname:  codemachine-ovh-cg-7
kernel:    Linux 5.4.0-216-generic x86_64      (8 CPUs)
HOME:      /tmp
limits:    Max processes 200, Max cpu time unlimited
PATH:      /bin:/usr/bin:/usr/GNUstep/Local/Tools:/usr/GNUstep/System/Tools:/opt/coderunner/groovy/bin
```

Two things follow from that `PATH`.

**CodinGame installs each toolchain under its own prefix** — `/opt/coderunner/<language>/`,
`/usr/GNUstep` for Objective-C — and puts only the relevant one on `PATH`. `java`, `node` and
`dotnet` are absent from `/usr/bin` entirely. That is the same shape `cg`'s fragments use, arrived at
independently: it's what lets Java's JDK 21 and Scala's JVM 1.8 coexist in one image without either
owning the global environment.

**Compilation and execution share one environment.** Compile-time and run-time glibc both report
2.36, and `g++` is present at run time — so there is no separate build container to account for.

### A caveat on the base image

`/etc/os-release` reports Debian 11 (bullseye), whose glibc is 2.31 — but both the compile-time
headers and the run-time loader report **2.36**, which is Debian 12 (bookworm). The label is
misleading, and the measured glibc is what `cg`'s base image is chosen to match.

## What cg's image actually delivers

Measured on a real 8-language build (1.89 GB), against what CodinGame runs:

| | CodinGame | cg's toolchain | |
| --- | --- | --- | --- |
| Java | 21.0.4 | 21.0.4 | exact |
| C# | SDK 8.0.401 / runtime 8.0.8 | SDK 8.0.401 | exact |
| JavaScript | Node 20.9.0 | Node 20.9.0 | exact |
| TypeScript | 5.6.2 on Node 20.9.0 | 5.6.2 on Node 20.9.0 | exact |
| C / C++ | gcc 11.2.0, `-O0` | gcc 11.3.0, `-O0` | patch level |
| Python | 3.11.5 | 3.11.2 | patch level |
| — NumPy / SciPy | 1.23.2 / 1.9.3 | 1.23.2 / 1.9.3 | exact |
| — pandas | 1.4.2 | 1.5.0 | see below |
| Bash | 5.1.16 | 5.2.15 | minor version |
| Architecture | x86_64 | host's (arm64 on Apple Silicon) | see below |

Four exact, four close. The gaps and why they are accepted:

- **gcc and Python patch levels** come from Debian bookworm. Matching exactly would mean building
  gcc or CPython from source on every image build, for a patch release.
- **pandas 1.4.2 cannot be installed on Python 3.11** — it publishes no cp311 wheel. CodinGame
  evidently compiles it from source; we pin 1.5.0, the earliest with a wheel. Discovered by the
  image build failing, not by reading anything.
- **Bash 5.2.15 vs 5.1.16** is bookworm's shell. Pinning an older bash would mean building a shell
  from source.
- **Architecture** follows the host, so an Apple Silicon machine builds arm64 rather than running
  everything under emulation. `--platform` can produce x86_64 when it matters.

### Isolation, verified on the built image

`java`, `javac`, `dotnet`, `node`, `npm` and `tsc` are **absent from the global `PATH`** — reachable
only by sourcing their activation script. That is what allows two conflicting toolchains in one image,
and it is the property CodinGame itself relies on to run four JDKs. The apt-installed toolchains
(`gcc-11`, `python3`, `bash`) do appear on `PATH`, but are never ambiguous: gcc is version-suffixed,
and activation prepends the Python virtualenv so `python3` resolves to the right interpreter.

## Reproducing this

The probes are ordinary solutions that write to stderr, which CodinGame returns in a run's `output`:

```bash
cg --profile dev puzzle import /tmp/probe temperatures --language C++
# ...write a probe into /tmp/probe/data/solution.cpp...
cg --profile dev puzzle --puzzle-dir /tmp/probe play-server --show-stdout 1
```

A C or C++ probe needs no subprocess: `__VERSION__`, `__cplusplus`, `_GLIBCXX_RELEASE`, `__GLIBC__`,
`__OPTIMIZE__` and `__NO_INLINE__` all come from the preprocessor, and `uname()`, `/etc/os-release`
and `/proc/self/limits` cover the rest. This works for languages `cg` cannot yet run locally, so a
new language's versions can be measured *before* its fragment is written.

> **Use a throwaway profile.** Running a test case **autosaves your code server-side** for that
> (puzzle, language) — confirmed by experiment. A probe run against your own account overwrites
> whatever solution you had saved there. Either use a separate profile, or pick a language slot where
> `getPreviousCodeByLanguageId` returns `null` (a pure read) so there is nothing to lose.
