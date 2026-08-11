"""`CgCppLanguage`: `CgLanguage` for CodinGame's "C++", compiled and run inside Docker so nothing
   has to be installed locally--see `codingame_tools.language.registry` for the discovery contract
   (`LANGUAGE` below) that finds it.
"""

from __future__ import annotations

import json
import platform
import shlex
from collections.abc import AsyncGenerator
from pathlib import Path

from .._docker import (
    BUILD_DIR,
    CgDockerError,
    CgToolchain,
    container_name_for,
    docker_exec_argv,
    ensure_toolchain,
    latest_alias_for,
)
from .._process import run_argv_capture, run_argv_streaming
from ..base import (
    DEFAULT_BUILD_TIMEOUT_SECONDS,
    DEFAULT_RUN_TIMEOUT_SECONDS,
    CgBuildProfile,
    CgBuildResult,
    CgDebugSession,
    CgLanguage,
    CgLanguageContext,
    CgRunEvent,
)
from ..toolchain.fragment import ENV_DIR, CgToolchainFragment
from ..vscode import (
    ACTION_DEBUG,
    ACTION_PREPARE_DEBUG,
    PRESENTATION,
    CgVsCodeProvisioning,
    CgVsCodeRequest,
    entry_name,
)

__all__ = [
    "CgCppLanguage",
    "LANGUAGE",
]

LANG_SLUG = "cpp"

CACHED_MARKER = "cg-build:cached"
COMPILED_MARKER = "cg-build:compiled"
"""Machine markers the build script writes to **stdout** (diagnostics go to stderr) so the caller
   can tell a cached no-op from a real compile. Necessary because a clean compile emits no
   diagnostics at all, making it otherwise indistinguishable from the cached path."""

def build_script(source: str, profile: CgBuildProfile) -> str:
    """Shell to compile `source` (a path inside the container) into `/build/<profile>/solution`,
       skipping the work entirely when nothing relevant changed.

       Hashes the source file's **contents**, its **path**, and the compiler flags--never a
       directory tree. The working directory contains a git object database and `tests/`, both of
       which churn constantly and would cause endless spurious rebuilds.

       The path belongs in the hash because g++ records it in the debug info, so it is part of what
       the build *is*, not merely how it was made. The **toolchain identity** belongs there for the
       same reason: the compiler and its flags now come from the image's activation script rather
       than from this file, so switching images -- or editing `custom.dockerfile` -- must invalidate
       artifacts compiled by the previous one. Omitting it caused a real staleness bug: switching
       which of two identical-content paths gets compiled (`data/solution.src` versus the
       `solution.<ext>` symlink pointing at it) left the previous binary in place, still carrying the
       old path in its DWARF, and breakpoints silently failed to bind.

       Caches failures as well as successes: rebuilding known-bad source replays the saved
       diagnostics instead of recompiling, so a repeat is cheap and says exactly the same thing.

       Compiler diagnostics go to **stderr**; stdout carries only a `CACHED_MARKER`/`COMPILED_MARKER`
       machine marker. They have to be separable because a clean compile with no warnings says
       nothing at all, which would otherwise be indistinguishable from the cached fast path."""
    flags = "$CG_CXXFLAGS_DEBUG" if profile == "debug" else "$CG_CXXFLAGS"
    out = f"{BUILD_DIR}/{LANG_SLUG}/{profile}"
    src = shlex.quote(source)
    return f"""
set -u
. {ENV_DIR}/{LANG_SLUG}.sh
mkdir -p {out}
if [ ! -f {src} ]; then
    echo "no solution source at {source}" >&2
    exit 2
fi
TOOLCHAIN="$CG_CXX|$CG_CXXFLAGS|$CG_CXXFLAGS_DEBUG|$CG_CXXLIBS"
HASH="$(sha256sum {src} | cut -d' ' -f1)-$(printf '%s' "{flags}|{source}|$TOOLCHAIN" | sha256sum | cut -d' ' -f1)"
if [ "$(cat {out}/ok 2>/dev/null)" = "$HASH" ]; then
    echo {CACHED_MARKER}
    exit 0
fi
if [ "$(cat {out}/fail 2>/dev/null)" = "$HASH" ]; then
    echo {CACHED_MARKER}
    cat {out}/log >&2
    exit 1
fi
echo {COMPILED_MARKER}
if "$CG_CXX" {flags} -x c++ -o {out}/solution {src} $CG_CXXLIBS >{out}/log 2>&1; then
    printf '%s' "$HASH" >{out}/ok
    rm -f {out}/fail
    cat {out}/log >&2
    exit 0
fi
printf '%s' "$HASH" >{out}/fail
rm -f {out}/ok
cat {out}/log >&2
exit 1
"""


