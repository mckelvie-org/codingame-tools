#!/usr/bin/env python
"""Serve this checkout's documentation and open it in a dedicated browser window.

Run it with `bin/docs`. Starts the docs server, waits for it to accept connections, opens the site in
a Chromium window with no address bar or tabs, and shuts the server down when that window is closed.

The mechanics all live in `codingame_tools.docs`, because `cg doc` needs exactly the same things and
two copies would drift. What stays here is the developer-facing framing: this always serves the
working tree, where `cg doc` prefers the published site unless it detects a checkout.

The point is the cleanup. `properdocs serve` is a foreground process that outlives a browser tab, so
the usual way to preview docs leaves a server running on port 8000 until you remember it. Here the
window *is* the lifetime: close it and the server goes with it, and Ctrl-C does the same from the
terminal.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from codingame_tools.docs import (  # noqa: E402  (needs the path fix above)
    LocalDocsError,
    open_window_and_wait,
    start_local_docs,
)

DEFAULT_HOST = "127.0.0.1"


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


async def _run(host: str, port: int | None, *, app_window: bool = True, quick: bool = False) -> int:
    try:
        server = start_local_docs(REPO_ROOT, host=host, port=port, rebuild=not quick)
    except LocalDocsError as e:
        print(f"{e}", file=sys.stderr)
        return 2

    if quick:
        print("Serving the existing build -- source changes will not appear. "
              "Drop --quick to rebuild.", file=sys.stderr)
    try:
        await server.wait_until_ready()
        await open_window_and_wait(server.url, app_window=app_window,
                                   on_ready=f"Serving {server.url} -- close the window to stop.")
    finally:
        # The whole reason this script exists: the window's lifetime is the server's lifetime.
        server.stop()
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
