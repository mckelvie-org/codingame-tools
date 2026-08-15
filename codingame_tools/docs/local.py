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
import hashlib
import socket
import subprocess
import sys
import threading
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal

CONFIG_FILE_NAME = "mkdocs.yml"
"""Kept under the MkDocs name even though ProperDocs builds it -- see doc/design/docs-toolchain.md."""

STARTUP_TIMEOUT_SECONDS = 120.0
"""How long to wait for a build-and-serve to accept connections. Generous because the first build
   has to import the whole package and run mkdocstrings over it, which takes several seconds cold."""

POLL_INTERVAL_SECONDS = 0.25


LocalDocsMode = Literal["build", "watch", "existing"]
"""How [`start_local_docs`][codingame_tools.docs.local.start_local_docs] obtains what it serves.

   - `build`: build into the output directory, then serve it. What `cg doc` does, so that a later
     `existing` against the same directory serves exactly the same bytes.
   - `watch`: run the live-reloading dev server, which rebuilds on edits and serves from its own
     memory. What `bin/docs` does while you are *writing* docs -- it writes no output directory
     at all.
   - `existing`: serve whatever is already in that same directory, without building. About ten
     times faster to first page, and what you want when reading rather than writing."""


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

    output: Path | None = None
    """The directory being served, or None for `watch`, which serves from memory."""

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
    """The build directory the site's own config declares, relative to the checkout."""
    configured = config.get("site_dir")
    return root / (configured if isinstance(configured, str) else "site")


def docs_cache_dir(root: Path) -> Path:
    """Where `cg doc` builds a checkout's documentation: a per-user cache, never the checkout.

       `cg doc` is package functionality -- something a *user* of cg runs, from anywhere on the
       system -- whereas `bin/gen-docs` and `bin/docs` are contributor tooling operating on a tree
       you are working in. That is the line, and it is a better one than "wherever happens to be
       writable": a user command should not write into a source tree at all, not even one it could.

       Rebuilt output belongs in a cache by nature. It is byte-deterministic, reproducible from the
       source in seconds, and read by nobody directly, so losing it costs only the rebuild.

       Keyed by the checkout's path, so two checkouts on one machine never overwrite each other and
       serve the wrong tree's documentation. Hashed rather than embedded, since the path may be
       long, deep, and full of characters a directory name cannot carry; the readable stem is kept
       as a prefix so the cache stays browsable."""
    from ..config.resolver import default_global_cache_dir

    key = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]
    return default_global_cache_dir() / "docs" / f"{root.name}-{key}"


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
                     mode: LocalDocsMode = "build",
                     output: Path | None = None) -> LocalDocsServer:
    """Start serving the checkout's documentation.

       Args:
           root: The repository root, from `find_source_checkout()`.
           host: Address to serve on.
           port: Port to serve on, or None to let the OS pick an unused one.
           mode: How to obtain what gets served -- see [`LocalDocsMode`][codingame_tools.docs.local.LocalDocsMode].
           output: Directory to build into and serve from. Defaults to the checkout's own
               `site_dir`, which is what the contributor tools (`bin/gen-docs`, `bin/docs`) share.
               `cg doc` passes [`docs_cache_dir`][codingame_tools.docs.local.docs_cache_dir]
               instead, so a user command never writes into a source tree. Whatever it is, `build`
               and `existing` are given the same one, or `--no-rebuild` would serve a different
               build than the one just produced.

       Raises:
           LocalDocsError: If the build fails, or `existing` finds nothing to serve."""
    config = read_config(root)
    prefix = base_path(config)
    port = port or free_port(host)
    url = f"http://{host}:{port}{prefix}"
    built = output if output is not None else site_dir(root, config)

    if mode == "build":
        # Into the configured site_dir -- the same directory bin/gen-docs writes -- so that this
        # and `bin/gen-docs` leave the tree in the same state, and a later `existing` serves exactly
        # what was just served. Building somewhere private would make `cg doc` followed by
        # `cg doc --no-rebuild` show two different things, which is the one behaviour nobody expects.
        #
        # --strict is deliberately *not* used, unlike bin/gen-docs: a warning should not stop you
        # reading the docs. Output is identical either way when the build is clean, which is what
        # keeps the two commands interchangeable.
        built.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(  # noqa: S603
                [sys.executable, "-m", "properdocs", "build", "-f", CONFIG_FILE_NAME,
                 "-d", str(built)], cwd=root)
        if result.returncode != 0:
            raise LocalDocsError(
                    f"the documentation build failed with code {result.returncode}. "
                    "Run `bin/gen-docs` in the checkout to see it again with --strict.")
        mode = "existing"

    if mode == "existing":
        if not (built / "index.html").is_file():
            where = built.relative_to(root) if built.is_relative_to(root) else built
            raise LocalDocsError(
                    f"there is no existing build in {where} to serve. "
                    "Build one first (`bin/gen-docs`, or drop the no-rebuild option).")
        server = _start_static_server(host, port, built, prefix)

        def stop_static() -> None:
            server.shutdown()
            server.server_close()

        return LocalDocsServer(url=url, host=host, port=port, stop=stop_static, output=built)

    # `watch`: the live-reloading dev server. It serves from its own memory and never populates
    # site_dir, which is exactly why it is not the default for `cg doc`.
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