def run_script(timeout: float) -> str:
    """Shell to exec the built binary.

       `timeout` runs **inside** the container because killing the local `docker exec` client does
       not terminate the process inside it--an infinite-looping solution would otherwise survive its
       timeout and keep burning CPU, with runs piling up in a long-lived container. It's set one
       second *beyond* the caller's timeout on purpose: the outer timeout should win the race, so a
       runaway is reported as a clean `timed_out=True` rather than as an opaque exit code 124. This
       is the backstop that guarantees cleanup, not the primary mechanism.

       `stdbuf -o0 -e0` because a C++ binary on a pipe is fully block-buffered, so a solution
       printing a few lines would emit nothing until exit--exactly the problem the Python3 plugin
       solves with `-u`/`PYTHONUNBUFFERED=1`. Without it `run_streaming` would stream nothing."""
    binary = f"{BUILD_DIR}/{LANG_SLUG}/run/solution"
    return f"""
set -u
if [ ! -x {binary} ]; then
    echo "solution is not built--run \\`cg puzzle build\\` (or \\`cg contribution build\\`) first" >&2
    exit 2
fi
exec timeout -k 1 {int(timeout) + 1} stdbuf -o0 -e0 {binary}
"""



DEBUG_STDIN_FILE_NAME = "debug-stdin"
"""Name of the file `start_debug_session` writes into the working directory's `.meta/` to redirect
   the debugged program's stdin from. A copy rather than the test case's own file, so that exactly
   the bytes the caller specified reach the program--see `start_debug_session`."""

DEBUG_STDIN_CONTAINER_PATH = f"{BUILD_DIR}/debug-stdin"
"""Where the selected test case's input is staged for the debugged program to read.

   A **fixed** path inside the container, not the working directory's own `.meta/debug-stdin`,
   because the launch configuration names it (`set args < ...`) and must stay identical for every
   working directory in the workspace--see `codingame_tools.language.vscode`. Safe because a
   container hosts one debug session at a time.

   `start_debug_session` copies the real input here; the file the user's working directory holds is
   still the source of truth."""

_SETUP_COMMANDS = [
        # gdb runs the program under this, and stdbuf's LD_PRELOAD makes the *program's* stdout
        # unbuffered. Without it libstdc++ block-buffers a stdout that isn't a terminal--and here it
        # never is--so nothing the program prints appears until it exits. std::cerr is unbuffered
        # regardless; this is what fixes std::cout.
        {"text": "set exec-wrapper stdbuf -o0 -e0",
         "description": "unbuffer the program's output", "ignoreFailures": True},
        # Two redirections, both applied by gdb's startup shell.
        #
        # `< input` is the whole reason a debug session needs preparing at all: the program must read
        # the test case, not the terminal.
        #
        # `2>&1` is what makes the program's stderr visible. The debug adapter reads gdb's *stdout* --
        # that is the MI channel -- and the program inherits both of gdb's streams, so its stdout
        # already arrives in the Debug Console while its stderr went to gdb's stderr, which the
        # adapter drops on the floor. Observed exactly that: `result` appeared and the `cerr`
        # diagnostics either side of it did not. Merging also restores ordering between the two,
        # since they then share one descriptor.
        {"text": f"set args < {DEBUG_STDIN_CONTAINER_PATH} 2>&1",
         "description": "feed the test case to stdin; surface stderr in the Debug Console"},
        # Silences a warning and gets readable std::string/vector in the locals pane.
        {"text": "set auto-load safe-path /",
         "description": "allow libstdc++ pretty-printers", "ignoreFailures": True},
        {"text": "-enable-pretty-printing",
         "description": "enable pretty printing", "ignoreFailures": True},
    ]
