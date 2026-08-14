"""Opening a documentation page in a dedicated, chrome-less browser window.

Shared by `cg doc` and by the repo's own `bin/docs` preview, so both behave identically and there is
one place where the Playwright quirks below are known about.

The browser is the same Playwright Chromium `cg login` uses, installed on first use exactly as that
does -- one shared browser, kept current by whichever command happens to run first. The *profile* is
never shared: this opens a throwaway temporary directory, so reading the docs cannot see, touch or
outlive the saved CodinGame session.
"""

from __future__ import annotations

import asyncio
import contextlib
import subprocess
import sys
import tempfile

from ..credentials.browser_login.common import ensure_playwright_chromium_installed

POLL_INTERVAL_SECONDS = 0.25
"""How often to check whether the window is still open, once it has been handed to the reader."""


async def activate_on_macos(profile_dir: str) -> bool:
    """Bring the launched Chromium to the front, on macOS.

       Playwright's Chromium is not a registered application bundle, so macOS does not activate it
       when it opens: the window is visible but never becomes the *key* window, and input can go to
       whatever was in front instead. Clicking it sometimes fixes that and sometimes does not, which
       is a miserable way to discover the problem.

       Found by profile directory rather than by name, so it can only ever match the browser this
       call launched--never the user's own Chrome, and never the persistent one `cg login` keeps.
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


async def open_window_and_wait(url: str, *, app_window: bool = True,
                               on_ready: str | None = None) -> None:
    """Open `url` in an app-mode Chromium window and return once the reader closes it.

       Uses `launch_persistent_context`, not `launch`. That is not a stylistic choice: Playwright
       starts a plain `launch()` browser with no startup window, because it expects to create pages
       itself -- so `--app=URL` is silently swallowed and *nothing appears on screen*, while the
       browser still reports itself connected. A persistent context opens its startup window, which
       is what `--app` needs to take effect.

       Args:
           url: The page to open.
           app_window: Open a chrome-less app window (no address bar or tabs), the same trick a PWA
               uses. Set false for an ordinary window with an address bar, which is the escape hatch
               when the app window misbehaves.
           on_ready: A line to print once the window is up, naming what is being served and how to
               stop it. Suppressed entirely when None."""
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
                            "Chromium started but opened no window. This is the `--app` flag "
                            "failing to take effect; see this function's docstring.")
                if not await activate_on_macos(profile):
                    print("(could not bring the window to the front automatically; "
                          "click it once if it does not respond)", file=sys.stderr)
                if on_ready is not None:
                    print(on_ready, file=sys.stderr)
                # `close` fires when the browser goes away, but closing the last *tab* leaves the
                # context briefly alive, so the page count is polled as well.
                while not closed.is_set() and context.pages:
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
            finally:
                with contextlib.suppress(Exception):
                    await context.close()
