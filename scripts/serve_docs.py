#!/usr/bin/env python
"""Serve the documentation site and open it in a dedicated browser window.

Run it with `bin/docs`. Starts `properdocs serve`, waits for it to accept connections, opens the
site in a Chromium window with no address bar or tabs, and shuts the server down when that window
is closed.

Uses the same Playwright Chromium as `cg login`, installing it on first use exactly as that does --
one shared browser, kept current by whichever command happens to run first. No CodinGame account or
login is involved. The *profile* is not shared: it is a throwaway temporary directory rather than the
persistent one `cg login` keeps, so a docs preview cannot see saved credentials.

Chromium is launched in **app mode** (`--app=URL`), the same trick a PWA uses: the window has no
browser chrome, so a docs preview looks like a docs viewer rather than a stray tab you later wonder
about.

The point is the cleanup. `properdocs serve` is a foreground process that outlives a browser tab,
so the usual way to preview docs leaves a server running on port 8000 until you remember it. Here
the window *is* the lifetime: close it and the server goes with it, and Ctrl-C does the same from
the terminal.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import functools
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from collections.abc import Callable
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from codingame_tools.credentials.browser_login.common import (  # noqa: E402  (needs the path fix)
    ensure_playwright_chromium_installed,
)

DEFAULT_HOST = "127.0.0.1"
STARTUP_TIMEOUT_SECONDS = 120.0
"""How long to wait for `properdocs serve` to accept connections. Generous because the first build
   has to import the whole package and run mkdocstrings over it, which takes several seconds
   cold."""

POLL_INTERVAL_SECONDS = 0.25




def _install_signal_handlers() -> None:
    """Route SIGTERM and SIGHUP onto the same path as Ctrl-C.

       Python raises `KeyboardInterrupt` for SIGINT, so `finally` blocks run and the server is
       cleaned up. It does *not* do that for SIGTERM (a plain `kill`) or SIGHUP (the terminal window
       closing) -- the default action terminates the process outright, `finally` never runs, and the
       browser and server are orphaned. Which is the exact failure this script exists to prevent, so
       it must not be reachable by closing a terminal."""
    def _raise(signum: int, _frame: object) -> None:
        raise KeyboardInterrupt(f"received signal {signum}")

    for sig in (signal.SIGTERM, signal.SIGHUP):
        with contextlib.suppress(ValueError, OSError, AttributeError):
            signal.signal(sig, _raise)


def _read_config() -> dict[str, object]:
    """The site config, parsed loosely -- only a couple of top-level keys are wanted from it."""
    import yaml

    with (REPO_ROOT / "mkdocs.yml").open(encoding="utf-8") as fd:
        # mkdocs.yml uses tags (e.g. !ENV) that a plain safe_load rejects; nothing read here needs
        # them resolved, so it is loaded with a loader that ignores what it does not understand.
        class _Tolerant(yaml.SafeLoader):
            pass

        _Tolerant.add_multi_constructor("!", lambda loader, suffix, node: None)
        config: dict[str, object] = yaml.load(fd, Loader=_Tolerant) or {}
    return config


def _base_path(config: dict[str, object]) -> str:
    """The URL path the docs server will serve under, from `site_url`.

       `properdocs serve` honours `site_url`'s path, so a project published at
       `https://host/codingame-tools/` is served locally at `http://127.0.0.1:PORT/codingame-tools/`,
       not at the root. Requesting `/` only works because the server 302s it; every deeper link
       404s. So the prefix is read from the config rather than assumed away."""
    site_url = config.get("site_url")
    path = urllib.parse.urlparse(site_url if isinstance(site_url, str) else "").path or "/"
    return path if path.endswith("/") else path + "/"


def _site_dir(config: dict[str, object]) -> Path:
    """Where a completed build lands -- what `--quick` serves instead of building again."""
    site_dir = config.get("site_dir")
    return REPO_ROOT / (site_dir if isinstance(site_dir, str) else "site")


def _free_port(host: str) -> int:
    """An unused port, chosen by the OS.

       Rather than the default 8000, which is the single most contended port on a developer
       machine--and a preview that silently attaches to someone else's server, or refuses to start
       because of one, is worse than either failing or just working."""
    with socket.socket() as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


async def _wait_until_serving(host: str, port: int,
                              died: Callable[[], str | None] | None = None) -> None:
    """Block until the server accepts a connection, or it dies trying.

       `died` reports that the server is gone and why, so a build failure surfaces as its own error
       instead of as a startup timeout thirty seconds later."""
    deadline = asyncio.get_running_loop().time() + STARTUP_TIMEOUT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        if died is not None and (reason := died()) is not None:
            raise RuntimeError(reason)
        with contextlib.suppress(OSError), socket.create_connection((host, port), timeout=1.0):
            return
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(f"the documentation server did not start listening on {host}:{port} within "
                       f"{STARTUP_TIMEOUT_SECONDS:.0f}s")


def _start_static_server(host: str, port: int, site_dir: Path, base_path: str) -> ThreadingHTTPServer:
    """Serve an already-built site, in-process, under the same base path a real build would use.

       This is what `--quick` trades the build for. A full `properdocs serve` re-runs mkdocstrings
       over the whole package before it will answer a single request, which is several seconds even
       when nothing changed -- too slow when the reason for opening the docs is to look something
       up rather than to write them.

       The base path is stripped here rather than ignored, so the URLs are identical to the built
       site's and to the published one's. Serving at the root instead would 404 every internal
       link."""
    class _Handler(SimpleHTTPRequestHandler):
        def translate_path(self, path: str) -> str:
            parsed = urllib.parse.urlparse(path)
            trimmed = parsed.path
            if base_path != "/" and trimmed.startswith(base_path):
                trimmed = "/" + trimmed[len(base_path):]
            # The parent sanitises traversal (`..`, absolute paths) and resolves directory indexes;
            # only the prefix is this override's business.
            return super().translate_path(trimmed)

        def log_message(self, format: str, *args: object) -> None:
            """Silence per-request logging; the point of this window is to read the docs."""

    handler = functools.partial(_Handler, directory=str(site_dir))
    server = ThreadingHTTPServer((host, port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server



async def _activate_on_macos(profile_dir: str) -> bool:
    """Bring the launched Chromium to the front, on macOS.

       Playwright's Chromium is not a registered application bundle, so macOS does not activate it
       when it opens: the window is visible but never becomes the *key* window, and input can go to
       whatever was in front instead. Clicking it sometimes fixes that and sometimes does not, which
       is a miserable way to discover the problem.

       Found by profile directory rather than by name, so it can only ever match the browser this
       script launched--never the user's own Chrome, and never the persistent one `cg login` keeps.
       Best-effort: a failure here means the window may need a click to focus, not that anything is
       broken, so it never raises."""
    if sys.platform != "darwin":
        return False
    with contextlib.suppress(Exception):
        found = subprocess.run(["pgrep", "-f", profile_dir],  # noqa: S603, S607
                               capture_output=True, text=True, timeout=10)
        for pid in found.stdout.split():
            result = subprocess.run(  # noqa: S603, S607
                    ["osascript", "-e",
                     f'tell application "System Events" to set frontmost of '
                     f'(first process whose unix id is {pid}) to true'],
                    capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                return True
    return False



async def _open_window_and_wait(url: str, *, app_window: bool = True) -> None:
    """Open `url` in an app-mode Chromium window and return once the user closes it.

       Uses `launch_persistent_context`, not `launch`. That is not a stylistic choice: Playwright
       starts a plain `launch()` browser with no startup window, because it expects to create pages
       itself -- so `--app=URL` is silently swallowed and *nothing appears on screen*, while the
       browser still reports itself connected. A persistent context opens its startup window, which
       is what `--app` needs to take effect.

       The profile is a throwaway temporary directory rather than the persistent one `cg login`
       keeps, so a docs preview can never see or disturb saved CodinGame credentials."""
    from playwright.async_api import async_playwright

    # Same self-healing install `cg login` performs, and the same shared browser. Sharing it is the
    # point: one Chromium, kept current by whichever command runs first.
    await ensure_playwright_chromium_installed()
    with tempfile.TemporaryDirectory(prefix="cg-docs-browser-") as profile:
        async with async_playwright() as pw:
            # `--disable-blink-features=AutomationControlled` mirrors the login browser, which is
            # the configuration known to accept real user input in this project.
            args = ["--disable-blink-features=AutomationControlled"]
            args.append(f"--app={url}" if app_window else f"--new-window={url}")
            context = await pw.chromium.launch_persistent_context(
                    profile, headless=False, args=args)
            if not app_window and not context.pages:
                await context.new_page()
            if context.pages and context.pages[0].url in ("about:blank", ""):
                await context.pages[0].goto(url)
            closed = asyncio.Event()
            context.on("close", lambda _: closed.set())
            try:
                if not context.pages:
                    raise RuntimeError(
                            "Chromium started but opened no window. This is the `--app` flag failing "
                            "to take effect; see this function's docstring.")
                if not await _activate_on_macos(profile):
                    print("(could not bring the window to the front automatically; "
                          "click it once if it does not respond)", file=sys.stderr)
                print(f"Serving {url} -- close the window to stop.", file=sys.stderr)
                # `close` fires when the browser goes away, but closing the last *tab* leaves the
                # context briefly alive, so the page count is polled as well.
                while not closed.is_set() and context.pages:
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
            finally:
                with contextlib.suppress(Exception):
                    await context.close()


def _start_build_server(host: str, port: int) -> tuple[Callable[[], str | None],
                                                       Callable[[], None]]:
    """Start `properdocs serve`, returning the callbacks that report on and end its lifetime."""
    # --strict is deliberately *not* used here, unlike bin/gen-docs: a warning should not stop you
    # previewing what you are in the middle of writing. CI and bin/gen-docs enforce it.
    # `properdocs`, not `mkdocs` -- see the docs dependency group in pyproject.toml.
    process = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "properdocs", "serve", "-f", "mkdocs.yml",
             "--dev-addr", f"{host}:{port}"],
            cwd=REPO_ROOT)

    def died() -> str | None:
        if process.poll() is None:
            return None
        return (f"properdocs serve exited with code {process.returncode} before serving anything. "
                "Run `bin/gen-docs` to see the build errors.")

    def stop() -> None:
        if process.poll() is None:
            process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=10)
            if process.poll() is None:
                process.kill()

    return died, stop


async def _run(host: str, port: int | None, *, app_window: bool = True, quick: bool = False) -> int:
    config = _read_config()
    port = port or _free_port(host)
    url = f"http://{host}:{port}{_base_path(config)}"

    died: Callable[[], str | None] | None = None
    if quick:
        site_dir = _site_dir(config)
        if not (site_dir / "index.html").is_file():
            print(f"--quick needs an existing build, and {site_dir.relative_to(REPO_ROOT)}/ does "
                  "not contain one.\nRun `bin/gen-docs` once, then --quick will serve it.",
                  file=sys.stderr)
            return 2
        server = _start_static_server(host, port, site_dir, _base_path(config))

        def stop() -> None:
            server.shutdown()
            server.server_close()

        print(f"Serving the existing build in {site_dir.relative_to(REPO_ROOT)}/ -- source changes "
              "will not appear. Drop --quick to rebuild.", file=sys.stderr)
    else:
        died, stop = _start_build_server(host, port)

    try:
        await _wait_until_serving(host, port, died)
        await _open_window_and_wait(url, app_window=app_window)
    finally:
        # The whole reason this script exists: the window's lifetime is the server's lifetime.
        stop()
        print("Documentation server stopped.", file=sys.stderr)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help=f"Address to serve on. Default: {DEFAULT_HOST}.")
    parser.add_argument("--port", type=int, default=None,
                        help="Port to serve on. Default: an unused one chosen by the OS.")
    parser.add_argument("--windowed", action="store_true",
                        help="Open an ordinary browser window with an address bar, instead of a "
                             "chrome-less app window. Use this if the app window misbehaves.")
    parser.add_argument("-q", "--quick", action="store_true",
                        help="Serve the existing build in site/ instead of rebuilding first. Opens "
                             "immediately, but shows the docs as they were at the last build. Run "
                             "`bin/gen-docs` to refresh it.")
    args = parser.parse_args()
    _install_signal_handlers()
    try:
        sys.exit(asyncio.run(_run(args.host, args.port, app_window=not args.windowed,
                                  quick=args.quick)))
    except KeyboardInterrupt:
        # Ctrl-C is a normal way to stop this; the finally above has already killed the server.
        sys.exit(130)


if __name__ == "__main__":
    main()