"""What gdb is told before the program starts.

   Sent by the adapter as `-interpreter-exec console` before `-exec-run`, and verified to survive
   that route: driving gdb over MI by hand with exactly these, `-exec-run` hit the breakpoint and
   the program read its input."""

_ADAPTER_LOGGING = {
        "engineLogging": True,
        "trace": True,
        "traceResponse": True,
        # Every shared-library load, otherwise--dozens of lines that bury the exchange being read.
        "moduleLoad": False,
    }
"""cppdbg's `logging` block, emitted only under `CgVsCodeRequest.debug_adapter_logging`.

   Puts the full MI conversation in the Debug Console: every command the adapter sends gdb and every
   response back. The adapter is the one component of this stack that cannot be exercised from a
   terminal--gdb, the build, stdin redirection and stepping can all be driven by hand and checked."""

_BUILD_PROBLEM_MATCHER = {
        "owner": "cg-cpp",
        # Absolute, because that is what we hand the compiler (see source_path_in_container), and
        # inside the container it is the same absolute path as on the host.
        "fileLocation": "absolute",
        # gcc diagnostics and nothing else. A catch-all pattern is a trap: every line the task prints
        # becomes a "problem", and VS Code then refuses to launch with "errors exist after
        # preLaunchTask"--observed exactly that, reporting an ordinary progress line as an error.
        "pattern": [{
            "regexp": r"^(.*?):(\d+):(\d+):\s+(warning|error):\s+(.*)$",
            "file": 1, "line": 2, "column": 3, "severity": 4, "message": 5,
        }],
    }
"""Turns a failed debug build into clickable entries in the Problems panel, and stops the launch.

   No `background` section any more: the task prepares and exits, so VS Code simply waits for it.
   That it needed one--and a readiness pattern to go with it--was an artifact of the task having to
   leave a debug server running behind it."""


_TARGET_ARCHITECTURES = {
        "arm64": "arm64", "aarch64": "arm64",
        "x86_64": "x64", "amd64": "x64", "AMD64": "x64",
    }
"""`platform.machine()` -> the spelling cppdbg wants for `targetArchitecture`."""


def target_architecture() -> str | None:
    """What to tell the debug adapter the debuggee's architecture is, or `None` if unrecognized.

       Without it cppdbg warns "Debuggee TargetArchitecture not detected, assuming x86_64" and does
       exactly that--wrong on any Apple Silicon or ARM host, where it silently misreads the
       disassembly and register views. Breakpoints, stepping and variables are unaffected, which is
       what makes it easy to miss.

       Derived from the *host* architecture rather than by asking the container. Strictly the
       container is the authority--under QEMU emulation a deliberately foreign image would make this
       wrong--but asking it would make the generated configuration depend on whether Docker happened
       to be running, so provisioning would emit different output at different times and
       `cg vscode install --check` would flap between them. A configuration file should be a function
       of the project, not of daemon state.

       The host is a sound proxy in every non-emulated case, because cg builds its image from a
       multi-arch base with no `--platform`, so the container matches the host. An unrecognized host
       yields `None`, leaving cppdbg to its own detection rather than asserting something false."""
    return _TARGET_ARCHITECTURES.get(platform.machine())


_TASK_PRESENTATION = {
        "reveal": "silent",
        "panel": "dedicated",
        "close": True,
        "showReuseMessage": False,
    }
