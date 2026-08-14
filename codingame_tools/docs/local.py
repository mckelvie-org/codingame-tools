"""Serving the documentation from a source checkout, instead of reading the published site.

Only reachable when `cg` is running from its own working tree (see
[`find_source_checkout`][codingame_tools.docs.site.find_source_checkout]). There it is strictly
better than the published site: it shows the docs for the code actually in the tree, including
whatever is uncommitted, rather than the docs for the last release.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import socket
import subprocess
import sys
import threading
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CONFIG_FILE_NAME = "mkdocs.yml"
"""Kept under the MkDocs name even though ProperDocs builds it -- see doc/design/docs-toolchain.md."""

STARTUP_TIMEOUT_SECONDS = 120.0
"""How long to wait for a build-and-serve to accept connections. Generous because the first build
   has to import the whole package and run mkdocstrings over it, which takes several seconds cold."""

POLL_INTERVAL_SECONDS = 0.25


class LocalDocsError(Exception):
    """Local documentation cannot be served, with a message saying what to do about it."""


@dataclass(frozen=True)
class LocalDocsServer:
    """A running local documentation server, and the handles needed to watch and stop it."""

    url: str
    """Where the site is being served, base path included."""

    host: str
    """Address it is listening on."""

    port: int
    """Port it is listening on."""

    stop: Callable[[], None]
    """Shut the server down. Safe to call whether or not it is still running."""

    died: Callable[[], str | None] | None = None
    """Why the server is gone, or None while it is alive. None for a server that cannot crash."""

    async def wait_until_ready(self) -> None:
        """Block until the server answers, raising if it died building instead."""
        await wait_until_serving(self.host, self.port, self.died)


def read_config(root: Path) -> dict[str, object]:
    """The site config, parsed loosely -- only a couple of top-level keys are wanted from it."""
    import yaml

    with (root / CONFIG_FILE_NAME).open(encoding="utf-8") as fd:
        # The config uses tags (e.g. !ENV) that a plain safe_load rejects; nothing read here needs
        # them resolved, so it is loaded with a loader that ignores what it does not understand.
        class _Tolerant(yaml.SafeLoader):
            pass

        _Tolerant.add_multi_constructor(  # type: ignore[no-untyped-call]
                "!", lambda loader, suffix, node: None)
        config: dict[str, object] = yaml.load(fd, Loader=_Tolerant) or {}
    return config


def base_path(config: dict[str, object]) -> str:
    """The URL path the docs are served under, from `site_url`.

       The dev server honours `site_url`'s path, so a project published at
       `https://host/codingame-tools/` is served locally at `http://127.0.0.1:PORT/codingame-tools/`,
       not at the root. Requesting `/` only works because the server 302s it; every deeper link
       404s. So the prefix is read from the config rather than assumed away."""
    site_url = config.get("site_url")
    path = urllib.parse.urlparse(site_url if isinstance(site_url, str) else "").path or "/"
    return path if path.endswith("/") else path + "/"


def site_dir(root: Path, config: dict[str, object]) -> Path:
    """Where a completed build lands -- what the no-rebuild path serves instead of building again."""
    configured = config.get("site_dir")
    return root / (configured if isinstance(configured, str) else "site")


def free_port(host: str) -> int:
    """An unused port, chosen by the OS.

       Rather than the default 8000, which is the single most contended port on a developer
       machine--and a preview that silently attaches to someone else's server, or refuses to start
       because of one, is worse than either failing or just working."""
    with socket.socket() as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


async def wait_until_serving(host: str, port: int,
                             died: Callable[[], str | None] | None = None) -> None:
    """Block until the server accepts a connection, or it dies trying.

       `died` reports that the server is gone and why, so a build failure surfaces as its own error
       instead of as a startup timeout two minutes later."""
    deadline = asyncio.get_running_loop().time() + STARTUP_TIMEOUT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        if died is not None and (reason := died()) is not None:
            raise RuntimeError(reason)
        with contextlib.suppress(OSError), socket.create_connection((host, port), timeout=1.0):
            return
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(f"the documentation server did not start listening on {host}:{port} within "
                       f"{STARTUP_TIMEOUT_SECONDS:.0f}s")


def _start_static_server(host: str, port: int, directory: Path,
                         prefix: str) -> ThreadingHTTPServer:
    """Serve an already-built site, in-process, under the same base path a real build would use.

       This is what skipping the rebuild trades away. A full dev-server start re-runs mkdocstrings
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
            if prefix != "/" and trimmed.startswith(prefix):
                trimmed = "/" + trimmed[len(prefix):]
            # The parent sanitises traversal (`..`, absolute paths) and resolves directory indexes;
            # only the prefix is this override's business.
            return super().translate_path(trimmed)

        def log_message(self, format: str, *args: object) -> None:
            """Silence per-request logging; the point of this window is to read the docs."""

    handler = functools.partial(_Handler, directory=str(directory))
    server = ThreadingHTTPServer((host, port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def start_local_docs(root: Path, *, host: str = "127.0.0.1", port: int | None = None,
                     rebuild: bool = True) -> LocalDocsServer:
    """Start serving the checkout's documentation, building it first unless `rebuild` is false.

       Args:
           root: The repository root, from `find_source_checkout()`.
           host: Address to serve on.
           port: Port to serve on, or None to let the OS pick an unused one.
           rebuild: Build the site first and live-reload on edits -- what you want while writing
               docs. False serves the existing `site/` as-is, which is roughly ten times faster to
               first page and is what you want while reading them.

       Raises:
           LocalDocsError: If `rebuild` is false and there is no existing build to serve."""
    config = read_config(root)
    prefix = base_path(config)
    port = port or free_port(host)
    url = f"http://{host}:{port}{prefix}"

    if not rebuild:
        built = site_dir(root, config)
        if not (built / "index.html").is_file():
            raise LocalDocsError(
                    f"there is no existing build in {built.relative_to(root)}/ to serve. "
                    "Run `bin/gen-docs` in the checkout once, or drop the no-rebuild option.")
        server = _start_static_server(host, port, built, prefix)

        def stop_static() -> None:
            server.shutdown()
            server.server_close()

        return LocalDocsServer(url=url, host=host, port=port, stop=stop_static)

    # --strict is deliberately *not* used: a warning should not stop you previewing what you are in
    # the middle of writing. CI and bin/gen-docs enforce it.
    # `properdocs`, not `mkdocs` -- see the docs dependency group in pyproject.toml.
    process = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "properdocs", "serve", "-f", CONFIG_FILE_NAME,
             "--dev-addr", f"{host}:{port}"],
            cwd=root)

    def died() -> str | None:
        if process.poll() is None:
            return None
        return (f"the documentation server exited with code {process.returncode} before serving "
                "anything. Run `bin/gen-docs` in the checkout to see the build errors.")

    def stop_build() -> None:
        if process.poll() is None:
            process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=10)
            if process.poll() is None:
                process.kill()

    return LocalDocsServer(url=url, host=host, port=port, stop=stop_build, died=died)