"""The build task has nothing to show unless it fails, and `reveal: silent` shows it exactly then.
   `close` spares a terminal sitting on "Press any key to close".

   Note this is no longer the program's console: gdb owns the program now, so its output goes to the
   Debug Console like any ordinary debug session."""


def _devcontainer_json(root: Path) -> str:
    """A `devcontainer.json` for "Reopen in Container".

       Purely a convenience for IntelliSense over the container's own headers--none of the run or
       debug functionality needs it, since those drive the container from the host. That's what
       makes it safe to keep under `.meta/` (gitignored, generated, not the user's to maintain)
       even though VS Code won't discover it there on its own: point the Dev Containers extension
       at it explicitly if you want it. It references
       the **already-built image by tag** rather than a `dockerFile` path, which sidesteps pointing
       at Dockerfiles that live outside the folder (they're per-user and global by default).

       Note VS Code mounts the folder at `/workspaces/<name>` here, not at `/src`--harmless for
       IntelliSense, but it's why the generated cppdbg configuration is the host-side one and not
       an in-container variant."""
    content = {
            "name": f"CG {root.name} (C++)",
            "image": latest_alias_for(),
            "customizations": {"vscode": {"extensions": ["ms-vscode.cpptools"]}},
            "runArgs": list(_DEVCONTAINER_RUN_ARGS),
        }
    return json.dumps(content, indent=2) + "\n"


_DEVCONTAINER_RUN_ARGS = ("--cap-add=SYS_PTRACE", "--security-opt", "seccomp=unconfined")
"""Same ptrace allowances the cg-managed container gets--so debugging also works if the user does
   reopen the folder in the dev container."""


class CgCppLanguage(CgLanguage):
    """C++ (CodinGame's `cg_id` "C++"), compiled and run in a container so no local toolchain is
       needed. See `codingame_tools.language._docker` for the container/image model."""

    def __init__(self) -> None:
        super().__init__("C++")

    @property
    def extension(self) -> str:
        return "cpp"

    @property
    def comment_prefix(self) -> str:
        return "//"

    def source_path_in_container(self, ctx: CgLanguageContext, profile: CgBuildProfile) -> str:
        """Which path inside the container to compile: the solution file itself, the same for every
           profile.

           There is only one path to choose from now, which is the point. When a `solution.<ext>`
           symlink sat over a fixed `data/solution.src` this had to pick one, and both choices were
           wrong in different ways: gdb reports *two* paths per stop location--`file` from the DWARF
           and `fullname`, its own `realpath` of it--and the editor navigates by `fullname`.
           Compiling the symlink made them disagree, so a breakpoint bound and then yanked the editor
           to the target; adding a `sourceFileMap` to fix the navigation broke the *binding* instead,
           since it applied in both directions. One real file carrying its language's own extension
           makes the two paths identical and deletes the problem rather than balancing it.

           The host path *is* the in-container path--the mount root is bind-mounted at its own
           location (see `codingame_tools.language._docker`)--so there is nothing to translate here.

           `-x c++` is kept although the file is now named `solution.cpp`: it costs nothing, and it
           still compiles a working directory an older cg left holding `data/solution.src`, whose
           extension g++ doesn't recognize and would treat as a
           as a linker input ("file format not recognized")."""
        return str(ctx.solution_file)

    async def _toolchain(self, ctx: CgLanguageContext, *, timeout: float) -> CgToolchain:
        return await ensure_toolchain(
                root=ctx.mount_root, meta_dir=ctx.meta_dir, toolchain_dir=ctx.toolchain_dir,
                languages=ctx.toolchain_languages, image=ctx.toolchain_image, timeout=timeout,
            )

    async def build(
                self,
                ctx: CgLanguageContext,
                *,
                profile: CgBuildProfile = "run",
                timeout: float = DEFAULT_BUILD_TIMEOUT_SECONDS,
            ) -> CgBuildResult:
        """Compile the solution inside the container, bringing the image and container up first if
           needed. Near-free when the source hasn't changed since the last successful build.

           Compiler diagnostics come back in the result rather than as an exception--a compile error
           is a routine thing to display, not a crash. A Docker problem (no daemon, image build
           failure) is reported the same way, so a caller never has to catch anything here."""
        try:
            toolchain = await self._toolchain(ctx, timeout=timeout)
        except CgDockerError as e:
            return CgBuildResult(ok=False, output=str(e), up_to_date=False)

        result = await run_argv_capture(
                docker_exec_argv(
                    toolchain.container_name,
                    build_script(self.source_path_in_container(ctx, profile), profile)),
                timeout=timeout,
            )
        if result.timed_out:
            return CgBuildResult(
                    ok=False, up_to_date=False,
                    output=f"compiling timed out after {timeout}s (raise --build-timeout if the "
                           "first build is simply slow)",
                )
        compiler_output = result.stderr.strip()
        output = "\n".join([*toolchain.warnings, compiler_output]).strip()
        return CgBuildResult(
                ok=result.ok, output=output,
                up_to_date=result.ok and CACHED_MARKER in result.stdout,
            )

    async def run_streaming(
                self,
                ctx: CgLanguageContext,
                input_text: str,
                *,
                timeout: float = DEFAULT_RUN_TIMEOUT_SECONDS,
            ) -> AsyncGenerator[CgRunEvent, None]:
        """Run the already-built binary in the container, streaming its output.

           Does **not** build--that's a separate step (see `CgLanguage.build`). It does ensure the
           container is up, since losing the container also loses the artifacts that live inside it;
           if the binary is missing, the run fails with a message saying to build first."""
        toolchain = await self._toolchain(ctx, timeout=DEFAULT_BUILD_TIMEOUT_SECONDS)
        argv = docker_exec_argv(toolchain.container_name, run_script(timeout), interactive=True)
        async for event in run_argv_streaming(argv, input_text, timeout=timeout):
            yield event

    async def start_debug_session(
                self,
                ctx: CgLanguageContext,
                stdin_text: str,
                *,
                timeout: float = DEFAULT_BUILD_TIMEOUT_SECONDS,
            ) -> CgDebugSession:
        """Build the debug profile and stage `stdin_text` where the debugged program will read it.

           Despite the name this **starts nothing**. gdb launches the program itself, the way it does
           for any ordinary local target--see this module's docstring for why there is no gdbserver
           in the picture. All that is needed beforehand is a current debug build and the input in
           place, so this is a `preLaunchTask` that prepares and exits.

           `stdin_text` is copied rather than the test case's own file being used directly. That is
           not incidental: a contribution's test-case file carries a final newline this client added
           (see `common.text_files`), and reading from it would put one extra byte on stdin--
           diverging from `cg contribution play` and from CodinGame, which appends nothing. Copying
           also drops the requirement that the caller's file live inside the working directory.

           It lands at `DEBUG_STDIN_CONTAINER_PATH`, a fixed path inside the container, so the launch
           configuration that names it stays identical for every working directory. The route is a
           `cp` inside the container rather than a `docker cp`, because the workspace is already
           bind-mounted at its own absolute path--the file cg just wrote on the host is visible there
           under the same name.
        """
        build_result = await self.build(ctx, profile="debug", timeout=timeout)
        if not build_result.ok:
            return CgDebugSession(ok=False, output=build_result.output)
        stdin_file = ctx.meta_dir / DEBUG_STDIN_FILE_NAME
        stdin_file.parent.mkdir(parents=True, exist_ok=True)
        stdin_file.write_text(stdin_text, encoding="utf-8")
        toolchain = await self._toolchain(ctx, timeout=timeout)
        staged = await run_argv_capture(
                docker_exec_argv(
                    toolchain.container_name,
                    f"cp {shlex.quote(str(stdin_file))} {DEBUG_STDIN_CONTAINER_PATH}"),
                timeout=60.0,
            )
        if not staged.ok:
            return CgDebugSession(
                    ok=False,
                    output=(staged.combined.strip() or "failed to stage the test case input"))
        return CgDebugSession(
                ok=True, output=build_result.output,
                details={
                    "container": toolchain.container_name,
                    "program": f"{BUILD_DIR}/{LANG_SLUG}/debug/solution",
                    "stdin": DEBUG_STDIN_CONTAINER_PATH,
                },
            )

    async def stop_debug_session(self, ctx: CgLanguageContext) -> None:
        """Nothing to tear down: gdb owns the debugged process, so it dies with the debug session.

           Kept as an explicit no-op rather than removed, because the base class declares it and a
           language whose debugger *does* leave something running still needs it. It also means
           `cg debug stop` stays safe to run at any time."""
        return

    @property
    def toolchain_fragment(self) -> CgToolchainFragment:
        """Installs nothing: C++ is entirely supplied by the shared `gcc11` subsystem, which C also
           depends on, so an image containing both carries one compiler rather than two.

           The flags live here rather than in the image because they are cg's business, not the
           toolchain's -- changing a warning flag should not require rebuilding a multi-gigabyte
           image. `CG_CXXLIBS` is separate from `CG_CXXFLAGS` because link libraries must follow the
           translation unit on the command line, not precede it.

           **The flags are measured, not guessed.** A probe run on CodinGame reports `__OPTIMIZE__`
           *undefined* and `__NO_INLINE__` defined, so the platform compiles at **-O0** -- while cg
           previously used `-O2`. That asymmetry is the dangerous direction: an O(n^2) solution fast
           enough locally at -O2 can exceed the time limit on submission, and the local run would
           have said it was fine. Matching means the local run predicts the remote one, which is the
           only reason to pin a toolchain at all. See doc/design/codingame-runtime.md.

           `-O0` explicitly rather than by omission, and deliberately **not** configurable. Optimizing
           past CodinGame buys nothing: puzzles are designed to be solvable in every supported
           language, so the time limits are set by the slowest of them and a C++ solution has orders
           of magnitude of headroom either way. It also makes single-stepping faithful -- at -O0 the
           code you step through is the code you wrote, with nothing reordered or inlined away.

           **`-lm -lpthread -ldl -lcrypt` matches what CodinGame links**, which cg previously omitted
           entirely -- so a solution using `pthread_create` or `dlopen` linked remotely and failed
           locally, or worse the reverse."""
        return CgToolchainFragment(
                slug="cpp",
                version=2,
                depends_on=("gcc11",),
                env_script=(
                    'export CG_CXXFLAGS="-std=c++20 -O0 -g -Wall -Wextra"\n'
                    'export CG_CXXFLAGS_DEBUG="-std=c++20 -O0 -g3 -Wall -Wextra"\n'
                    'export CG_CXXLIBS="-lm -lpthread -ldl -lcrypt"\n'
                ),
            )

    @property
    def supports_vscode(self) -> bool:
        return True

    async def build_vscode_provisioning(self, request: CgVsCodeRequest) -> CgVsCodeProvisioning:
        """A single `cppdbg` configuration in which **gdb launches the program itself**, plus the
           task that prepares the build and a `devcontainer.json` for IntelliSense.

           gdb runs *inside the container*, reached by `pipeTransport` shelling out to `docker exec`,
           so the host needs nothing but Docker. From gdb's point of view this is then an ordinary
           local target: it forks and execs the program, wires breakpoints before a single
           instruction runs, and owns its stdin, stdout and stderr.

           **There is no gdbserver**, and that is deliberate. gdbserver exists for targets that
           cannot run gdb--embedded boards, foreign architectures, machines reachable only over a
           network. Here gdb is already on the target, so a second debugger-side process in the same
           container, talking to the first over a socket, buys nothing and costs the thing that
           matters: whoever execs the program owns its descriptors. With gdbserver doing it, the
           program's output went to gdbserver's terminal and never reached the Debug Console, and its
           stdin had to be arranged separately. With gdb doing it, the program's I/O is simply the
           debug session's, exactly as VS Code's own Dev Containers arrangement works.

           Everything the program needs is set up before `-exec-run`--see `_SETUP_COMMANDS`, notably
           the stdin redirection that makes it read the selected test case.

           **Nothing here is specific to a working directory**, so it is written once and never
           regenerated. Which directory and which test case are both resolved at launch time by the
           `preLaunchTask` (`--file ${file}`, plus `.meta/selected-test.json`), and the container is
           per *workspace*, so its name is a constant--see
           `codingame_tools.language._docker.container_name_for`.

           **No `sourceFileMap` at all.** Two separate things make that possible: the workspace is
           bind-mounted at its own path, so the paths the compiler recorded are already the paths VS
           Code has open; and the solution is one real file rather than a `solution.<ext>` symlink
           over a fixed `data/solution.src`, so gdb's `file` (from the DWARF) and `fullname` (its own
           `realpath`) name the same thing. While that symlink existed, a mapping was needed to stop
           the editor navigating away from the file the breakpoints were set in--and it applied in
           *both* directions, which then broke binding.

           The task passes `${workspaceFolder}` explicitly rather than letting cg guess the mount
           root, so VS Code's real workspace wins over `find_workspace_root`'s heuristic. A mismatch
           is self-correcting: the mount is part of the container spec hash, so a differently-mounted
           container is recreated rather than reused."""
        container = container_name_for(request.workspace_root)
        architecture = target_architecture()
        target = '--file "${file}" --workspace-root "${workspaceFolder}"'

        return CgVsCodeProvisioning(
                configurations=[
                        {
                            "name": entry_name(self.cg_id, ACTION_DEBUG),
                            "presentation": PRESENTATION,
                            "type": "cppdbg",
                            "request": "launch",
                            "program": f"{BUILD_DIR}/{LANG_SLUG}/debug/solution",
                            "cwd": BUILD_DIR,
                            "MIMode": "gdb",
                            "miDebuggerPath": "/usr/bin/gdb",
                            "stopAtEntry": True,
                            **({"targetArchitecture": architecture}
                               if architecture is not None else {}),
                            "externalConsole": False,
                            "pipeTransport": {
                                "pipeProgram": "docker",
                                "pipeArgs": ["exec", "-i", container, "sh", "-c"],
                                "debuggerPath": "/usr/bin/gdb",
                                "pipeCwd": "",
                            },
                            "setupCommands": _SETUP_COMMANDS,
                            "preLaunchTask": entry_name(self.cg_id, ACTION_PREPARE_DEBUG),
                            **({"logging": _ADAPTER_LOGGING}
                               if request.debug_adapter_logging else {}),
                        },
                    ],
                tasks=[
                        {
                            "label": entry_name(self.cg_id, ACTION_PREPARE_DEBUG),
                            "type": "shell",
                            "command": f"cg debug start {target}",
                            "presentation": _TASK_PRESENTATION,
                            "problemMatcher": _BUILD_PROBLEM_MATCHER,
                        },
                    ],
                retired_names=[
                    # Through the gdbserver design this language generated a *pair* of tasks, one to
                    # start a debug server and one to kill it. gdb needs neither: it launches the
                    # program and it dies with the session.
                    entry_name(self.cg_id, "Start debug session"),
                    entry_name(self.cg_id, "Stop debug session"),
                ],
                files={
                    # Under .meta/, not the working directory root: this file is generated, never
                    # hand-edited, and .meta/ is the one place already gitignored -- at the root it
                    # would be committed into whatever repository tracks the working directory.
                    f"{request.ctx.meta_dir.relative_to(request.ctx.root).as_posix()}/.devcontainer/devcontainer.json":
                        _devcontainer_json(request.workspace_root),
                },
                obsolete_files=[".devcontainer/devcontainer.json"],
                recommended_extensions=["ms-vscode.cpptools"],
            )


LANGUAGE = CgCppLanguage()
