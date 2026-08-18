"""CLI interface for contribution manager."""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import subprocess
import sys
import textwrap
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any, cast

import aiohttp
from argparse_wizard import CliBase, CliCommand, CliError, CliExit, OptCmdFunc, cli_command
from json_data_types import JsonData, JsonList
from rich.console import Console

from ..client.client import CgClient
from ..client.common.protocol.codingamer import CgCodingamePointsStats, CgXpThreshold
from ..client.common.protocol.contribution import (
    CgContributionData,
    CgPendingContribution,
    CgPersonalContribution,
    CgTopic,
)
from ..client.common.protocol.test_session import CgMultipleLanguagesTestParams, CgPlayRequest, CgSubmitRequest
from ..client.common.protocol.user import CgUserProperties
from ..client.common.raw_client import CgAuthenticationError, CgDownloadFileResult, compute_content_hash
from ..common.timestamps import parse_timestamp
from ..common.typedefs import Self, override
from ..config import (
    CONFIG_FILE_NAME,
    CONFIG_SUBDIR_NAME,
    DATA_SUBDIR_NAME,
    PROJECT_CONFIG_MARKER_DIR_NAME,
    CgConfig,
    CgConfigData,
    default_global_config_file,
    find_config_file,
    resolve_config,
)
from ..contribution_manager import (
    CONTRIBUTION_DIFFICULTIES,
    CONTRIBUTION_IDENTITY_FILE_NAME,
    SERVER_BRANCH_NAME,
    SUPPORTED_PUZZLE_TYPES,
    CgContributionCommitMetadata,
    CgContributionLocalTestResult,
    CgContributionManager,
    CgContributionManagerError,
    CgContributionStatus,
    CgContributionSyncStatus,
    CgContributionView,
    CgMergeStartStatus,
    CgRebaseStatus,
    find_contribution_dir,
    redact_commit_contribution,
    renormalize_test_case_dirs,
    resolve_contribution_dir,
)
from ..credentials.browser_login import async_cg_browser_login, cg_browser_delete_session
from ..credentials.cg_credentials import (
    CgCredentials,
    get_credentials_with_override,
    set_credentials,
    validate_profile_name,
)
from ..docs import (
    LocalDocsError,
    docs_cache_dir,
    find_source_checkout,
    open_window_and_wait,
    published_docs_url,
    start_local_docs,
)
from ..language import (
    BASE_IMAGE,
    DEFAULT_BUILD_TIMEOUT_SECONDS,
    DEFAULT_RUN_TIMEOUT_SECONDS,
    DEFAULT_TOOLCHAIN_BUILD_TIMEOUT_SECONDS,
    PREAMBLE,
    TOOLCHAIN_SUBDIR_NAME,
    CgBuildProfile,
    CgLanguageOperationNotSupportedError,
    CgVsCodeMergeError,
    all_fragments,
    build_image_content,
    clean_managed,
    compose_dockerfile,
    compose_with_base,
    default_languages,
    ensure_base_dockerfile,
    ensure_image,
    fragments_for_languages,
    get_language,
    image_tag_for,
    render_dockerfile,
    resolve_language_slugs,
    tag_image,
)
from ..puzzle_manager import (
    PUZZLE_IDENTITY_FILE_NAME,
    CgPuzzleManager,
    CgPuzzleManagerError,
    CgPuzzleStatus,
    find_puzzle_dir,
    parse_statement_html,
    resolve_puzzle_dir,
)
from ..settings import CgSettings, relativize_settings_dir, resolve_settings
from ..topics import (
    AmbiguousTopicError,
    TopicResolutionError,
    get_topic_catalogue,
    resolve_topic,
    same_topic,
    search_topics,
    topic_label,
    topic_labels,
)
from ..workdir import CgWorkingDir, find_working_dir, resolve_working_dir, working_dir_kind

logger = logging.getLogger(__name__)

def _isoformat_z(dt: datetime) -> str:
    """Render a UTC-aware datetime as ISO 8601 with a trailing "Z" instead of "+00:00"--both are
       equally standard (RFC 3339/ISO 8601's "Zulu time" designator for UTC), "Z" is just the
       more common convention."""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _print_captured_output(text: str) -> None:
    """Print a test run's captured stdout verbatim (no extra blank line if it already ends with
       "\\n"), but guarantee a trailing newline regardless--so whatever's printed next (the next
       test's header, a shell prompt) never gets glued onto the same line just because the
       program under test didn't itself end its output with one. A no-op for empty output."""
    if not text:
        return
    print(text, end="" if text.endswith("\n") else "\n")

def _format_xp_progress(xp: int | None, level: int | None, xp_thresholds: list[CgXpThreshold]) -> str:
    """`"{xp}   ({progress}/{needed} to level {level + 1})"`, using `xp_thresholds`'
       `cumulative_xp` (total XP required to reach a given level) to derive `progress` (XP earned
       within the current level) and `needed` (XP required for the current level as a whole)--
       both already implied by `findCodingamePointsStatsByHandle`'s own response, no separate
       formula/lookup needed. Falls back to a bare XP number if `xp`/`level` is unknown, or if
       `xp_thresholds` doesn't include entries for both the current and next level (observed live
       to include the current level onward, but not documented as guaranteed)."""
    if xp is None:
        return "(unknown)"
    if level is None:
        return str(xp)
    cumulative_xp_by_level = {t.level: t.cumulative_xp for t in xp_thresholds}
    current_base = cumulative_xp_by_level.get(level)
    next_base = cumulative_xp_by_level.get(level + 1)
    if current_base is None or next_base is None:
        return str(xp)
    progress = xp - current_base
    needed = next_base - current_base
    return f"{xp}   ({progress}/{needed} to level {level + 1})"

_SYNC_STATUS_TEXT: dict[CgContributionSyncStatus, str] = {
    CgContributionSyncStatus.NOT_PUSHED: "not yet pushed",
    CgContributionSyncStatus.UP_TO_DATE: "up to date",
    CgContributionSyncStatus.LOCAL_AHEAD: "local changes not yet pushed--`cg contribution push` would succeed cleanly",
    CgContributionSyncStatus.SERVER_AHEAD: "server has new changes--`cg contribution rebase` would fast-forward cleanly",
    CgContributionSyncStatus.DIVERGED: "diverged--both sides changed; see `cg contribution diff` and `cg contribution merge`",
    CgContributionSyncStatus.MERGE_IN_PROGRESS: "merge in progress",
}
"""Human-readable text for `cg contribution status`'s `CgContributionSyncStatus` display."""

def default_config_template(default_data_dir: str) -> str:
    """Build the content for a freshly-`init`'d config.yaml.

       Hand-written (not generated via CgConfigData.to_yaml()) so it can carry comments--plain
       YAML dumping can't emit those. Deliberately kept in sync with CgConfigData's actual fields
       by a test that parses this (for some placeholder path) and asserts it equals
       CgConfigData() (all defaults); update both together if a field is added, renamed, or its
       default changes.

       `default_data_dir` is the commented-out example value to show for `dataDir`, as a string
       (not necessarily an absolute path)--the caller decides what's appropriate for the specific
       config file being created:
         - project-local: the literal relative path `"../data"` (`DATA_SUBDIR_NAME`), NOT an
           absolute path resolved for one specific `--at` location--so the freshly-created
           config.yaml keeps working with its default data directory even if the whole project
           gets renamed or moved elsewhere. An absolute path here would silently break that.
         - `--global`: the actual resolved absolute data directory, since there's no comparable
           sibling relationship between the global config and data directories to express as a
           relative path (see `default_global_data_dir`'s docstring).
    """
    return f"""\
# codingame-tools configuration file.
#
# Run `cg config where` to see which config file is currently active (this one, unless
# shadowed by a more specific one), and `cg config dump` to see the fully resolved
# configuration, including defaults for anything left unset here.

# Override the persistent, app-writable data directory. A relative path is resolved relative
# to the directory containing this file; an absolute path (or a "~"-prefixed path) is used
# as-is. Currently defaults to (uncomment to pin explicitly):
#dataDir: {default_data_dir}

# Settings, identical in shape to the app-writable settings.json (see `cg settings dump`), but
# hand-edited here rather than set via `cg settings set`. If both a global (per-user) and a
# project-local config.yaml exist, each field below is resolved independently--base to most
# refined: the global file's settings, then the project file's own, then settings.json.
#settings:
#  defaultProfile: my-profile-name
#  contributionDir: /path/to/my/contribution
#  puzzleDir: /path/to/my/puzzle
"""

CONTRIBUTION_SET_FIELDS: dict[str, Callable[[CgContributionView], object]] = {
    "title": lambda v: v.data.title,
    "difficulty": lambda v: v.data.difficulty,
    "draft": lambda v: v.draft,
    "ready-for-moderation": lambda v: v.ready_for_moderation,
    "puzzle-type": lambda v: v.puzzle_type,
    "solution-language": lambda v: v.data.solution_language,
}
"""Every field `cg contribution set` shows, mapped to how each is read off the view.

   Keyed by CLI spelling; each is also a subcommand of `set`, so it documents and types its own
   value."""

PUZZLE_SET_FIELDS: dict[str, Callable[[CgPuzzleManager], object]] = {
    "solution-language": lambda m: (d.solution_language
                                    if (d := m.load_puzzle_data()) is not None else None),
}
"""Every field `cg puzzle set` shows. A puzzle has exactly one editable manifest field, so this is
   a table of one -- kept as a table so `cg puzzle set` and `cg contribution set` stay the same
   shape, and so a second field costs a row rather than a redesign."""

CONTRIBUTION_METADATA_FIELDS = frozenset(CONTRIBUTION_SET_FIELDS) - {"solution-language"}
"""Which of `CONTRIBUTION_SET_FIELDS` are plain edits routed through `update_metadata`.

   `solution-language` is the exception: setting it rewrites the reference solution and can refuse,
   so it goes through `set_language()` instead of being written straight into the JSON."""

CONTRIBUTION_BOOLEAN_FIELDS = frozenset({"draft", "ready-for-moderation"})
"""Which of `CONTRIBUTION_SET_FIELDS` take true/false rather than a string."""

def _format_field_value(value: object) -> str:
    """Render a field value for display: booleans lowercase, an unset field visibly unset."""
    if value is None or value == "":
        return "(unset)"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _print_topic_table(topics: list[CgTopic], *, show_all_labels: bool = False) -> None:
    """Print topics as a fixed-width table, sized to the data rather than to fixed guesses --
       handles range from 3 to 30-odd characters, so a fixed width either truncates or wastes."""
    rows = []
    for topic in topics:
        label = (" / ".join(topic_labels(topic)) if show_all_labels else topic_label(topic))
        rows.append((topic.handle or "", str(topic.id or ""), topic.category or "",
                     str(topic.puzzle_count if topic.puzzle_count is not None else ""), label))
    headers = ("HANDLE", "ID", "CATEGORY", "PUZZLES", "LABEL")
    numeric = (1, 3)  # ID and PUZZLES read as columns of numbers, so they line up on the right
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]

    def _cell(text: str, index: int) -> str:
        return text.rjust(widths[index]) if index in numeric else text.ljust(widths[index])

    print("  ".join(_cell(h, i) for i, h in enumerate(headers[:-1])) + "  " + headers[-1])
    for row in rows:
        print("  ".join(_cell(row[i], i) for i in range(len(headers) - 1)) + "  " + row[-1])


CLI_TRUE_SPELLINGS = ("true", "t", "yes", "y", "on", "1")
CLI_FALSE_SPELLINGS = ("false", "f", "no", "n", "off", "0")


def _cli_bool(raw: str) -> bool:
    """argparse `type` for a boolean a user typed, accepting the spellings people reach for.

       Raises ArgumentTypeError rather than returning None for a bad value, so argparse reports it
       with the usual usage line instead of the command failing later."""
    lowered = raw.strip().casefold()
    if lowered in CLI_TRUE_SPELLINGS:
        return True
    if lowered in CLI_FALSE_SPELLINGS:
        return False
    raise argparse.ArgumentTypeError(
            f"expected true or false, got {raw!r} (accepted: "
            f"{', '.join((*CLI_TRUE_SPELLINGS, *CLI_FALSE_SPELLINGS))})")


def _add_cli_bool_argument(parser: argparse.ArgumentParser, field: str) -> None:
    """Add the optional true/false VALUE positional the boolean `set` subcommands share."""
    parser.add_argument("value", type=_cli_bool, nargs="?", default=None, metavar="VALUE",
                        help=f"Whether {field} is on. Accepts true/false, yes/no, on/off, 1/0. "
                             "Omit to print the current setting.")


class CgCli(CliBase):
    """Command-line interface for the contribution manager."""

    _client: CgClient | None = None
    _client_authenticated: bool = False
    _client_validated: bool = False
    _console: Console | None = None
    _resolved_config: CgConfig | None = None
    _resolved_settings: CgSettings | None = None
    
    @property
    def console(self) -> Console:
        """Return the rich console instance."""
        if self._console is None:
            raise RuntimeError("Console not initialized")
        return self._console
    
    @console.setter
    def console(self, value: Console) -> None:
        """Set the rich console instance."""
        if self._console is not None:
            raise RuntimeError("Console already initialized")
        self._console = value
        
    def get_console(self) -> Console:
        """Return the rich console instance, initializing it if necessary."""
        if self._console is None:
            self._console = Console(highlight=False)
        return self._console
        
    def _make_trace_config(self) -> aiohttp.TraceConfig:
        tc = aiohttp.TraceConfig()

        async def on_request_start(
                    session: aiohttp.ClientSession,
                    ctx: object,
                    params: aiohttp.TraceRequestStartParams
                ) -> None:
            logger.debug("HTTP --> %s %s", params.method, params.url)
            cookies = session.cookie_jar.filter_cookies(params.url)
            if cookies:
                logger.debug("HTTP cookies: %s", "; ".join(f"{k}={v.value}" for k, v in cookies.items()))

        async def on_request_headers_sent(
                    session: aiohttp.ClientSession,
                    ctx: object,
                    params: aiohttp.TraceRequestHeadersSentParams
                ) -> None:
            for k, v in params.headers.items():
                logger.debug("HTTP >  %s: %s", k, v)

        async def on_request_end(
                    session: aiohttp.ClientSession,
                    ctx: object,
                    params: aiohttp.TraceRequestEndParams
                ) -> None:
            logger.debug("HTTP <-- %s %s", params.response.status, params.response.url)
            for k, v in params.response.headers.items():
                logger.debug("HTTP <  %s: %s", k, v)

        async def on_request_exception(
                    session: aiohttp.ClientSession,
                    ctx: object,
                    params: aiohttp.TraceRequestExceptionParams
                ) -> None:
            logger.debug("HTTP ERR %s %s: %s", params.method, params.url, params.exception)

        tc.on_request_start.append(on_request_start)
        tc.on_request_headers_sent.append(on_request_headers_sent)
        tc.on_request_end.append(on_request_end)
        tc.on_request_exception.append(on_request_exception)
        return tc
    
    def get_trace_configs(self) -> list[aiohttp.TraceConfig]:
        """Return a list of aiohttp.TraceConfig instances for the client session."""
        trace_http: bool = self.args.trace_http
        return [self._make_trace_config()] if trace_http else []
    
    async def get_client(self, *, require_credentials: bool = False, validate: bool = False) -> CgClient:
        """Return the CgClient instance, initializing it if necessary.

           Credentials are always resolved and applied to the session on first use, best-effort
           (never raises if none are available)--this is "level 2" of four auth-strictness levels:

               1. No authentication at all: don't call this method for auth purposes; pass
                  `require_login=False` directly to `service_request`/etc.
               2. Authenticated API, best-effort (the default: require_credentials=False,
                  validate=False): credentials are applied if available, but nothing errors if
                  they aren't.
               3. Login required (require_credentials=True, validate=False): raises
                  CgAuthenticationError if no credentials are available. Does not check they're
                  still valid/unexpired.
               4. Validated login required (require_credentials=True, validate=True): raises if
                  no credentials are available, and separately raises if they don't pass a live
                  validation check against the server.
        """
        if self._client is None:
            profile: str | None = self.args.profile
            # resolve_default_settings() (rather than CgClient's own no-args best-effort
            # fallback) so that -c/--config actually controls the client's default-profile
            # resolution too--not just cg config/cg settings commands--and so this agrees with
            # login_helper()'s own resolution (see resolve_default_settings()'s docstring for why
            # that matters). Only attempted when actually needed (profile is None); skipping it
            # otherwise avoids a spurious FileNotFoundError from a broken --config that the
            # client wouldn't even consult in that case.
            settings = None if profile is not None else self.resolve_default_settings()
            self._client = CgClient(
                profile_name=profile,
                trace_configs=self.get_trace_configs(),
                settings=settings,
            )
        client = self._client
        if not self._client_authenticated:
            await client.authenticate()
            self._client_authenticated = True

        if require_credentials and client.credentials is None:
            raise CgAuthenticationError()

        if validate and not self._client_validated:
            await client.validate_credentials()
            self._client_validated = True

        return client

    async def get_config(self) -> CgConfig:
        """Return the resolved configuration for this invocation, resolving it lazily on first
           use (honoring the --config/-c flag) and caching the result for the rest of the process.

           Raises CgConfigNotFoundError if none can be found. Any predispatch hook or command
           handler that needs config can just call this--call order and command hierarchy depth
           don't matter, since the first caller triggers resolution and everyone after gets the
           cached value. `cg config init` never calls this (it constructs its own target path
           from scratch); `cg config where` calls `find_config_file()` directly instead, since it
           needs to report absence as normal output rather than let it raise.
        """
        if self._resolved_config is None:
            explicit: str | None = self.args.config
            self._resolved_config = resolve_config(explicit)
        return self._resolved_config

    async def get_settings(self) -> CgSettings:
        """Return the resolved settings for this invocation, resolving it lazily on first use
           (which itself lazily resolves the config via `get_config()`) and caching the result.

           Unlike `get_config()`, never raises for "not found"--a missing settings.json just
           means all-default settings (see `resolve_settings()`).
        """
        if self._resolved_settings is None:
            config = await self.get_config()
            self._resolved_settings = resolve_settings(config)
        return self._resolved_settings

    async def set_current_working_dir(self, kind: str, directory: Path) -> None:
        """Record `directory` as the active puzzle/contribution working directory.

           `kind` is `"puzzle"` or `"contribution"`. Called after import/create/activate, so that
           subsequent commands operate on what was just set up--without this, a standing
           `puzzleDir`/`contributionDir` preference would silently redirect them somewhere else.
           Stored relative to settings.json's own directory (see `relativize_settings_dir`), so the
           active directory doesn't move when `cg` is run from elsewhere.

           Uses `resolve_default_settings()`, not `get_settings()`: the strict resolver raises when
           there's no config.yaml, and this runs *after* `import`/`create` has already built the
           working directory--so a user without a config.yaml would get a fully-created directory
           followed by an error. A failed write is likewise a warning rather than an error, for the
           same reason: the real work succeeded, and the only consequence is that discovery falls
           back to the usual rules."""
        settings = self.resolve_default_settings()
        value = relativize_settings_dir(directory, settings.settings_file.parent)
        setattr(settings.raw_data, f"current_{kind}_dir", value)
        try:
            settings.save()
        except OSError as e:
            self.eprint(f"warning: could not record the active {kind} directory in "
                        f"{settings.settings_file}: {e}")

    async def clear_current_working_dir(self, kind: str, *, only_if: Path | None = None) -> Path | None:
        """Clear the active puzzle/contribution working directory, returning what it was.

           With `only_if`, clears only when the active directory is that one--so deleting some
           *other* working directory doesn't silently deactivate the one you're working on.

           Non-strict settings resolution, for the same reason as `set_current_working_dir`."""
        settings = self.resolve_default_settings()
        attribute = f"current_{kind}_dir"
        current: Path | None = getattr(settings, attribute)
        if current is None:
            return None
        if only_if is not None and current != Path(only_if).expanduser().resolve():
            return None
        setattr(settings.raw_data, attribute, None)
        settings.save()
        return current

    def resolve_default_settings(self) -> CgSettings:
        """Best-effort settings resolution: honors -c/--config, but--unlike `get_settings()`--
           never raises `CgConfigNotFoundError` if no config.yaml exists (see
           `resolve_config(allow_default=True)`).

           This exists specifically so `get_client()` and `login_helper()` resolve the effective
           default profile name (when --profile isn't given) via the exact same logic and always
           agree with each other--previously, `login_helper()` saved credentials under whatever
           `credentials.cg_credentials`'s own hardcoded default profile resolved to, while
           `get_client()` separately resolved the profile via settings/config, and the two could
           silently disagree (e.g. settings.json overriding the default profile) with the
           confusing symptom of "login succeeded but the client reports unauthenticated". `cg
           settings dump`/`cg config dump` intentionally keep using the strict
           `get_settings()`/`get_config()` instead--those exist specifically to tell the user
           "nothing configured yet", which this method must never do.

           Not cached on the CLI instance (unlike `get_config()`/`get_settings()`)--cheap to
           recompute (pure filesystem checks), and giving it its own cache would either diverge
           from `get_config()`/`get_settings()`'s cache or require unifying them despite their
           different failure semantics, both worse than just recomputing.
        """
        return resolve_settings(resolve_config(self.args.config, allow_default=True))

    def resolve_toolchain_dir(self) -> Path:
        """The per-user global directory holding user-tweakable per-language toolchain (container
           image) definitions--`<data dir>/docker`, honoring a configured `dataDir`.

           Best-effort in the same spirit as `resolve_default_settings()`: never raises if no
           config.yaml exists. Passed into the managers so a language plugin that needs a toolchain
           finds the same one regardless of which working directory it's invoked from."""
        return resolve_config(self.args.config, allow_default=True).data_dir / TOOLCHAIN_SUBDIR_NAME

    def resolve_toolchain_languages(self) -> list[str] | None:
        """Which languages the toolchain image should carry, from settings, or `None` for every
           language cg can containerize.

           Passed alongside `resolve_toolchain_dir()` at each manager construction that can reach a
           containerized build--the two travel together, and a site that opts into one wants the
           other. See `CgSettings.toolchain_languages`."""
        return self.resolve_default_settings().toolchain_languages

    def resolve_toolchain_image(self) -> str | None:
        """A prebuilt image tag to use instead of building one locally, from settings, or `None` to
           build. See `CgSettings.toolchain_image`."""
        return self.resolve_default_settings().toolchain_image

    @override
    async def ctx_exit(
               self,
               exc_type: type[BaseException] | None,
               exc_value: BaseException | None,
               traceback: TracebackType | None
            ) -> None:
        """Async context manager exit. If we opened a CgClient, close it."""
        if self._client is not None:
            try:
                await self._client.__aexit__(exc_type, exc_value, traceback)
            except Exception as e:
                self.logger.error("Error closing CgClient: %s", e)
            self._client = None

    def wrap_and_indent(self, text: str, w: int = 80, indent: int = 4) -> str:
        return textwrap.indent(textwrap.fill(text, width=w), " " * indent)
    
    def show_diff(self, expected: str, actual: str) -> None:
        console = self.get_console()
        exp_lines = expected.splitlines(keepends=True)
        act_lines = actual.splitlines(keepends=True)
        diff = list(difflib.unified_diff(exp_lines, act_lines,
                                        fromfile="expected", tofile="actual",
                                        lineterm=""))
        for line in diff:
            eol = "" if line.endswith("\n") else "\n"
            if line.startswith("+"):
                console.print("    " + line, style="green", end=eol)
            elif line.startswith("-"):
                console.print("    " + line, style="red", end=eol)
            else:
                console.print("    " + line, style="dim", end=eol)
                
    @cli_command("Compute a content hash from stdin content.")
    async def cmd_content_hash(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            content = sys.stdin.buffer.read()
            hash_value = compute_content_hash(content)
            print(hash_value)
        return handler
    
    async def login_helper(
                self,
                *,
                profile_name: str | None = None,
                manual: bool = False,
                timeout: float | None = None,
                clean: bool = False,
                force: bool = False,
                remember_me: str | None = None,
                cg_session: str | None = None,
                no_validate: bool = False,
            ) -> CgCredentials:
        """Performs the login process, either via browser or manual credentials, and returns the CgCredentials.
        
        Args:
            profile_name:       The name of the profile to use for storing credentials and browser session state.
                                Allows for multiple independent session profiles; e.g., if multiple CodinGame
                                accounts are used. If None, defaults to the default profile.
            manual:             If True, perform manual login instead of browser login.
                                Implied by presence of --remember-me or --cg-session.
            timeout:            For browser login, maximum time in seconds to wait for the user to log in.
                                If None, defaults to DEFAULT_TIMEOUT_SECS.
            clean:              For browser login, if True, erases browser session state and forces a fresh login flow
                                even if valid credentials are already cached in the browser. Defaults to False.
            force:              If True, force a login even if persistent credentials already exist. By default, login is skipped if
                                credentials already exist for the profile. Note that freshness of credentials is not checked in any case;
                                if they are expired, the client will fail to use them. Defaults to False.
            remember_me:        The rememberMe cookie value, for manual (non-browser) login. Must be provided together with --cg-session.
            cg_session:         The cgSession cookie value, for manual (non-browser) login. Must be provided together with --remember-me.
            no_validate:        If True, skip validation of the credentials after login. Defaults to False.

        """
        if profile_name is None:
            # Resolved once, up front, via the same best-effort settings/config logic
            # get_client() uses (see resolve_default_settings()'s docstring)--every use of
            # profile_name below (credential lookup, save, and the validation client
            # construction) must agree on the same concrete profile name, or credentials can be
            # saved under one profile and then looked up under another.
            profile_name = self.resolve_default_settings().default_profile
        credentials: CgCredentials | None = None
        if not force:
            # Try to get existing credentials; if they exist, just return without doing a browser or manual login.
            # Note that we consider the presence of environment variable credentials to suffice for being logged in, even
            # though they are not saved to the profile store. This is because the environment variable credentials
            # are used implicitly by the client regardless of profile--they are not persisted to the profile store.
            # If no_validate is False, we will validate the credentials after login, which will fail if they are expired, in
            # which case we will fall through to the browser or manual login flow.
            credentials = get_credentials_with_override(profile_name=profile_name)
            if credentials is not None and (
                            credentials.remember_me_cookie is None or
                            credentials.cg_session_cookie is None
                    ):
                # Incomplete credentials; treat as not logged in.
                credentials = None
            if credentials is not None and not no_validate:
                # verify that the credentials are valid by attempting to authenticate with them in a temporary
                # client session.  If they are invalid, fall through to the login flow.
                async with CgClient(
                            profile_name=profile_name,
                            trace_configs=self.get_trace_configs()
                        ) as client:
                    try:
                        await client.authenticate(
                                profile_name=profile_name, credentials=credentials,
                                require_credentials=True, validate=True,
                            )
                    except CgAuthenticationError:
                        self.logger.warning(
                                "Existing credentials for profile %r are invalid or expired; forcing login.", profile_name)
                        credentials = None
            if credentials is not None:
                self.logger.debug("Credentials already exist for this profile; skipping login.")
                return credentials
            
        if manual or remember_me is not None or cg_session is not None:
            # manual login
            if remember_me is None or cg_session is None:
                raise ValueError("Both --remember-me and --cg-session must be provided for manual login.")
            credentials = CgCredentials(
                remember_me_cookie=remember_me,
                cg_session_cookie=cg_session,
            )
            set_credentials(credentials, profile_name=profile_name)
            self.logger.info("Manual login credentials set successfully.")
            return credentials
        else:
            # browser login
            self.eprint("Starting browser login. Please finish logging in in browser window that pops up...")
            credentials = await async_cg_browser_login(
                    profile_name=profile_name,
                    clean=clean,
                    timeout=timeout,
                    save=True,
                )
            self.eprint("Logged in successfully via browser. Credentials saved.")
            return credentials

    @cli_command("Log in and save the credentials. By default, opens a browser window for the user to log in interactively.")
    async def cmd_login(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            timeout: float = self.args.timeout
            manual: bool = self.args.manual
            clean: bool = self.args.clean
            profile_name: str | None = self.args.profile
            force: bool = self.args.force
            no_validate: bool = self.args.no_validate
            remember_me: str | None = self.args.remember_me
            cg_session: str | None = self.args.cg_session
            
            _ = await self.login_helper(
                    manual=manual,
                    timeout=timeout,
                    clean=clean,
                    profile_name=profile_name,
                    force=force,
                    remember_me=remember_me,
                    cg_session=cg_session,
                )
            
            if not no_validate:
                # level 4: validated login required--confirm the just-saved credentials actually work
                await self.get_client(require_credentials=True, validate=True)
            
            # For debugging, might log credentials here, but omitting here to keep creds out of logs.
            # profile_name here is self.args.profile, possibly still None (login_helper resolves
            # its own local copy internally)--not guessing "default" avoids this message being
            # wrong when the resolved default profile is actually something else.
            resolved_profile_desc = profile_name if profile_name is not None else "<resolved default>"
            self.logger.debug(f"Login completed successfully for profile {resolved_profile_desc!r}")

        p = cmd.get_parser()
        p.add_argument(
                "--force", "-f", default=False, action="store_true",
                help="Force a login even if persistent credentials already exist. By default, login is skipped if "
                     "credentials already exist for the profile. Note that freshness of credentials is not checked in any case; "
                     "if they are expired, the client will fail to use them.",
            )
        p.add_argument(
                "--no-validate", "-q", default=False, action="store_true",
                help="Skip validation of the credentials after login.",
            )
        p.add_argument(
                "--manual", "-m", default=False, action="store_true",
                help="Perform manual login instead of browser login. Implied by presence of --remember-me or --cg-session.",
            )
        p.add_argument(
                "--remember-me", "-r", default=None,
                help="Remember me cookie value, for manual (non-browser) login.",
            )
        p.add_argument(
                "--cg-session", "-s", default=None,
                help="cgSession cookie value, for manual (non-browser) login.",
            )
        p.add_argument(
                "--clean", "-c", default=False, action="store_true",
                help="If a browser is created, force a clean browser profile and a fresh login flow. By default, the existing browser "
                     "session state is used if it exists, so that repeated logins for the same profile are generally automatic.",
            )
        p.add_argument(
                "--timeout", "-t", type=float, default=300.0, metavar="SECONDS",
                help="Maximum seconds to wait for browser login completion (default: 300).",
            )
        return handler
    
    async def logout_helper(
                self,
                *,
                profile_name: str | None = None,
                keep_browser_session: bool = False,
            ) -> None:
        """Performs the logout process, clearing the credentials and optionally the browser session state.
        
        Args:
            profile_name:       The name of the profile to use for storing credentials and browser session state.
                                Allows for multiple independent session profiles; e.g., if multiple CodinGame
                                accounts are used. If None, defaults to the default profile.
            keep_browser_session: If True, keep the existing browser session even when logging out of the profile.
                                  If the browser session is logged in, it will remain logged in and will auto-login
                                  without user authentication at the next profile login. By default, the browser
                                  session is deleted on logout, which will require a full login flow in the browser.
        """
        
        if not keep_browser_session:
            # Clear browser session state for this profile
            cg_browser_delete_session(profile_name=profile_name, delete_credentials=False)
            self.eprint("Browser session state cleared for this profile.")

        # Clear credentials from persistent store
        set_credentials(None, profile_name=profile_name)
        self.eprint("Credentials cleared from persistent store.")

    @cli_command("Log out of a given profile's session.")
    async def cmd_logout(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            profile_name: str | None = self.args.profile
            keep_browser_session: bool = self.args.keep_browser_session
            
            await self.logout_helper(
                    profile_name=profile_name,
                    keep_browser_session=keep_browser_session,
                )

            self.logger.debug(f"Logout completed successfully for profile {profile_name or 'default'!r}")

        p = cmd.get_parser()
        p.add_argument(
                "--keep-browser-session", "-k", default=False, action="store_true",
                help="Keep the existing browser session even when logging out of the profile. "
                     "If the browser session is logged in, it will remain logged in and will auto-login without user authentication "
                     "at the next profile login. By default, the browser session is deleted on logout, which will require "
                     "a full login flow in the browser.",
            )
        return handler

    @cli_command("Show the current logged-in user and other session info for the given profile.")
    async def cmd_whoami(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            use_json: bool = self.args.json
            # level 2: best-effort--report what's there rather than erroring if nothing is
            client = await self.get_client()
            profile = client.profile_name
            credentials = client.credentials
            has_credentials = credentials is not None
            codingamer_id: int | None = client.codingamer_id
            remember_me: str | None = None
            cg_session: str | None = None
            credentials_valid: bool | None = None
            if credentials is not None:
                remember_me = credentials.remember_me_cookie
                cg_session = credentials.cg_session_cookie
                try:
                    await client.validate_credentials()
                    credentials_valid = True
                except CgAuthenticationError:
                    credentials_valid = False
            if use_json:
                output = {
                    "profile": profile,
                    "hasCredentials": has_credentials,
                    "codingamerId": codingamer_id,
                    "credentialsValid": credentials_valid,
                    "rememberMe": remember_me,
                    "cgSession": cg_session,
                }
                print(json.dumps(output, indent=4, sort_keys=True))
            else:
                print(f"Profile: {profile}")
                print(f"Has credentials: {has_credentials}")
                print(f"Codingamer ID: {codingamer_id}")
                if has_credentials:
                    print(f"Credentials valid: {credentials_valid}")
                    print(f"rememberMe cookie: {remember_me}")
                    print(f"cgSession cookie: {cg_session}")


        return handler

    @cli_command("Summarize the current session: login status, profile details, and points/rank "
                 "stats for the logged-in codingamer. Always hits the network--there's no "
                 "cached/local mode, unlike `cg contribution status`/`cg puzzle status` (that's "
                 "the whole point of this command). \"Gamer stats\" are informational, not a "
                 "breakdown of one another--see CgCodingamePointsRankingDto's docstring for why. "
                 "With --json (top-level option), renders as JSON instead of text.")
    async def cmd_status(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            use_json: bool = self.args.json
            client = await self.get_client()
            profile = client.profile_name
            credentials = client.credentials
            has_credentials = credentials is not None
            codingamer_id: int | None = client.codingamer_id
            credentials_valid: bool | None = None
            if credentials is not None:
                try:
                    await client.validate_credentials()
                    credentials_valid = True
                except CgAuthenticationError:
                    credentials_valid = False

            stats: CgCodingamePointsStats | None = None
            if codingamer_id is not None and credentials_valid:
                info = await client.services.codingamer.find_codingamer_public_informations(codingamer_id)
                stats = await client.services.codingamer.find_codingame_points_stats_by_handle(info.public_handle)

            if use_json:
                stats_dict: JsonData | None = None
                if stats is not None:
                    stats_dict = stats.to_dict()
                    # rank_history can be thousands of dated snapshots (years of history)--not
                    # appropriate for a "status" summary; strip it for display (same spirit as
                    # redacting bulky content elsewhere--see `cg contribution status`).
                    ranking_dict = stats_dict.get("codingamePointsRankingDto")
                    if isinstance(ranking_dict, dict):
                        ranking_dict.pop("rankHistory", None)
                output: JsonData = {
                    "profile": profile,
                    "hasCredentials": has_credentials,
                    "credentialsValid": credentials_valid,
                    "codingamerId": codingamer_id,
                    "stats": stats_dict,
                }
                print(json.dumps(output, indent=4, sort_keys=True))
                return

            def line(label: str, value: object) -> None:
                print(f"{label:<30}{value}")

            line("Profile:", profile)
            if credentials_valid:
                line("Logged in:", "yes")
            elif has_credentials:
                line("Logged in:", "no (saved credentials are no longer valid--run `cg login`)")
            else:
                line("Logged in:", "no (no saved credentials--run `cg login`)")
            line("Codingamer id:", codingamer_id if codingamer_id is not None else "(unknown)")
            if stats is None:
                return

            codingamer = stats.codingamer
            print()
            line("Handle:", codingamer.public_handle)
            line("Nickname:", codingamer.pseudo or "(not set)")
            line("Level:", codingamer.level if codingamer.level is not None else "(unknown)")
            line("Country:", codingamer.country_id or "(not set)")
            if codingamer.company:
                line("Company:", codingamer.company)
            if codingamer.online_since is not None:
                line("Online since:", _isoformat_z(codingamer.online_since))

            ranking = stats.codingame_points_ranking_dto
            print()
            print("Gamer stats:")
            line("  Points total:", ranking.codingame_points_total)
            line("  Global rank:", f"{ranking.codingame_points_rank} / {ranking.number_codingamers_global}")
            line("  XP:", _format_xp_progress(codingamer.xp, codingamer.level, stats.xp_thresholds))
            line("  Achievements:", ranking.codingame_points_achievements)
            line("  Contests:", ranking.codingame_points_contests)
            line("  Optimization puzzles:", ranking.codingame_points_optim)
            line("  Code golf puzzles:", ranking.codingame_points_codegolf)
            line("  Multiplayer training:", ranking.codingame_points_multi_training)
            line("  Clash of Code:", ranking.codingame_points_clash)

            print()
            line("Achievements unlocked:", stats.achievement_count)
        return handler

    @cli_command("Open the documentation for this version of cg in a dedicated browser window. "
                 "The published site keeps every release side by side, so this opens the directory "
                 "matching the cg you are actually running rather than whatever is newest. Inside a "
                 "source checkout it serves that tree's own docs instead--including uncommitted "
                 "edits--and stops the server when the window closes. With --url, prints the "
                 "address and exits, which is what to use over SSH or anywhere a window cannot "
                 "open.")
    async def cmd_doc(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            want_url_only: bool = self.args.url
            windowed: bool = self.args.windowed
            online: bool = self.args.online
            rebuild: bool = not self.args.no_rebuild
            version: str | None = self.args.doc_version

            checkout = None if (online or version is not None) else find_source_checkout()
            if checkout is None:
                url = published_docs_url(version)
                self.logger.debug(f"Using published documentation at {url}")
                if want_url_only:
                    print(url)
                    return
                await open_window_and_wait(url, app_window=not windowed,
                                           on_ready=f"Showing {url} -- close the window when done.")
                return

            # A checkout's own docs beat the published site for the same reason `pip install -e`
            # exists: they describe the code in front of you, not the code that was last released.
            self.logger.debug(f"Serving documentation from the source checkout at {checkout}")
            try:
                # The cache, never the checkout: `cg doc` is something a user runs, not
                # a contributor tool operating on a tree they are working in.
                server = start_local_docs(
                        checkout, mode="build" if rebuild else "existing",
                        output=docs_cache_dir(checkout))
            except LocalDocsError as e:
                raise CliError(f"cannot serve local documentation: {e}") from e
            try:
                if want_url_only:
                    # Nothing would keep the server alive after printing, so this is a real choice
                    # between lying and refusing. Point at the published site, which does persist.
                    server.stop()
                    print(published_docs_url(version))
                    return
                await server.wait_until_ready()
                await open_window_and_wait(
                        server.url, app_window=not windowed,
                        on_ready=f"Showing {server.url} (from {checkout}) -- "
                                 "close the window to stop the server.")
            finally:
                server.stop()

        p = cmd.get_parser()
        p.add_argument(
                "--url", default=False, action="store_true",
                help="Print the documentation URL and exit instead of opening a window. Use this "
                     "over SSH, in a container, or anywhere a browser cannot open. In a source "
                     "checkout this prints the published URL, since a local server would stop the "
                     "moment this command returned.",
            )
        p.add_argument(
                "--online", default=False, action="store_true",
                help="Use the published site even inside a source checkout.",
            )
        p.add_argument(
                "--version", dest="doc_version", default=None, metavar="VERSION",
                help="Show the documentation for a specific cg version (e.g. 2.0.1) instead of the "
                     "installed one. Implies --online, since only the published site has other "
                     "versions.",
            )
        p.add_argument(
                "--no-rebuild", default=False, action="store_true",
                help="In a source checkout, serve the existing site/ build instead of building "
                     "first. Much faster to open, but shows the docs as of the last cg doc run. "
                     "Both build into and read from a per-user cache directory, never the "
                     "checkout, so this serves exactly what the previous run showed. Ignored "
                     "when using the published site.",
            )
        p.add_argument(
                "--windowed", default=False, action="store_true",
                help="Open an ordinary browser window with an address bar, instead of a chrome-less "
                     "app window. Use this if the app window misbehaves.",
            )
        return handler

    @cli_command("Raw (unstructured JSON) API commands.")
    async def cmd_raw_api(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Invoke a raw API request on a service endpoint. stdin must be a json-encoded list of args.")
    async def cmd_raw_api__service_request(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            service_name: str = self.args.service_name
            func_name: str = self.args.func_name
            data: str | None = self.args.req_args
            # level 2: attach credentials if available, but don't require them--this is a raw/low-level
            # tool that should also work against genuinely public endpoints without being logged in.
            client = await self.get_client()
            if data is None:
               data = cast(str, sys.stdin.read())
            json_list: JsonList = cast(JsonList,json.loads(data))
            if not isinstance(json_list, list):
                raise ValueError("Input JSON must be a list of arguments.")
            response: JsonData = await client.service_request(
                    service_name=service_name,
                    func_name=func_name,
                    args=json_list,
                    require_login=False,
                )
            print(json.dumps(response, indent=2, sort_keys=True))

        p = cmd.get_parser()
        p.add_argument("service_name", type=str, metavar="SERVICE-NAME",
                       help="Service name; e.g., 'CodingamerService'.")
        p.add_argument("func_name", type=str, metavar="FUNC-NAME",
                       help="Endpoint name; e.g., 'getCodingamer'.")
        p.add_argument("--req-args", "-a", type=str, default=None, metavar="JSON-ARGS",
                       help="Optional JSON-encoded list to send as the request arg. If not provided, stdin is read for the "
                            "JSON-encoded list of args.")
        return handler

    
    @cli_command("Low-level API commands.")
    async def cmd_api(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Download a file by server object ID (the fileservlet servlet).")
    async def cmd_api__file_servlet(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            file_id: int = self.args.file_id
            timestamp: datetime | None = self.args.timestamp
            format: str | None = self.args.format
            self.eprint(f"Downloading file with ID: {file_id}")
            # level 2: not every file requires a login--attach credentials if available and let
            # the server decide (401/403) whether this particular file actually needs them.
            client = await self.get_client()
            file_info: CgDownloadFileResult = await client.servlets.file_servlet(
                    file_id,
                    format=format,
                    timestamp=timestamp,
                    require_login=False,
                )
            self.eprint(
                    f"Fetched file: {file_info.filename!r}; content-type={file_info.content_type!r}, "
                    f"size={len(file_info.content)} bytes, hash={file_info.hash!r}"
                )
            if sys.stderr.isatty():
                self.eprint("Omitting file content because stdout is a terminal. Redirect stdout to a file or pipe to see the content.")
                return
            self.get_binary_stdout().write(file_info.content)
        p = cmd.get_parser()
        p.add_argument("file_id", type=int, metavar="ID",
                       help="Server file ID number.")
        p.add_argument("--format", type=str, default=None,
                       help="Optional format string to append to the URL as a query parameter; e.g., 'puzzle_tile'.")
        p.add_argument("--timestamp", type=parse_timestamp, default=None, metavar="TIMESTAMP",
                       help="Optional timestamp. Can be milliseconds since epoch (e.g., '1680000000000'),"
                            " a duration string (e.g., '1h30m'), a relative duration from now (e.g., '-1h30m'),"
                            " or an ISO 8601 datetime string.")
        return handler

    @cli_command("Upload a file from stdin (the fileupload servlet).")
    async def cmd_api__file_upload(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            filename: str = self.args.filename
            content_type: str = self.args.content_type
            prev_id: int | None = self.args.prev_id
            prev_content_hash: str | None = self.args.prev_content_hash
            client = await self.get_client(require_credentials=True)
            content = self.get_binary_stdin().read()
            content_hash = compute_content_hash(content)
            self.eprint(
                    f"Uploading file with filename={filename!r}, content-type={content_type!r}, "
                    f"size={len(content)} bytes, hash={content_hash!r}")
            file_changed = prev_id is None or prev_content_hash is None or prev_content_hash != content_hash
            if not file_changed:
                self.eprint("Content hash matches previous content hash; skipping upload.")
                print(str(prev_id))
                return
            self.eprint("Content hash differs from previous content hash; proceeding with upload.")
            result = await client.servlets.file_upload(
                    content,
                    filename=filename,
                    content_type=content_type,
                )
            print(str(result.id))
        p = cmd.get_parser()
        p.add_argument("--filename", type=str, default="data.bin",
                       help="Optional filename provided to the server for the uploaded file; e.g., 'cover.png'.")
        p.add_argument("--content-type", type=str, default="application/octet-stream",
                       help="Optional content type for the uploaded file; e.g., 'application/octet-stream'.")
        p.add_argument("--prev-id", type=int, default=None,
                       help="Optional previous file ID for the uploaded file; e.g., 12345.")
        p.add_argument("--prev-content-hash", type=str, default=None,
                       help="Optional previous content hash for the uploaded file.")
        return handler

    @cli_command("Notification service commands.")
    async def cmd_api__notification(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Find unread notifications for a codingamer.")
    async def cmd_api__notification__find_unread_notifications(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            codingamer_id: int | None = self.args.codingamer_id
            # level 2 here is enough: notification_find_unread_notifications() enforces its own
            # login requirement (the endpoint always needs a valid session) and resolves the
            # default codingamer_id itself.
            client = await self.get_client()
            notifications = await client.services.notification.find_unread_notifications(codingamer_id)
            print(json.dumps([n.to_dict() for n in notifications], indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Codingamer ID to find unread notifications for. Defaults to the logged-in codingamer's ID.")
        return handler

    @cli_command("Contribution service commands.")
    async def cmd_api__contribution(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Find a contribution by its opaque contribution ID.")
    async def cmd_api__contribution__find_contribution(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_id: str = self.args.contribution_id
            arg2: bool = not self.args.arg2_false
            client = await self.get_client()
            contribution = await client.services.contribution.find_contribution(contribution_id, arg2)
            print(json.dumps(contribution.to_dict(), indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("contribution_id", type=str, metavar="CONTRIBUTION-ID",
                       help="Opaque contribution ID string.")
        p.add_argument("--arg2-false", default=False, action="store_true",
                       help="Set the API's second (purpose unknown) argument to False instead of the default True.")
        return handler

    @cli_command("Count new contributions published since a given point in time.")
    async def cmd_api__contribution__find_new_contribution_count(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            codingamer_id: int | None = self.args.codingamer_id
            since: datetime | None = self.args.since
            client = await self.get_client()
            count = await client.services.contribution.find_new_contribution_count(codingamer_id, since)
            print(json.dumps(count))
        p = cmd.get_parser()
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Codingamer to count new contributions for. Defaults to the logged-in codingamer's ID.")
        p.add_argument("--since", type=parse_timestamp, default=None, metavar="TIMESTAMP",
                       help="Count contributions published after this point in time. Can be milliseconds "
                            "since epoch (e.g., '1680000000000'), a duration string (e.g., '1h30m'), a relative "
                            "duration from now (e.g., '-1h30m'), or an ISO 8601 datetime string. Defaults to now.")
        return handler

    @cli_command("List the moderators who have cast a given vote ('validate'/'deny') on a "
                 "PENDING contribution's approve/reject moderation gate--the privileged gate "
                 "that actually decides whether it gets published or rejected (3 votes either "
                 "way, confirmed live). Distinct from the ungated community vote (`cg api vote "
                 "find-votable-values-by-id`)--do not conflate the two.")
    async def cmd_api__contribution__find_contribution_moderators(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_numeric_id: int = self.args.contribution_numeric_id
            action: str = self.args.action
            client = await self.get_client()
            moderators = await client.services.contribution.find_contribution_moderators(contribution_numeric_id, action)
            print(json.dumps([m.to_dict() for m in moderators], indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("contribution_numeric_id", type=int, metavar="CONTRIBUTION-NUMERIC-ID",
                       help="The contribution's *numeric* ID (CgContribution.id)--NOT the opaque "
                            "public handle used by every other `cg api contribution` command.")
        p.add_argument("action", type=str, choices=["validate", "deny"], metavar="ACTION",
                       help="'validate' (approve) or 'deny' (reject).")
        return handler

    @cli_command("Get pending (community-review-queue) contributions.")
    async def cmd_api__contribution__get_all_pending_contributions(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_type_filter: str = self.args.type_filter
            codingamer_id: int | None = self.args.codingamer_id
            page: int = self.args.page
            client = await self.get_client()
            contributions = await client.services.contribution.get_all_pending_contributions(
                    contribution_type_filter, codingamer_id, page)
            print(json.dumps([c.to_dict() for c in contributions], indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("--type-filter", "-t", type=str, default="ALL", metavar="FILTER",
                       help="Category filter: 'ALL', 'CLASHOFCODE', or 'PUZZLE'. Defaults to 'ALL'.")
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Must equal the logged-in codingamer's own ID (server-enforced). "
                            "Defaults to the logged-in codingamer's ID.")
        p.add_argument("--page", "-n", type=int, default=1, metavar="PAGE",
                       help="Assumed 1-indexed page number; unconfirmed. Defaults to 1.")
        return handler

    @cli_command("List every contribution (any status--draft/PENDING/APPROVED/REFUSED/etc.) "
                 "authored by a codingamer. Unlike `get-all-pending-contributions`, this "
                 "genuinely filters to just that codingamer's own contributions.")
    async def cmd_api__contribution__get_personal_contributions(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            codingamer_id: int | None = self.args.codingamer_id
            page: int = self.args.page
            client = await self.get_client()
            contributions = await client.services.contribution.get_personal_contributions(codingamer_id, page)
            print(json.dumps([c.to_dict() for c in contributions], indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Must equal the logged-in codingamer's own ID (server-enforced). "
                            "Defaults to the logged-in codingamer's ID.")
        p.add_argument("--page", "-n", type=int, default=1, metavar="PAGE",
                       help="1-indexed page number (confirmed live via the server's own "
                            "INVALID_PAGE error detail). Defaults to 1.")
        return handler

    @cli_command("Submit a new version of a contribution's content. A JSON-serialized "
                 "CgContributionData object is read from stdin.")
    async def cmd_api__contribution__update_contribution(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_id: str = self.args.contribution_id
            puzzle_type: str = self.args.puzzle_type
            prev_version: int = self.args.prev_version
            draft: bool = self.args.draft
            ready_for_moderation: bool = self.args.ready_for_moderation
            codingamer_id: int | None = self.args.codingamer_id
            contribution_data = CgContributionData.loads(sys.stdin.read())
            client = await self.get_client()
            contribution = await client.services.contribution.update_contribution(
                    contribution_id, puzzle_type, contribution_data, draft, ready_for_moderation,
                    prev_version, codingamer_id)
            print(json.dumps(contribution.to_dict(), indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("contribution_id", type=str, metavar="CONTRIBUTION-ID",
                       help="Opaque contribution ID string.")
        p.add_argument("puzzle_type", type=str, metavar="PUZZLE-TYPE",
                       help="The type of the contribution, e.g. 'PUZZLE_INOUT'.")
        p.add_argument("prev_version", type=int, metavar="PREV-VERSION",
                       help="The contribution's current version number, as last retrieved via find-contribution "
                            "(an idempotency/concurrency check--rejected if stale).")
        p.add_argument("--draft", default=False, action="store_true",
                       help="Submit as a private, unpublished draft. Defaults to false.")
        p.add_argument("--ready-for-moderation", default=False, action="store_true",
                       help="Formally submit for moderation. Defaults to false.")
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="The authoring codingamer's numeric ID. Defaults to the logged-in codingamer's ID.")
        return handler

    @cli_command("Create a brand new contribution. A JSON-serialized CgContributionData object "
                 "is read from stdin.")
    async def cmd_api__contribution__create_contribution(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            puzzle_type: str = self.args.puzzle_type
            draft: bool = self.args.draft
            ready_for_moderation: bool = self.args.ready_for_moderation
            codingamer_id: int | None = self.args.codingamer_id
            contribution_data = CgContributionData.loads(sys.stdin.read())
            client = await self.get_client()
            handle = await client.services.contribution.create_contribution(
                    puzzle_type, contribution_data, draft, ready_for_moderation, codingamer_id)
            print(json.dumps(handle))
        p = cmd.get_parser()
        p.add_argument("puzzle_type", type=str, metavar="PUZZLE-TYPE",
                       help="The type of the contribution, e.g. 'PUZZLE_INOUT'.")
        p.add_argument("--draft", default=False, action="store_true",
                       help="Create as a private, unpublished draft. Defaults to false.")
        p.add_argument("--ready-for-moderation", default=False, action="store_true",
                       help="Formally submit for moderation. Defaults to false.")
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="The authoring codingamer's numeric ID. Defaults to the logged-in codingamer's ID.")
        return handler

    @cli_command("Delete a contribution.")
    async def cmd_api__contribution__delete_contribution(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_id: str = self.args.contribution_id
            codingamer_id: int | None = self.args.codingamer_id
            client = await self.get_client()
            result = await client.services.contribution.delete_contribution(contribution_id, codingamer_id)
            print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("contribution_id", type=str, metavar="CONTRIBUTION-ID",
                       help="Opaque contribution ID string of the contribution to delete.")
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="The authoring codingamer's numeric ID. Defaults to the logged-in codingamer's ID.")
        return handler

    @cli_command("ClashOfCode service commands.")
    @cli_command("ClashOfCode service commands.")
    async def cmd_api__clash_of_code(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Get a codingamer's global Clash of Code ranking.")
    async def cmd_api__clash_of_code__get_clash_rank_by_codingamer_id(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            codingamer_id: int | None = self.args.codingamer_id
            client = await self.get_client()
            clash_rank = await client.services.clash_of_code.get_clash_rank_by_codingamer_id(codingamer_id)
            print(json.dumps(clash_rank.to_dict() if clash_rank is not None else None, indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Codingamer numeric ID. Defaults to the logged-in codingamer's ID.")
        return handler

    @cli_command("Find a Clash of Code session by its handle.")
    async def cmd_api__clash_of_code__find_clash_by_handle(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            handle: str = self.args.handle
            client = await self.get_client()
            clash = await client.services.clash_of_code.find_clash_by_handle(handle)
            print(json.dumps(clash.to_dict(), indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("handle", type=str, metavar="HANDLE",
                       help="Opaque clash-instance handle string (a per-slot handle from "
                            "'api featured-event find-clash-slots'; not a codingamer handle or the "
                            "parent featured event's own handle--both are rejected by the server).")
        return handler

    @cli_command("ClashOfCodeDescription service commands.")
    async def cmd_api__clash_of_code_description(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Get localized help/explainer content for Clash of Code.")
    async def cmd_api__clash_of_code_description__get_clash_description(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            client = await self.get_client()
            description = await client.services.clash_of_code_description.get_clash_description()
            print(json.dumps(description.to_dict(), indent=2, sort_keys=True))
        return handler

    @cli_command("FeaturedEvent service commands.")
    async def cmd_api__featured_event(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Find upcoming and ongoing site-wide featured events.")
    async def cmd_api__featured_event__find_upcoming_and_ongoing_featured_events(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            codingamer_id: int | None = self.args.codingamer_id
            client = await self.get_client()
            events = await client.services.featured_event.find_upcoming_and_ongoing_featured_events(codingamer_id)
            print(json.dumps([e.to_dict() for e in events], indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Codingamer to check registration status for. Defaults to the logged-in codingamer's ID.")
        return handler

    @cli_command("Check whether a codingamer is auto-registered for featured events.")
    async def cmd_api__featured_event__is_codingamer_auto_registered(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            codingamer_id: int | None = self.args.codingamer_id
            client = await self.get_client()
            auto_registered = await client.services.featured_event.is_codingamer_auto_registered(codingamer_id)
            print(json.dumps(auto_registered))
        p = cmd.get_parser()
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Codingamer to check. Must be the logged-in codingamer's own ID "
                            "(server-enforced). Defaults to the logged-in codingamer's ID.")
        return handler

    @cli_command("Count featured events published since a given point in time.")
    async def cmd_api__featured_event__find_new_featured_event_count(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            since: datetime | None = self.args.since
            client = await self.get_client()
            count = await client.services.featured_event.find_new_featured_event_count(since)
            print(json.dumps(count))
        p = cmd.get_parser()
        p.add_argument("--since", type=parse_timestamp, default=None, metavar="TIMESTAMP",
                       help="Count featured events published after this point in time. Can be milliseconds "
                            "since epoch (e.g., '1680000000000'), a duration string (e.g., '1h30m'), a relative "
                            "duration from now (e.g., '-1h30m'), or an ISO 8601 datetime string. Defaults to now.")
        return handler

    @cli_command("Find the individual scheduled Clash of Code slots belonging to a featured event.")
    async def cmd_api__featured_event__find_clash_slots(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            featured_event_id: int = self.args.featured_event_id
            client = await self.get_client()
            slots = await client.services.featured_event.find_clash_slots(featured_event_id)
            print(json.dumps([s.to_dict() for s in slots], indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("featured_event_id", type=int, metavar="FEATURED-EVENT-ID",
                       help="The numeric 'id' of a CLASH_OF_CODE-type featured event (not its 'handle').")
        return handler

    @cli_command("Find a featured event by its opaque handle.")
    async def cmd_api__featured_event__find_by_handle(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            handle: str = self.args.handle
            client = await self.get_client()
            event = await client.services.featured_event.find_by_handle(handle)
            print(json.dumps(event.to_dict(), indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("handle", type=str, metavar="HANDLE",
                       help="Opaque featured event handle string.")
        return handler

    @cli_command("CodingamerPuzzleTopic service commands.")
    async def cmd_api__codingamer_puzzle_topic(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Find the puzzle topics a codingamer has made progress on.")
    async def cmd_api__codingamer_puzzle_topic__find_topics_by_codingamer_id(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            codingamer_id: int | None = self.args.codingamer_id
            client = await self.get_client()
            topics = await client.services.codingamer_puzzle_topic.find_topics_by_codingamer_id(codingamer_id)
            print(json.dumps([t.to_dict() for t in topics], indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Codingamer whose puzzle topic progress to list. Defaults to the logged-in codingamer's ID.")
        return handler

    @cli_command("Find the topic tree for a single puzzle, personalized with the codingamer's per-topic learned status.")
    async def cmd_api__codingamer_puzzle_topic__select_topics_by_codingamer_id_and_puzzle_id(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            puzzle_id: int = self.args.puzzle_id
            codingamer_id: int | None = self.args.codingamer_id
            client = await self.get_client()
            topics = await client.services.codingamer_puzzle_topic.select_topics_by_codingamer_id_and_puzzle_id(
                    puzzle_id, codingamer_id)
            print(json.dumps([t.to_dict() for t in topics], indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("puzzle_id", type=int, metavar="PUZZLE-ID",
                       help="Numeric ID of the puzzle.")
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Codingamer whose topic mastery to check. Defaults to the logged-in codingamer's ID.")
        return handler

    @cli_command("Puzzle service commands.")
    async def cmd_api__puzzle(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Count a codingamer's solved puzzles, broken down by programming language.")
    async def cmd_api__puzzle__count_solved_puzzles_by_programming_language(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            codingamer_id: int | None = self.args.codingamer_id
            client = await self.get_client()
            counts = await client.services.puzzle.count_solved_puzzles_by_programming_language(codingamer_id)
            print(json.dumps([c.to_dict() for c in counts], indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Codingamer whose solved-puzzle counts to list. Defaults to the logged-in codingamer's ID.")
        return handler

    @cli_command("Find the current puzzle of the week.")
    async def cmd_api__puzzle__find_puzzle_of_the_week(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            client = await self.get_client()
            puzzle = await client.services.puzzle.find_puzzle_of_the_week()
            print(json.dumps(puzzle.to_dict(), indent=2, sort_keys=True))
        return handler

    @cli_command("Find a codingamer's minimal progress summary for every puzzle they have some relationship to.")
    async def cmd_api__puzzle__find_all_minimal_progress(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            codingamer_id: int | None = self.args.codingamer_id
            client = await self.get_client()
            progress = await client.services.puzzle.find_all_minimal_progress(codingamer_id)
            print(json.dumps([p.to_dict() for p in progress], indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Codingamer whose puzzle progress to list. Defaults to the logged-in codingamer's ID.")
        return handler

    @cli_command("Find a codingamer's progress summary for a specific set of puzzles, by puzzle ID.")
    async def cmd_api__puzzle__find_progress_by_ids(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            puzzle_ids: list[int] = self.args.puzzle_ids
            codingamer_id: int | None = self.args.codingamer_id
            arg3: int = self.args.arg3
            client = await self.get_client()
            progress = await client.services.puzzle.find_progress_by_ids(puzzle_ids, codingamer_id, arg3)
            print(json.dumps([p.to_dict() for p in progress], indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("puzzle_ids", type=int, nargs="+", metavar="PUZZLE-ID",
                       help="One or more numeric puzzle IDs to look up.")
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Codingamer whose progress to look up. Defaults to the logged-in codingamer's ID.")
        p.add_argument("--arg3", type=int, default=2, metavar="N",
                       help="Third (purpose unclear) argument to the underlying API call. Defaults to 2.")
        return handler

    @cli_command("Find the best progress on a given puzzle among the codingamers a codingamer follows.")
    async def cmd_api__puzzle__find_best_following_progress(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            puzzle_id: int = self.args.puzzle_id
            codingamer_id: int | None = self.args.codingamer_id
            client = await self.get_client()
            progress = await client.services.puzzle.find_best_following_progress(puzzle_id, codingamer_id)
            print(json.dumps([p.to_dict() for p in progress], indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("puzzle_id", type=int, metavar="PUZZLE-ID",
                       help="Numeric ID of the puzzle to check.")
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Codingamer whose followees to check. Defaults to the logged-in codingamer's ID.")
        return handler

    @cli_command("Find a codingamer's progress summary for a single puzzle, by its pretty ID.")
    async def cmd_api__puzzle__find_progress_by_pretty_id(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            pretty_id: str = self.args.pretty_id
            codingamer_id: int | None = self.args.codingamer_id
            client = await self.get_client()
            puzzle = await client.services.puzzle.find_progress_by_pretty_id(pretty_id, codingamer_id)
            print(json.dumps(puzzle.to_dict(), indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("pretty_id", type=str, metavar="PRETTY-ID",
                       help="The puzzle's pretty ID: displayed title, lowercased with spaces replaced by "
                            "hyphens, e.g. 'literary-alfabet-soupe'.")
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Codingamer whose progress to look up. Defaults to the logged-in codingamer's ID.")
        return handler

    @cli_command("Get (or create) the codingamer's test session handle for a puzzle, by its "
                 "pretty ID. Confirmed to return the same handle across repeated calls (a "
                 "per-user singleton test session)--use `cg api test-session start-test-session` "
                 "on the result to get the full session/question/answer details.")
    async def cmd_api__puzzle__generate_session_from_puzzle_pretty_id(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            puzzle_pretty_id: str = self.args.puzzle_pretty_id
            codingamer_id: int | None = self.args.codingamer_id
            client = await self.get_client()
            handle = await client.services.puzzle.generate_session_from_puzzle_pretty_id(
                    puzzle_pretty_id, codingamer_id)
            print(json.dumps(handle))
        p = cmd.get_parser()
        p.add_argument("puzzle_pretty_id", type=str, metavar="PUZZLE-PRETTY-ID",
                       help="The puzzle's pretty ID: displayed title, lowercased with spaces replaced by "
                            "hyphens, e.g. 'literary-alfabet-soupe'.")
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Codingamer to get/create the session for. Defaults to the logged-in codingamer's ID.")
        return handler

    @cli_command("LastActivities service commands.")
    async def cmd_api__last_activities(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Get a codingamer's most recent activity feed entries.")
    async def cmd_api__last_activities__get_last_activities(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            codingamer_id: int | None = self.args.codingamer_id
            limit: int = self.args.limit
            client = await self.get_client()
            activities = await client.services.last_activities.get_last_activities(codingamer_id, limit)
            print(json.dumps([a.to_dict() for a in activities], indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Codingamer whose recent activity to list. Defaults to the logged-in codingamer's ID.")
        p.add_argument("--limit", "-n", type=int, default=4, metavar="N",
                       help="Maximum number of activity entries to return. Defaults to 4.")
        return handler

    @cli_command("Quest service commands.")
    async def cmd_api__quest(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Find a codingamer's quest map.")
    async def cmd_api__quest__find_quest_map(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            codingamer_id: int | None = self.args.codingamer_id
            client = await self.get_client()
            quest_map = await client.services.quest.find_quest_map(codingamer_id)
            print(json.dumps(quest_map.to_dict(), indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Codingamer whose quest map to fetch. Defaults to the logged-in codingamer's ID.")
        return handler

    @cli_command("Count a codingamer's completed-but-unclaimed (lootable) quests.")
    async def cmd_api__quest__count_lootable_quests(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            codingamer_id: int | None = self.args.codingamer_id
            client = await self.get_client()
            count = await client.services.quest.count_lootable_quests(codingamer_id)
            print(json.dumps(count))
        p = cmd.get_parser()
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Codingamer to count lootable quests for. Defaults to the logged-in codingamer's ID.")
        return handler

    @cli_command("Intercom service commands.")
    async def cmd_api__intercom(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Generate an Intercom identity-verification JWT for the logged-in codingamer.")
    async def cmd_api__intercom__generate_token(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            client = await self.get_client()
            token = await client.services.intercom.generate_token()
            print(json.dumps(token))
        return handler

    @cli_command("Survey service commands.")
    async def cmd_api__survey(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Find a survey to potentially show a codingamer (UNVERIFIED--response shape unconfirmed).")
    async def cmd_api__survey__find_survey(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            codingamer_id: int | None = self.args.codingamer_id
            limit: int = self.args.limit
            client = await self.get_client()
            survey = await client.services.survey.find_survey(codingamer_id, limit)
            print(json.dumps(survey.to_dict() if survey is not None else None, indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Codingamer to find a survey for. Defaults to the logged-in codingamer's ID.")
        p.add_argument("--limit", "-n", type=int, default=2, metavar="N",
                       help="Assumed maximum number of results; unconfirmed. Defaults to 2.")
        return handler

    @cli_command("Topic service commands.")
    async def cmd_api__topic(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("List every puzzle topic a contribution can be tagged with, each with the number "
                 "of published puzzles carrying it. Takes no arguments. See `cg topics` for the "
                 "same data as a searchable table, and `cg contribution topic` to tag with it.")
    async def cmd_api__topic__get_all_children_topics_with_puzzle_count(
                self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            client = await self.get_client()
            topics = await client.services.topic.get_all_children_topics_with_puzzle_count()
            print(json.dumps([t.to_dict() for t in topics], indent=2, sort_keys=True))
        return handler

    @cli_command("Achievement service commands.")
    async def cmd_api__achievement(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Find the achievements a codingamer has unlocked.")
    async def cmd_api__achievement__find_by_codingamer_id(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            codingamer_id: int | None = self.args.codingamer_id
            client = await self.get_client()
            achievements = await client.services.achievement.find_by_codingamer_id(codingamer_id)
            print(json.dumps([a.to_dict() for a in achievements], indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Codingamer whose achievements to list. Defaults to the logged-in codingamer's ID.")
        return handler

    @cli_command("User service commands.")
    async def cmd_api__user(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Update a subset of a codingamer's account properties.")
    async def cmd_api__user__update_user_properties(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            codingamer_id: int | None = self.args.codingamer_id
            contributions_list_last_visit: datetime | None = self.args.contributions_list_last_visit
            properties = CgUserProperties()
            if contributions_list_last_visit is not None:
                properties.contributions_list_last_visit = contributions_list_last_visit
            client = await self.get_client()
            await client.services.user.update_user_properties(properties, codingamer_id)
        p = cmd.get_parser()
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Codingamer to update. Defaults to the logged-in codingamer's ID.")
        p.add_argument("--contributions-list-last-visit", type=parse_timestamp, default=None, metavar="TIMESTAMP",
                       help="Set the codingamer's last-visit time for their contributions list. Can be "
                            "milliseconds since epoch, a duration string (e.g., '1h30m'), a relative duration "
                            "from now (e.g., '-1h30m'), or an ISO 8601 datetime string.")
        return handler

    @cli_command("TestSession service commands.")
    async def cmd_api__test_session(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Start (or resume) an interactive IDE test session for a puzzle.")
    async def cmd_api__test_session__start_test_session(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            test_session_handle: str = self.args.test_session_handle
            client = await self.get_client()
            session = await client.services.test_session.start_test_session(test_session_handle)
            print(json.dumps(session.to_dict(), indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("test_session_handle", type=str, metavar="TEST-SESSION-HANDLE",
                       help="The puzzle's test session handle (e.g. CgLastActivityPuzzle.test_session_handle).")
        return handler

    @cli_command("Run a codingamer's code against a single test case within a test session. Code is read from stdin.")
    async def cmd_api__test_session__play(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            test_session_handle: str = self.args.test_session_handle
            programming_language_id: str = self.args.language
            test_index: int | None = self.args.test_index
            code = sys.stdin.read()
            request = CgPlayRequest(code=code, programming_language_id=programming_language_id)
            if test_index is not None:
                request.multiple_languages = CgMultipleLanguagesTestParams(test_index=test_index)
            client = await self.get_client()
            result = await client.services.test_session.play(test_session_handle, request)
            print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("test_session_handle", type=str, metavar="TEST-SESSION-HANDLE",
                       help="The puzzle's test session handle.")
        p.add_argument("--language", "-l", type=str, required=True, metavar="LANGUAGE-ID",
                       help="Programming language ID the code is written in, e.g. 'Python3'.")
        p.add_argument("--test-index", "-t", type=int, default=None, metavar="N",
                       help="1-based test case index to run against, for MULTIPLE_LANGUAGES-type puzzles.")
        return handler

    @cli_command("Generate a Language Server Protocol (LSP) auth token for a test session.")
    async def cmd_api__test_session__generate_lsp_token(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            test_session_id: int = self.args.test_session_id
            client = await self.get_client()
            token = await client.services.test_session.generate_lsp_token(test_session_id)
            print(json.dumps(token))
        p = cmd.get_parser()
        p.add_argument("test_session_id", type=int, metavar="TEST-SESSION-ID",
                       help="The test session's numeric ID (CgTestSession.test_session_id).")
        return handler

    @cli_command("Fetch the codingamer's most recently saved code for one language in a test "
                 "session. CodinGame keeps your latest source per language, not just one; this "
                 "reaches the ones the session isn't currently on. Prints JSON null if you've "
                 "never attempted the puzzle in that language. This is a pure read--it does NOT "
                 "make that language the session's current one (only running a test or submitting "
                 "does that).")
    async def cmd_api__test_session__get_previous_code_by_language_id(
                self, cmd: CliCommand[Self],
            ) -> OptCmdFunc:
        async def handler() -> None:
            test_session_handle: str = self.args.test_session_handle
            programming_language_id: str = self.args.language_id
            client = await self.get_client()
            code = await client.services.test_session.get_previous_code_by_language_id(
                    test_session_handle, programming_language_id)
            print(json.dumps(code))
        p = cmd.get_parser()
        p.add_argument("test_session_handle", type=str, metavar="TEST-SESSION-HANDLE",
                       help="The puzzle's test session handle.")
        p.add_argument("language_id", type=str, metavar="LANGUAGE-ID",
                       help="CodinGame language ID, e.g. 'Python3', 'C++'.")
        return handler

    @cli_command("Submit a final solution to a puzzle for credit. Code is read from stdin.")
    async def cmd_api__test_session__submit(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            test_session_handle: str = self.args.test_session_handle
            programming_language_id: str = self.args.language
            code = sys.stdin.read()
            request = CgSubmitRequest(code=code, programming_language_id=programming_language_id)
            client = await self.get_client()
            submission_id = await client.services.test_session.submit(test_session_handle, request)
            print(json.dumps(submission_id))
        p = cmd.get_parser()
        p.add_argument("test_session_handle", type=str, metavar="TEST-SESSION-HANDLE",
                       help="The puzzle's test session handle.")
        p.add_argument("--language", "-l", type=str, required=True, metavar="LANGUAGE-ID",
                       help="Programming language ID the code is written in, e.g. 'Python3'.")
        return handler

    @cli_command("Report service commands.")
    async def cmd_api__report(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Find the results report for a single puzzle submission.")
    async def cmd_api__report__find_report_by_submission(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            submission_id: int = self.args.submission_id
            client = await self.get_client()
            report = await client.services.report.find_report_by_submission(submission_id)
            print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("submission_id", type=int, metavar="SUBMISSION-ID",
                       help="Numeric ID of the submission.")
        return handler

    @cli_command("TestSessionQuestionSubmission service commands.")
    async def cmd_api__test_session_question_submission(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Find all past submissions for a puzzle, most recent first.")
    async def cmd_api__test_session_question_submission__find_all_submissions(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            test_session_handle: str = self.args.test_session_handle
            client = await self.get_client()
            submissions = await client.services.test_session_question_submission.find_all_submissions(
                    test_session_handle)
            print(json.dumps([s.to_dict() for s in submissions], indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("test_session_handle", type=str, metavar="TEST-SESSION-HANDLE",
                       help="The puzzle's test session handle.")
        return handler

    @cli_command("CodinGamer service commands.")
    async def cmd_api__codingamer(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Find a codingamer's points/ranking stats by their opaque public handle.")
    async def cmd_api__codingamer__find_codingame_points_stats_by_handle(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            handle: str = self.args.handle
            client = await self.get_client()
            stats = await client.services.codingamer.find_codingame_points_stats_by_handle(handle)
            print(json.dumps(stats.to_dict(), indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("handle", type=str, metavar="HANDLE",
                       help="Opaque codingamer public handle string (not the numeric codingamer ID).")
        return handler

    @cli_command("Find a codingamer's public profile information by their numeric ID.")
    async def cmd_api__codingamer__find_codingamer_public_informations(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            codingamer_id: int | None = self.args.codingamer_id
            client = await self.get_client()
            codingamer = await client.services.codingamer.find_codingamer_public_informations(codingamer_id)
            print(json.dumps(codingamer.to_dict(), indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Codingamer numeric ID. Defaults to the logged-in codingamer's ID.")
        return handler

    @cli_command("Find the followers of a codingamer.")
    async def cmd_api__codingamer__find_followers(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            codingamer_id: int | None = self.args.codingamer_id
            current_codingamer_id: int | None = self.args.current_codingamer_id
            client = await self.get_client()
            followers = await client.services.codingamer.find_followers(codingamer_id, current_codingamer_id)
            print(json.dumps([f.to_dict() for f in followers], indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Codingamer whose followers to list. Defaults to the logged-in codingamer's ID.")
        p.add_argument("--current-codingamer-id", "-c", type=int, default=None, metavar="ID",
                       help="Must equal the logged-in codingamer's ID (server-enforced). "
                            "Defaults to the logged-in codingamer's ID.")
        return handler

    @cli_command("Find the codingamers that a codingamer is following.")
    async def cmd_api__codingamer__find_following(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            codingamer_id: int | None = self.args.codingamer_id
            current_codingamer_id: int | None = self.args.current_codingamer_id
            client = await self.get_client()
            following = await client.services.codingamer.find_following(codingamer_id, current_codingamer_id)
            print(json.dumps([f.to_dict() for f in following], indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Codingamer whose followees to list. Defaults to the logged-in codingamer's ID.")
        p.add_argument("--current-codingamer-id", "-c", type=int, default=None, metavar="ID",
                       help="Must equal the logged-in codingamer's ID (server-enforced). "
                            "Defaults to the logged-in codingamer's ID.")
        return handler

    @cli_command("Find a codingamer's follow-card summary (profile plus follow-relationship flags).")
    async def cmd_api__codingamer__find_codingamer_follow_card(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            codingamer_id: int | None = self.args.codingamer_id
            current_codingamer_id: int | None = self.args.current_codingamer_id
            client = await self.get_client()
            card = await client.services.codingamer.find_codingamer_follow_card(
                    codingamer_id, current_codingamer_id)
            print(json.dumps(card.to_dict(), indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Codingamer whose follow card to fetch. Defaults to the logged-in codingamer's ID.")
        p.add_argument("--current-codingamer-id", "-c", type=int, default=None, metavar="ID",
                       help="Must equal the logged-in codingamer's ID (server-enforced). "
                            "Defaults to the logged-in codingamer's ID.")
        return handler

    @cli_command("Find the numeric IDs of a codingamer's followers.")
    async def cmd_api__codingamer__find_follower_ids(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            codingamer_id: int | None = self.args.codingamer_id
            client = await self.get_client()
            follower_ids = await client.services.codingamer.find_follower_ids(codingamer_id)
            print(json.dumps(follower_ids, indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Codingamer whose follower IDs to list. Defaults to the logged-in codingamer's ID.")
        return handler

    @cli_command("Find the numeric IDs of the codingamers that a codingamer is following.")
    async def cmd_api__codingamer__find_following_ids(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            codingamer_id: int | None = self.args.codingamer_id
            client = await self.get_client()
            following_ids = await client.services.codingamer.find_following_ids(codingamer_id)
            print(json.dumps(following_ids, indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Codingamer whose followee IDs to list. Defaults to the logged-in codingamer's ID.")
        return handler

    @cli_command("Search service commands.")
    async def cmd_api__search(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Search for codingamers, puzzles, and other objects by name.")
    async def cmd_api__search__search(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            query: str = self.args.query
            locale: str = self.args.locale
            type_filter: str | None = self.args.type
            client = await self.get_client()
            results = await client.services.search.search(query, locale, type_filter)
            print(json.dumps([r.to_dict() for r in results], indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("query", type=str, metavar="QUERY",
                       help="Search query text, e.g. a codingamer's pseudo or part of a puzzle title.")
        p.add_argument("--locale", "-l", type=str, default="en", metavar="LOCALE",
                       help="Locale code for localized result names, e.g. 'en', 'fr'. Defaults to 'en'.")
        p.add_argument("--type", "-t", type=str, default=None, metavar="TYPE",
                       help="Restrict results to a single result type, e.g. 'USER', 'PUZZLE'. "
                            "Defaults to no filter (all types).")
        return handler

    @cli_command("ProgrammingLanguage service commands.")
    async def cmd_api__programming_language(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Find the IDs of all programming languages supported for contribution reference solutions.")
    async def cmd_api__programming_language__find_all_ids(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            client = await self.get_client()
            language_ids = await client.services.programming_language.find_all_ids()
            print(json.dumps(language_ids, indent=2, sort_keys=True))
        return handler

    @cli_command("Vote service commands.")
    async def cmd_api__vote(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Find a votable's current up/down-vote tally (e.g. a contribution's "
                 "CgContribution.votable_id)--CodinGame's generic community vote, distinct from "
                 "the moderator approve/reject gate (no known API for that yet).")
    async def cmd_api__vote__find_votable_values_by_id(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            votable_id: int = self.args.votable_id
            codingamer_id: int | None = self.args.codingamer_id
            client = await self.get_client()
            values = await client.services.vote.find_votable_values_by_id(votable_id, codingamer_id)
            print(json.dumps([v.to_dict() for v in values], indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("votable_id", type=int, metavar="VOTABLE-ID",
                       help="The votable entity's ID, e.g. a contribution's votableId.")
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="Codingamer whose own vote to report. Defaults to the logged-in codingamer's ID.")
        return handler

    @cli_command("Higher-level helper commands, layered on top of the plain API wrappers "
                 "(retries, polling, data normalization).")
    async def cmd_api_helper(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Contribution service helper commands.")
    async def cmd_api_helper__contribution(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Submit a new version of a contribution's content, with 524 retry/polling. "
                 "A JSON-serialized CgContributionData object is read from stdin.")
    async def cmd_api_helper__contribution__update_contribution(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_id: str = self.args.contribution_id
            puzzle_type: str = self.args.puzzle_type
            prev_version: int = self.args.prev_version
            draft: bool = self.args.draft
            ready_for_moderation: bool = self.args.ready_for_moderation
            codingamer_id: int | None = self.args.codingamer_id
            max_wait_seconds: float = self.args.max_wait_seconds
            contribution_data = CgContributionData.loads(sys.stdin.read())
            client = await self.get_client()
            contribution = await client.services.contribution.helper.update_contribution(
                    contribution_id, puzzle_type, contribution_data, draft, ready_for_moderation,
                    prev_version, codingamer_id, max_wait_seconds=max_wait_seconds)
            print(json.dumps(contribution.to_dict(), indent=2, sort_keys=True))
        p = cmd.get_parser()
        p.add_argument("contribution_id", type=str, metavar="CONTRIBUTION-ID",
                       help="Opaque contribution ID string.")
        p.add_argument("puzzle_type", type=str, metavar="PUZZLE-TYPE",
                       help="The type of the contribution, e.g. 'PUZZLE_INOUT'.")
        p.add_argument("prev_version", type=int, metavar="PREV-VERSION",
                       help="The contribution's current version number, as last retrieved via find-contribution "
                            "(an idempotency/concurrency check--rejected if stale).")
        p.add_argument("--draft", default=False, action="store_true",
                       help="Submit as a private, unpublished draft. Defaults to false.")
        p.add_argument("--ready-for-moderation", default=False, action="store_true",
                       help="Formally submit for moderation. Defaults to false.")
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="The authoring codingamer's numeric ID. Defaults to the logged-in codingamer's ID.")
        p.add_argument("--max-wait-seconds", type=float, default=0.0, metavar="SECONDS",
                       help="If the server returns HTTP 524 (Cloudflare/origin timeout), how long to keep "
                            "polling find-contribution for the version to increment before giving up, in "
                            "seconds. Defaults to 0, meaning wait indefinitely.")
        return handler

    @cli_command("Create a brand new contribution (deliberately with no 524 retry--see "
                 "CgContributionServiceHelper.create_contribution). A JSON-serialized "
                 "CgContributionData object is read from stdin.")
    async def cmd_api_helper__contribution__create_contribution(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            puzzle_type: str = self.args.puzzle_type
            draft: bool = self.args.draft
            ready_for_moderation: bool = self.args.ready_for_moderation
            codingamer_id: int | None = self.args.codingamer_id
            contribution_data = CgContributionData.loads(sys.stdin.read())
            client = await self.get_client()
            handle = await client.services.contribution.helper.create_contribution(
                    puzzle_type, contribution_data, draft, ready_for_moderation, codingamer_id)
            print(json.dumps(handle))
        p = cmd.get_parser()
        p.add_argument("puzzle_type", type=str, metavar="PUZZLE-TYPE",
                       help="The type of the contribution, e.g. 'PUZZLE_INOUT'.")
        p.add_argument("--draft", default=False, action="store_true",
                       help="Create as a private, unpublished draft. Defaults to false.")
        p.add_argument("--ready-for-moderation", default=False, action="store_true",
                       help="Formally submit for moderation. Defaults to false.")
        p.add_argument("--codingamer-id", "-g", type=int, default=None, metavar="ID",
                       help="The authoring codingamer's numeric ID. Defaults to the logged-in codingamer's ID.")
        return handler

    @staticmethod
    def _add_workspace_root_arg(p: Any) -> None:
        """The `--workspace-root` option every kind-agnostic command takes.

           Only containerized languages care: they bind-mount it so in-container paths match host
           paths (see `codingame_tools.language._docker`). Generated VS Code tasks pass
           `${workspaceFolder}` explicitly, because the editor knows the real answer and cg's
           `find_workspace_root` is only a heuristic."""
        p.add_argument("--workspace-root", type=Path, default=None, metavar="DIR",
                       help="The editor workspace root containing the working directory--normally "
                            "VS Code's ${workspaceFolder}. Only matters for containerized "
                            "languages, which bind-mount it so in-container paths match host "
                            "paths; passing it explicitly beats cg's own guess. Defaults to "
                            "searching upward for a .vscode/ or VCS directory.")

    def _resolve_working_dir_arg(self, target: Path | None) -> CgWorkingDir:
        """The working directory a `--file` names, or the one containing the current directory.

           Shared by every kind-agnostic command. Without `--file` these stay usable by hand, which
           is why discovery from the cwd is a fallback rather than an error."""
        if target is not None:
            return resolve_working_dir(target)
        found = find_working_dir(Path.cwd())
        if found is None:
            raise CliError(
                    "No puzzle or contribution working directory found here. Pass --file "
                    "(a file inside one), or cd into one.")
        return found

    @cli_command("Run the solution for whatever file you name against its working directory's test "
                 "cases, whether that's a puzzle or a contribution. Entirely local--no network "
                 "access at all. Exists so one editor task can serve every working directory in a "
                 "workspace: `cg play --file ${file}` needs to know neither which kind of working "
                 "directory it's in nor where that directory is. Exits non-zero if any test fails.")
    async def cmd_play(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            selected_only: bool = self.args.selected
            timeout: float = self.args.timeout
            build_timeout: float = self.args.build_timeout
            working_dir = self._resolve_working_dir_arg(self.args.file)

            console = Console(stderr=True, highlight=False)
            # soft_wrap: a long absolute path shouldn't be folded into two lines.
            console.print(f"[dim]{working_dir.kind} {working_dir.root}[/dim]", soft_wrap=True)
            failures = 0
            if working_dir.kind == "puzzle":
                manager = CgPuzzleManager(
                        working_dir.root, cast(CgClient, None),
                        toolchain_dir=self.resolve_toolchain_dir(),
                        toolchain_languages=self.resolve_toolchain_languages(),
                        toolchain_image=self.resolve_toolchain_image(),
                        mount_root=self.args.workspace_root)
                indices = [manager.resolve_debug_test_index()] if selected_only else None
                for result in await manager.play_local(
                            indices, timeout=timeout, build_timeout=build_timeout):
                    failures += not result.passed
                    mark = "[green]PASS[/green]" if result.passed else "[red]FAIL[/red]"
                    console.print(f"[{mark}] {result.index} {result.label}")
                    if not result.passed:
                        console.print(f"  expected: {result.expected_output!r}")
                        console.print(f"  actual:   {result.actual_output!r}")
            else:
                contribution = CgContributionManager(
                        working_dir.root, cast(CgClient, None),
                        toolchain_dir=self.resolve_toolchain_dir(),
                        toolchain_languages=self.resolve_toolchain_languages(),
                        toolchain_image=self.resolve_toolchain_image(),
                        mount_root=self.args.workspace_root)
                language = contribution.load().data.solution_language
                if language is None:
                    raise CliError(f"{working_dir.root} has no solution language set.")
                test_cases = [contribution.resolve_debug_test()] if selected_only \
                    else contribution.list_local_tests()
                for outcome in await contribution.run_local_tests(
                            test_cases, language, timeout=timeout, build_timeout=build_timeout):
                    failures += not outcome.passed
                    mark = "[green]PASS[/green]" if outcome.passed else "[red]FAIL[/red]"
                    console.print(f"[{mark}] {outcome.ordinal} {outcome.side}: {outcome.title}")
                    if not outcome.passed:
                        console.print(f"  expected: {outcome.expected_output!r}")
                        console.print(f"  actual:   {outcome.actual_output!r}")
            if failures:
                raise CliError(f"{failures} test case(s) failed.")
        p = cmd.get_parser()
        p.add_argument("--file", "-f", type=Path, default=None, metavar="FILE",
                       help="Any file inside the working directory to run--normally VS Code's "
                            "${file}. Its kind (puzzle or contribution) and root are both inferred "
                            "from it. Defaults to discovering a working directory from the current "
                            "directory.")
        p.add_argument("--selected", "-s", default=False, action="store_true",
                       help="Run only the test case selected for debugging (see `cg puzzle "
                            "select-test`/`cg contribution select-test`) instead of all of them.")
        p.add_argument("--timeout", type=float, default=DEFAULT_RUN_TIMEOUT_SECONDS, metavar="SECONDS",
                       help="Per-test-case run timeout in seconds.")
        p.add_argument("--build-timeout", type=float, default=DEFAULT_BUILD_TIMEOUT_SECONDS,
                       metavar="SECONDS", help="Build timeout in seconds, for compiled languages.")
        self._add_workspace_root_arg(p)
        return handler

    def _report_provisioning(self, changed: list[Path], *, supported: bool, check: bool) -> None:
        """Shared reporting for `cg vscode install`.

           An empty `changed` is ambiguous--already up to date, or no integration for any of these
           languages at all--so `supported` is needed to tell the two apart. Under `--check` a
           non-empty result exits non-zero, so it works in a script or a pre-commit hook."""
        if not changed:
            if supported:
                print("VS Code configuration is up to date.")
            else:
                self.eprint("No VS Code integration available for this language yet--nothing written.")
            return
        if check:
            self.eprint("VS Code configuration is out of date; `cg vscode install` would update:")
            for path in changed:
                print(path)
            raise CliExit(1)
        for path in changed:
            print(path)

    @cli_command(
            "Editor integration for VS Code. All-or-nothing opt-in: nothing here is written unless "
            "you ask for it, and what is written is confined to entries cg names as its own.")
    async def cmd_vscode(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None

    def _provisioning_targets(self, target: Path | None) -> list[CgWorkingDir]:
        """Which working directories `cg vscode install` should set up.

           With `--file`, exactly the one that file belongs to. Without it, **every** working
           directory cg can identify: the one the current directory sits inside, plus the active
           puzzle and the active contribution (see `cg puzzle activate`).

           A set rather than a single directory because installing is all-or-nothing opt-in, and
           because there is nothing to arbitrate. What gets written is per *language*, and languages
           are independent--so setting up two working directories at once is well defined, and two
           sharing a language simply write the same entries twice, the second a no-op. Picking one
           and ignoring the other would be the surprising behaviour, and erroring on "both a puzzle
           and a contribution are active" would reject the most ordinary setup there is."""
        if target is not None:
            return [resolve_working_dir(target)]

        found: list[CgWorkingDir] = []
        seen: set[Path] = set()

        def add(candidate: CgWorkingDir | None) -> None:
            if candidate is not None and candidate.root not in seen:
                seen.add(candidate.root)
                found.append(candidate)

        add(find_working_dir(Path.cwd()))
        settings = self.resolve_default_settings()
        for resolve in (resolve_puzzle_dir, resolve_contribution_dir):
            try:
                active = Path(resolve(None, settings=settings)).resolve()
            except Exception:  # noqa: BLE001 -- "not configured" is each resolver's own exception
                continue
            # Resolvers take a configured path at face value; it need not exist yet.
            kind = working_dir_kind(active)
            if kind is not None:
                add(CgWorkingDir(root=active, kind=kind))

        if not found:
            raise CliError(
                    "No puzzle or contribution working directory found. Pass --file (any file "
                    "inside one), cd into one, or activate one with `cg puzzle activate` / "
                    "`cg contribution activate`.")
        return found

    @cli_command(
            "Install cg's VS Code run/debug configuration. What it writes is the same for every "
            "working directory of a given language, so this is run once per language rather than "
            "once per working directory--and never again after an import, repair, or language "
            "change. With --file, sets up just that file's working directory; with no arguments, "
            "every working directory cg can find (the one you are standing in, plus the active "
            "puzzle and contribution). Writes into the workspace root's .vscode/ (VS Code only "
            "reads launch.json from the workspace root, never from a subdirectory), merging with "
            "what is already there: it replaces only the entries it generated, leaves yours alone, "
            "and does not touch a file whose content would not change.")
    async def cmd_vscode__install(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            workspace_dir: Path | None = self.args.workspace_dir
            force: bool = self.args.force
            check: bool = self.args.check
            adapter_logging: bool = self.args.debug_adapter_logging
            targets = self._provisioning_targets(self.args.file)
            toolchain_dir = self.resolve_toolchain_dir()

            changed: list[Path] = []
            supported = False
            for working_dir in targets:
                if working_dir.kind == "puzzle":
                    puzzle = CgPuzzleManager(
                            working_dir.root, cast(CgClient, None), toolchain_dir=toolchain_dir)
                    puzzle_data = puzzle.load_puzzle_data()
                    language = puzzle_data.solution_language if puzzle_data is not None else None
                    if language is None:
                        self.eprint(f"{working_dir.root}: no solution language set--skipped.")
                        continue
                    coro = puzzle.provision_vscode(
                            workspace_root=workspace_dir, force=force, check=check,
                            debug_adapter_logging=adapter_logging)
                else:
                    contribution = CgContributionManager(
                            working_dir.root, cast(CgClient, None), toolchain_dir=toolchain_dir)
                    language = contribution.load().data.solution_language
                    if language is None:
                        self.eprint(f"{working_dir.root}: no solution language set--skipped.")
                        continue
                    coro = contribution.provision_vscode(
                            language, workspace_root=workspace_dir, force=force, check=check,
                            debug_adapter_logging=adapter_logging)
                supported = supported or get_language(language).supports_vscode
                try:
                    changed.extend(await coro)
                except CgVsCodeMergeError as e:
                    raise CliError(str(e)) from e
            self._report_provisioning(changed, supported=supported, check=check)
        p = cmd.get_parser()
        p.add_argument("--file", "-f", type=Path, default=None, metavar="FILE",
                       help="Set up only the working directory this file belongs to. Defaults to "
                            "every working directory cg can find.")
        p.add_argument("--workspace-dir", type=Path, default=None, metavar="DIR",
                       help="Workspace root to write .vscode/ into. Defaults to the nearest "
                            "enclosing directory that already has a .vscode/, then the nearest "
                            "one under version control, then the working directory itself.")
        p.add_argument("--force", action="store_true",
                       help="Overwrite an existing .vscode/ config file that isn't strict JSON "
                            "(VS Code allows comments there, which can't be merged into safely). "
                            "Without this, such a file is left untouched and an error is reported.")
        p.add_argument("--check", action="store_true",
                       help="Report what would change and exit non-zero if anything would, without "
                            "writing. Use it to find out whether a cg upgrade changed the "
                            "generated configuration--there is no version stamp to compare, "
                            "because the generated content is the version.")
        p.add_argument("--debug-adapter-logging", action="store_true",
                       help="Generate a configuration that logs the debug adapter's own "
                            "conversation with the debugger to the Debug Console. For diagnosing a "
                            "session that misbehaves when everything underneath it works: the "
                            "adapter is the one part of the stack that can't be exercised from a "
                            "terminal. Loud and slow--re-run without it to turn it back off.")
        return handler

    @cli_command("Debug-session commands that work in any working directory, puzzle or "
                 "contribution. The kind-agnostic counterparts of `cg puzzle debug` / `cg "
                 "contribution debug`, taking a file instead of a directory and a test selection "
                 "instead of a test argument--which is what lets one static VS Code launch "
                 "configuration per language serve a whole workspace.")
    async def cmd_debug(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None

    @cli_command("Build the debug profile and start a stopped debug target fed by the working "
                 "directory's selected test case, ready for a debugger to attach. Prints the "
                 "connection details. Which test is selected comes from `.meta/` (see `cg puzzle "
                 "select-test` / `cg contribution select-test`), defaulting to the first test "
                 "case--so this command needs no test argument, and a launch configuration wiring "
                 "it to a preLaunchTask never has to be regenerated.")
    async def cmd_debug__start(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            working_dir = self._resolve_working_dir_arg(self.args.file)
            build_timeout: float = self.args.build_timeout
            toolchain_dir = self.resolve_toolchain_dir()
            mount_root: Path | None = self.args.workspace_root
            try:
                if working_dir.kind == "puzzle":
                    puzzle = CgPuzzleManager(
                            working_dir.root, cast(CgClient, None),
                            toolchain_dir=toolchain_dir, mount_root=mount_root)
                    session = await puzzle.start_debug_session(
                            puzzle.resolve_debug_test_index(), timeout=build_timeout)
                else:
                    contribution = CgContributionManager(
                            working_dir.root, cast(CgClient, None),
                            toolchain_dir=toolchain_dir, mount_root=mount_root)
                    language = contribution.load().data.solution_language
                    if language is None:
                        raise CliError(f"{working_dir.root} has no solution language set.")
                    test_case = contribution.resolve_debug_test()
                    session = await contribution.start_debug_session(
                            language, test_case.ordinal, test_case.side, timeout=build_timeout)
            except CgLanguageOperationNotSupportedError as e:
                raise CliError(str(e)) from e
            if session.output:
                self.eprint(session.output.rstrip())
            if not session.ok:
                raise CliExit(1)
            for key, value in session.details.items():
                self.eprint(f"{key}: {value}")
        p = cmd.get_parser()
        p.add_argument("--file", "-f", type=Path, default=None, metavar="FILE",
                       help="Any file inside the working directory to debug--normally VS Code's "
                            "${file}. Its kind (puzzle or contribution) and root are both inferred "
                            "from it. Defaults to discovering a working directory from the current "
                            "directory.")
        p.add_argument("--build-timeout", type=float, default=DEFAULT_BUILD_TIMEOUT_SECONDS,
                       metavar="SECONDS", help="Wall-clock timeout for the debug build.")
        self._add_workspace_root_arg(p)
        return handler

    @cli_command("Stop a debug target started by `cg debug start`. Always succeeds, including when "
                 "nothing is running--it's wired to a postDebugTask, which fires even for a "
                 "session that never really began.")
    async def cmd_debug__stop(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            working_dir = self._resolve_working_dir_arg(self.args.file)
            toolchain_dir = self.resolve_toolchain_dir()
            mount_root: Path | None = self.args.workspace_root
            if working_dir.kind == "puzzle":
                await CgPuzzleManager(
                        working_dir.root, cast(CgClient, None), toolchain_dir=toolchain_dir,
                        mount_root=mount_root).stop_debug_session()
                return
            contribution = CgContributionManager(
                    working_dir.root, cast(CgClient, None), toolchain_dir=toolchain_dir,
                    mount_root=mount_root)
            language = contribution.load().data.solution_language
            if language is not None:
                await contribution.stop_debug_session(language)
        p = cmd.get_parser()
        p.add_argument("--file", "-f", type=Path, default=None, metavar="FILE",
                       help="Any file inside the working directory whose debug session to stop--"
                            "normally VS Code's ${file}. Defaults to discovering a working "
                            "directory from the current directory.")
        self._add_workspace_root_arg(p)
        return handler

    @cli_command("List server-side contributions, one line each: handle, id, status, puzzle type, "
                 "title. By default lists every author's pending contributions from the community "
                 "review queue (Contribution/getAllPendingContributions); --personal lists only "
                 "your own, in any status (Contribution/getPersonalContributions). With --json, "
                 "prints the raw list instead: CgPendingContribution or CgPersonalContribution "
                 "depending on which was listed -- there is no unified schema between the two.")
    async def cmd_contributions(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            personal: bool = self.args.personal
            use_json: bool = self.args.json
            client = await self.get_client()
            items: list[CgPendingContribution] | list[CgPersonalContribution]
            if personal:
                items = await client.services.contribution.get_personal_contributions()
            else:
                items = await client.services.contribution.get_all_pending_contributions()

            if use_json:
                print(json.dumps([item.to_dict() for item in items], indent=2, sort_keys=True))
                return

            if not items:
                print("No contributions found.")
                return
            print(f"{'HANDLE':<42}{'ID':<10}{'STATUS':<12}{'TYPE':<16}{'TITLE'}")
            for item in items:
                print(f"{item.public_handle:<42}{item.id:<10}{item.status:<12}{item.contribution_type:<16}{item.title}")
        p = cmd.get_parser()
        p.add_argument("--personal", default=False, action="store_true",
                       help="List only the logged-in codingamer's own contributions (any "
                            "status), instead of all pending contributions from every author.")
        return handler

    @cli_command("List or search the catalogue of puzzle topics a contribution can be tagged with "
                 "(Topic/getAllChildrenTopicsWithPuzzleCount). With no SEARCH, lists every topic; "
                 "with one, matches it against handles and display labels, ignoring case. Topics "
                 "carry a label per CodinGame UI language region, and SEARCH matches any of them, "
                 "so either the English or the French label finds a topic. The catalogue is global "
                 "CodinGame data, cached per user for a week; --refresh refetches it. With --json, "
                 "prints the raw CgTopic list instead of a table.")
    async def cmd_topics(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            search: str | None = self.args.search
            category: str | None = self.args.category
            refresh: bool = self.args.refresh
            use_json: bool = self.args.json
            client = await self.get_client()
            catalogue = await get_topic_catalogue(client, refresh=refresh)
            topics = search_topics(catalogue, search, category=category)
            if use_json:
                print(json.dumps([t.to_dict() for t in topics], indent=2, sort_keys=True))
                return
            if not topics:
                print("No topics matched.")
                return
            _print_topic_table(topics, show_all_labels=self.args.all_labels)
        p = cmd.get_parser()
        p.add_argument("search", type=str, nargs="?", default=None, metavar="SEARCH",
                       help="Match topics whose handle or any display label contains this, "
                            "ignoring case. Omit to list all of them.")
        p.add_argument("--category", "-c", type=str, default=None, metavar="CATEGORY",
                       help="Only topics in this category, e.g. 'FUNDAMENTALS', 'INTERMEDIATE', "
                            "'ADVANCED'.")
        p.add_argument("--all-labels", default=False, action="store_true",
                       help="Show every language region's label rather than just the English one.")
        p.add_argument("--refresh", default=False, action="store_true",
                       help="Refetch the catalogue instead of using the cached copy.")
        return handler

    @cli_command("Author and maintain your own CodinGame contributions in a local working "
                 "directory. data/ is backed by a real git repository, so syncing with the server "
                 "is a genuine fetch/merge workflow rather than a one-shot overwrite. See `cg api "
                 "contribution` and `cg api-helper contribution` for the raw, stateless API "
                 "underneath.")
    async def cmd_contribution(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        p = cmd.get_parser()
        p.add_argument("--contribution-dir", "-d", type=Path, default=None, metavar="DIR",
                       help="Working directory to operate on. Defaults to CG_CONTRIBUTION_DIR, then "
                            "the configured default (`cg settings set contribution-dir`), then the "
                            "current directory or \"./contribution\" if it contains contribution.json. "
                            "Ignored by `cg contribution import`, which always takes an explicit new "
                            "target directory as a positional argument instead.")
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Create a contribution working directory from an existing server-side "
                 "contribution, downloading its cover image if one is set. Makes it the active "
                 "contribution. DIRECTORY must not already exist. Ignores --contribution-dir.")
    async def cmd_contribution__import(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_id: str = self.args.contribution_id
            directory: Path = Path(self.args.directory).expanduser().resolve()
            if directory.exists():
                # Not an outright refusal: a directory whose contribution.json already tracks
                # this exact contribution is a legitimate repair target (e.g. an outer project
                # clone whose git-dir was deliberately not brought along--see
                # CgContributionManager.import_()'s docstring), which import_() itself already
                # knows how to handle (`cg contribution repair` is the simpler, dedicated way to
                # do this--no need to know/pass CONTRIBUTION-ID--but this shortcut is kept too,
                # for anyone who reaches for `import` out of habit). Anything else existing there
                # is left alone, same as before.
                existing_identity = CgContributionManager(directory, cast(CgClient, None)).load_identity()
                if existing_identity is None or existing_identity.contribution_handle != contribution_id:
                    raise CliError(
                            f"Directory already exists: {directory}. `cg contribution import` "
                            "only creates a new working directory (or repairs one whose "
                            "contribution.json already tracks CONTRIBUTION-ID--see also `cg "
                            "contribution repair`); import into an unrelated existing directory "
                            "by editing it directly, or remove the directory first."
                        )
            client = await self.get_client()
            manager = CgContributionManager(directory, client)
            working = await manager.import_(contribution_id)
            await self.set_current_working_dir("contribution", directory)
            self.eprint(f"Imported contribution {contribution_id!r} into {directory}")
            self.eprint(f"  title: {working.data.title!r}")
            self.eprint(f"  puzzleType: {working.puzzle_type!r}")
            self.eprint("  (now the active contribution--`cg contribution where` prints it, "
                        "`cg contribution deactivate` clears it)")
        p = cmd.get_parser()
        p.add_argument("directory", type=Path, metavar="DIRECTORY",
                       help="New directory to create the working directory in, or an existing "
                            "one whose contribution.json already tracks CONTRIBUTION-ID (to "
                            "repair a missing git-dir--see also `cg contribution repair`). Always "
                            "first, matching `cg contribution create` and `cg puzzle import`. "
                            "Becomes the active contribution directory (see `cg contribution "
                            "activate`).")
        p.add_argument("contribution_id", type=str, metavar="CONTRIBUTION-ID",
                       help="Opaque contribution ID string (see `cg api contribution find-contribution`).")
        return handler

    @cli_command("Rebuild this working directory's git repository from scratch, without disturbing "
                 "what is already in data/. Use it after cloning a repo that does not carry .meta/ "
                 "(it is gitignored), or if the repository is missing or corrupt. If this "
                 "contribution exists on the server it re-bases off a fresh copy; if it was "
                 "created locally and never pushed, it works entirely offline.")
    async def cmd_contribution__repair(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_dir: Path | None = self.args.contribution_dir
            resolved_dir = resolve_contribution_dir(contribution_dir, settings=self.resolve_default_settings())
            client = await self.get_client()
            manager = CgContributionManager(resolved_dir, client)
            working = await manager.repair()
            identity = manager.load_identity()
            assert identity is not None
            if identity.contribution_handle is None:
                self.eprint(f"{resolved_dir}: repaired (purely local--never pushed to the server yet)")
            else:
                self.eprint(f"{resolved_dir}: repaired, re-based off contribution {identity.contribution_handle!r}")
            self.eprint(f"  title: {working.data.title!r}")
        return handler

    @cli_command("Create a brand new contribution working directory, entirely locally -- no "
                 "network access, and nothing exists on the server until your first `cg "
                 "contribution push`. Seeds placeholder content for every file you will edit, "
                 "consistent enough that `cg contribution play` passes on it immediately, and "
                 "marks the contribution a private draft. Edit the files under data/, then push. "
                 "DIRECTORY must not already exist. Ignores --contribution-dir.")
    async def cmd_contribution__create(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            directory: Path = Path(self.args.directory).expanduser().resolve()
            title: str | None = self.args.title
            puzzle_type: str = self.args.puzzle_type
            language: str = self.args.language
            if directory.exists():
                raise CliError(
                        f"Directory already exists: {directory}. `cg contribution create` only "
                        "creates a new working directory; remove it first, or use `cg "
                        "contribution import` if a contribution already exists server-side."
                    )
            if title is None:
                title = f"Example puzzle {directory.name}"
            client = await self.get_client()
            manager = CgContributionManager(directory, client)
            working = await manager.create(title=title, puzzle_type=puzzle_type, language=language)
            await self.set_current_working_dir("contribution", directory)
            self.eprint(f"Initialized a new local-only contribution working directory at {directory}")
            self.eprint("  (not yet pushed to the server)")
            self.eprint(f"  title: {working.data.title!r}")
            self.eprint(f"  puzzleType: {working.puzzle_type!r}")
            self.eprint(f"  language: {language!r}")
            self.eprint("  (seeded with placeholder statement/difficulty/test cases--edit, then "
                         "`cg contribution push` to create it on the server)")
            self.eprint("  (now the active contribution--`cg contribution where` prints it, "
                        "`cg contribution deactivate` clears it)")
        p = cmd.get_parser()
        p.add_argument("directory", type=Path, metavar="DIRECTORY",
                       help="New directory to create the working directory in. Must not already exist.")
        p.add_argument("title", type=str, nargs="?", default=None, metavar="TITLE",
                       help="Title for the new contribution. Defaults to 'Example puzzle <DIRECTORY's last path "
                            "component>'.")
        p.add_argument("--puzzle-type", "-t", type=str, default="PUZZLE_INOUT", metavar="PUZZLE-TYPE",
                       help="The type of the contribution. Defaults to 'PUZZLE_INOUT'.")
        p.add_argument("--language", "-l", type=str, default="Python3", metavar="LANGUAGE",
                       help="Reference solution language, e.g. 'Python3', 'Java', 'C++'. Defaults to "
                            "'Python3'. The solution file carries the language's own extension "
                            "(data/solution.cpp for C++), but only Python3 gets a working starter solution "
                            "that passes the seeded test case; every other language starts empty, for you "
                            "to write.")
        return handler

    @cli_command("Push this working directory's content to the server, then update the local "
                 "server and version-data branches to match. The first push from a directory made "
                 "by `cg contribution create` establishes the contribution on the server and "
                 "records its handle; pass --direct-create to do that in a single call. A push "
                 "with nothing to push does nothing and says so: CodinGame has no empty update, so "
                 "republishing identical content costs you a moderation cycle -- pass --force to "
                 "publish a version anyway.")
    async def cmd_contribution__push(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_dir: Path | None = self.args.contribution_dir
            direct_create: bool = self.args.direct_create
            force: bool = self.args.force
            resolved_dir = resolve_contribution_dir(contribution_dir, settings=self.resolve_default_settings())
            client = await self.get_client(require_credentials=True)
            manager = CgContributionManager(resolved_dir, client)
            result = await manager.push(direct_create=direct_create, force=force)
            if result is None:
                # Not an error: nothing needed doing, so the exit status stays 0 for scripts.
                self.eprint(f"{resolved_dir} is already up to date on the server--nothing to push. "
                            "Use --force to publish a new version anyway.")
                return
            self.eprint(
                    f"Pushed {resolved_dir} -> contribution {result.public_handle!r}, "
                    f"version {result.last_version.version}"
                )
        p = cmd.get_parser()
        p.add_argument("--direct-create", default=False, action="store_true",
                       help="On a first push, skip the minimal-stub-first safety step and call "
                            "createContribution once, directly, with the real content. Ignored "
                            "on anything but a first push.")
        p.add_argument("--force", "-f", default=False, action="store_true",
                       help="Push even when nothing has changed. Without it, a push with no local "
                            "changes does nothing: updateContribution has no empty update--it "
                            "increments the version and re-runs moderation regardless--so "
                            "republishing identical content costs a review cycle and buries the "
                            "history of real changes.")
        return handler

    @cli_command("Debug-session plumbing for languages whose debugger attaches to a running "
                 "target, such as C++ in its container. Normally invoked for you by the VS Code "
                 "configuration `cg vscode install` generates, rather than typed by hand. "
                 "Languages whose debugger launches the program itself, such as Python3, never use "
                 "these.")
    async def cmd_contribution__debug(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None

    @cli_command("Build the debug profile and start a stopped debug target fed by the selected "
                 "test case's input, ready for a debugger to attach. Prints the connection "
                 "details.")
    async def cmd_contribution__debug__start(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_dir: Path | None = self.args.contribution_dir
            ordinal: str = self.args.ordinal
            side: str = self.args.side
            build_timeout: float = self.args.build_timeout
            resolved_dir = resolve_contribution_dir(contribution_dir, settings=self.resolve_default_settings())
            manager = CgContributionManager(
                    resolved_dir, cast(CgClient, None),
                    toolchain_dir=self.resolve_toolchain_dir(),
                    toolchain_languages=self.resolve_toolchain_languages(),
                    toolchain_image=self.resolve_toolchain_image())
            solution_language = manager.load().data.solution_language
            if solution_language is None:
                raise CliError(f"{manager.contribution_data_file} has no solutionLanguage set.")
            try:
                session = await manager.start_debug_session(
                        solution_language, ordinal, side, timeout=build_timeout)
            except CgLanguageOperationNotSupportedError as e:
                raise CliError(str(e)) from e
            if session.output:
                self.eprint(session.output.rstrip())
            if not session.ok:
                raise CliExit(1)
            for key, value in session.details.items():
                print(f"{key}: {value}")
        p = cmd.get_parser()
        p.add_argument("ordinal", type=str, metavar="ORDINAL",
                       help="Test case ordinal (tests/'s directory name, e.g. \"03\" or \"3\").")
        p.add_argument("side", choices=["local", "validator"], metavar="SIDE",
                       help="Which side of the test case to feed in: local or validator.")
        p.add_argument("--build-timeout", type=float, default=DEFAULT_BUILD_TIMEOUT_SECONDS,
                       metavar="SECONDS", help="Wall-clock timeout for the debug build.")
        return handler

    @cli_command("Stop a debug target started by `cg contribution debug start`. Always succeeds, "
                 "including when nothing is running.")
    async def cmd_contribution__debug__stop(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_dir: Path | None = self.args.contribution_dir
            resolved_dir = resolve_contribution_dir(contribution_dir, settings=self.resolve_default_settings())
            manager = CgContributionManager(
                    resolved_dir, cast(CgClient, None),
                    toolchain_dir=self.resolve_toolchain_dir(),
                    toolchain_languages=self.resolve_toolchain_languages(),
                    toolchain_image=self.resolve_toolchain_image())
            solution_language = manager.load().data.solution_language
            if solution_language is not None:
                await manager.stop_debug_session(solution_language)
        return handler

    @cli_command("Compile the reference solution, if its language needs compiling (a no-op for "
                 "interpreted languages such as Python3). You rarely need this -- `cg contribution "
                 "play` builds first automatically -- but it is useful to compile without running, "
                 "or to warm a cold container image up front. Near-instant when the source has not "
                 "changed since the last successful build. Compiler diagnostics go to stderr.")
    async def cmd_contribution__build(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_dir: Path | None = self.args.contribution_dir
            profile: str = self.args.profile
            build_timeout: float = self.args.build_timeout
            resolved_dir = resolve_contribution_dir(contribution_dir, settings=self.resolve_default_settings())
            manager = CgContributionManager(
                    resolved_dir, cast(CgClient, None),
                    toolchain_dir=self.resolve_toolchain_dir(),
                    toolchain_languages=self.resolve_toolchain_languages(),
                    toolchain_image=self.resolve_toolchain_image())
            view = manager.load()
            solution_language = view.data.solution_language
            if solution_language is None:
                raise CliError(f"{manager.contribution_data_file} has no solutionLanguage set.")
            result = await manager.build_solution(
                    solution_language, profile=cast(CgBuildProfile, profile), timeout=build_timeout)
            if result.output:
                self.eprint(result.output.rstrip())
            if not result.ok:
                raise CliExit(1)
            self.eprint("up to date" if result.up_to_date else "built")
        p = cmd.get_parser()
        p.add_argument("--profile", choices=["run", "debug"], default="run",
                       help="Which build to produce. \"debug\" is built for debuggability rather "
                            "than speed (no optimization, full symbols) and is what a debug session "
                            "uses. Ignored by languages that need no build. Default: run.")
        p.add_argument("--build-timeout", type=float, default=DEFAULT_BUILD_TIMEOUT_SECONDS,
                       metavar="SECONDS",
                       help="Wall-clock timeout. Generous by default, because a cold build can pull "
                            f"and build a container image. Default {DEFAULT_BUILD_TIMEOUT_SECONDS}.")
        return handler

    async def _show_or_set_field(self, field: str, value: object) -> None:
        """Print a contribution metadata field, or set it when a value was given.

           Shared by the `cg contribution set <field>` subcommands that are plain edits. They
           differ only in how argparse types and documents their value, which is the whole reason
           each is its own subcommand. `solution-language` is not one of these -- setting it
           rewrites the reference solution, so it has its own handler."""
        resolved_dir = resolve_contribution_dir(
                self.args.contribution_dir, settings=self.resolve_default_settings())
        manager = CgContributionManager(resolved_dir, cast(CgClient, None))
        view = manager.load()
        if value is None:
            print(_format_field_value(CONTRIBUTION_SET_FIELDS[field](view)))
            return
        update: dict[str, Any] = {field.replace("-", "_"): value}
        try:
            updated = manager.update_metadata(**update)
        except CgContributionManagerError as e:
            raise CliError(str(e)) from e
        self.eprint(f"{resolved_dir}: {field} = "
                    f"{_format_field_value(CONTRIBUTION_SET_FIELDS[field](updated))}")

    def _resolve_topic_or_fail(self, token: str, catalogue: list[CgTopic],
                               *, not_found_hint: str | None = None) -> CgTopic:
        """Resolve one topic reference, turning an ambiguous or unknown one into a CLI error.

           An ambiguous reference prints every handle that matched, which is the thing the user
           needs in order to retype it unambiguously."""
        try:
            if not_found_hint is None:
                return resolve_topic(token, catalogue)
            return resolve_topic(token, catalogue, not_found_hint=not_found_hint)
        except AmbiguousTopicError as e:
            self.eprint(f"{token!r} matches {len(e.candidates)} topics:")
            for candidate in sorted(e.candidates, key=lambda t: t.handle or ""):
                self.eprint(f"    {candidate.handle}  (id {candidate.id})  {topic_label(candidate)}")
            raise CliError(f"{token!r} is ambiguous--use one of the handles above, or its id.") from e
        except TopicResolutionError as e:
            raise CliError(str(e)) from e

    async def _list_contribution_topics(self) -> None:
        """Print this contribution's own topics, shared by `cg contribution topic` and its
           `list` subcommand."""
        resolved_dir = resolve_contribution_dir(
                self.args.contribution_dir, settings=self.resolve_default_settings())
        manager = CgContributionManager(resolved_dir, cast(CgClient, None))
        topics = list(manager.load().data.topics)
        if self.args.json:
            print(json.dumps([t.to_dict() for t in topics], indent=2, sort_keys=True))
            return
        if not topics:
            print("No topics set. Add one with `cg contribution topic add TOPIC` "
                  "(`cg topics` lists them).")
            return
        _print_topic_table(topics)

    @cli_command("Show the contribution's scalar metadata fields, or set one. Each field is its "
                 "own subcommand, so `cg contribution set difficulty --help` documents what that "
                 "field accepts. Bare `cg contribution set` lists every field and its current "
                 "value. Every field is stored in data/contribution-data.json and is purely "
                 "local--nothing reaches the server until the next `cg contribution push`.")
    async def cmd_contribution__set(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            resolved_dir = resolve_contribution_dir(
                    self.args.contribution_dir, settings=self.resolve_default_settings())
            view = CgContributionManager(resolved_dir, cast(CgClient, None)).load()
            values = {name: reader(view) for name, reader in CONTRIBUTION_SET_FIELDS.items()}
            if self.args.json:
                print(json.dumps(values, indent=2, sort_keys=True))
                return
            for name, current in values.items():
                print(f"{name:<22}{_format_field_value(current)}")
        return handler

    @cli_command("Show or set the contribution's title--what solvers see in listings and at the "
                 "top of the puzzle. With no TITLE, prints the current one.")
    async def cmd_contribution__set__title(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            await self._show_or_set_field("title", self.args.value)
        p = cmd.get_parser()
        p.add_argument("value", type=str, nargs="?", default=None, metavar="TITLE",
                       help="The new title. Omit to print the current one.")
        return handler

    @cli_command("Show or set how hard the puzzle is. CodinGame offers exactly three levels and "
                 "shows this as a badge on the puzzle. With no LEVEL, prints the current one.")
    async def cmd_contribution__set__difficulty(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            await self._show_or_set_field("difficulty", self.args.value)
        p = cmd.get_parser()
        p.add_argument("value", type=str, nargs="?", default=None, metavar="LEVEL",
                       choices=CONTRIBUTION_DIFFICULTIES,
                       help=f"One of: {', '.join(CONTRIBUTION_DIFFICULTIES)}. Omit to print the "
                            "current level.")
        return handler

    @cli_command("Show or set whether the next pushed version is a private draft. A draft is "
                 "visible only to you, so this is what keeps work in progress off the community "
                 "queue. New contributions start as drafts. With no VALUE, prints the current "
                 "setting.")
    async def cmd_contribution__set__draft(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            await self._show_or_set_field("draft", self.args.value)
        _add_cli_bool_argument(cmd.get_parser(), "draft")
        return handler

    @cli_command("Show or set whether the next pushed version is formally submitted for "
                 "moderation. Turning this on is what puts the contribution in front of "
                 "moderators; it starts off. With no VALUE, prints the current setting.")
    async def cmd_contribution__set__ready_for_moderation(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            await self._show_or_set_field("ready-for-moderation", self.args.value)
        _add_cli_bool_argument(cmd.get_parser(), "ready-for-moderation")
        return handler

    @cli_command("Show or set the contribution type. CodinGame has several (CLASHOFCODE, "
                 "PUZZLE_MULTI, PUZZLE_SOLO, PUZZLE_OPTI), but PUZZLE_INOUT--a standard "
                 "non-interactive solo puzzle--is the only one this working-directory format "
                 "handles, so it is the only value accepted. With no TYPE, prints the current one.")
    async def cmd_contribution__set__puzzle_type(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            await self._show_or_set_field("puzzle-type", self.args.value)
        p = cmd.get_parser()
        p.add_argument("value", type=str, nargs="?", default=None, metavar="TYPE",
                       choices=SUPPORTED_PUZZLE_TYPES,
                       help=f"One of: {', '.join(SUPPORTED_PUZZLE_TYPES)}. Omit to print the "
                            "current type.")
        return handler

    @cli_command("Show or switch the language the reference solution is written in, writing a "
                 "fresh starter stub. With no LANGUAGE, prints the current one. "
                 "DESTRUCTIVE: a contribution stores only one solution, with no per-language "
                 "history, so there is nothing to restore and nothing to switch back to--your "
                 "existing solution is replaced by a stub, and the next push overwrites the last "
                 "durable copy. Refuses unless the current solution is still exactly the stub cg "
                 "generated; matching what the server has does not make it safe, since that copy "
                 "is what the next push destroys. Save any real work outside the working directory "
                 "first. Purely local--no network call.")
    async def cmd_contribution__set__solution_language(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            language: str | None = self.args.value
            resolved_dir = resolve_contribution_dir(
                    self.args.contribution_dir, settings=self.resolve_default_settings())
            # No client: there is no per-language code to fetch, unlike `cg puzzle set`.
            manager = CgContributionManager(
                    resolved_dir, cast(CgClient, None),
                    toolchain_dir=self.resolve_toolchain_dir(),
                    toolchain_languages=self.resolve_toolchain_languages(),
                    toolchain_image=self.resolve_toolchain_image())
            if language is None:
                print(_format_field_value(manager.load().data.solution_language))
                return
            try:
                result = await manager.set_language(language, force=self.args.force)
            except CgContributionManagerError as e:
                raise CliError(str(e)) from e
            self.eprint(f"{resolved_dir}: {result.previous_language!r} -> {result.language!r}")
            if result.wrote_stub:
                self.eprint(f"  wrote a starter {result.language} solution to {manager.solution_file}.")
            else:
                self.eprint(f"  no starter stub available for {result.language}--left "
                            f"{manager.solution_file} empty; write your solution there. (An empty "
                            "solution is pushed as none at all, which is valid.)")
        p = cmd.get_parser()
        p.add_argument("value", type=str, nargs="?", default=None, metavar="LANGUAGE",
                       help="CodinGame language ID to switch to, e.g. 'C++', 'Python3'. Omit to "
                            "print the current language.")
        p.add_argument("--force", "-f", default=False, action="store_true",
                       help="Switch even though a real reference solution would be discarded. "
                            "There is no way to get it back--save a copy outside the working "
                            "directory first.")
        return handler

    @cli_command("Tag this contribution with puzzle topics, or untag it. Topics are what a solver "
                 "browses by, and are stored in data/contribution-data.json like any other "
                 "content--purely local until the next `cg contribution push`. Bare `cg "
                 "contribution topic` lists this contribution's own topics; see `cg topics` for "
                 "the catalogue to pick from.")
    async def cmd_contribution__topic(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            await self._list_contribution_topics()
        return handler

    @cli_command("List the topics this contribution is tagged with. Purely local, no network. "
                 "With --json, prints the raw CgTopic list instead of a table.")
    async def cmd_contribution__topic__list(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            await self._list_contribution_topics()
        return handler

    @cli_command("Tag this contribution with one or more topics. Each TOPIC is resolved against "
                 "the topic catalogue, preferring, in order: an exact handle; a handle ignoring "
                 "case; a numeric id; an exact display label in any language region; and finally "
                 "a substring of a handle or label, accepted only when exactly one topic matches. "
                 "An ambiguous TOPIC is refused, listing the handles that matched so you can pick "
                 "one. A topic already present is left alone rather than duplicated.")
    async def cmd_contribution__topic__add(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            tokens: list[str] = self.args.topics
            resolved_dir = resolve_contribution_dir(
                    self.args.contribution_dir, settings=self.resolve_default_settings())
            client = await self.get_client()
            catalogue = await get_topic_catalogue(client, refresh=self.args.refresh)
            manager = CgContributionManager(resolved_dir, cast(CgClient, None))
            wanted = [self._resolve_topic_or_fail(token, catalogue) for token in tokens]
            added = manager.add_topics(wanted)
            for topic in wanted:
                if any(same_topic(topic, a) for a in added):
                    self.eprint(f"  + {topic.handle} ({topic_label(topic)})")
                else:
                    self.eprint(f"    {topic.handle} was already set--left alone")
            self.eprint(f"{resolved_dir}: {len(added)} topic(s) added.")
        p = cmd.get_parser()
        p.add_argument("topics", type=str, nargs="+", metavar="TOPIC",
                       help="Topic handle, numeric id, display label, or an unambiguous part of "
                            "one. Run `cg topics` to see them.")
        p.add_argument("--refresh", default=False, action="store_true",
                       help="Refetch the topic catalogue instead of using the cached copy.")
        return handler

    @cli_command("Remove one or more topics from this contribution. Each TOPIC is resolved against "
                 "the topics this contribution actually carries--not the whole catalogue--so it "
                 "needs no network access, and a topic CodinGame has since retired can still be "
                 "removed. Resolution and ambiguity work as they do for `cg contribution topic "
                 "add`. A topic that isn't set is reported and otherwise ignored.")
    async def cmd_contribution__topic__remove(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            tokens: list[str] = self.args.topics
            resolved_dir = resolve_contribution_dir(
                    self.args.contribution_dir, settings=self.resolve_default_settings())
            manager = CgContributionManager(resolved_dir, cast(CgClient, None))
            current = list(manager.load().data.topics)
            if not current:
                raise CliError(f"{resolved_dir} has no topics set--nothing to remove.")
            wanted = [self._resolve_topic_or_fail(token, current) for token in tokens]
            removed = manager.remove_topics(wanted)
            for topic in removed:
                self.eprint(f"  - {topic.handle} ({topic_label(topic)})")
            self.eprint(f"{resolved_dir}: {len(removed)} topic(s) removed.")
        p = cmd.get_parser()
        p.add_argument("topics", type=str, nargs="+", metavar="TOPIC",
                       help="Topic handle, numeric id, display label, or an unambiguous part of "
                            "one, among those this contribution carries.")
        return handler

    @cli_command("Make DIRECTORY the active contribution, so later `cg contribution` commands use "
                 "it without --contribution-dir. `cg contribution import` and `cg contribution "
                 "create` set this for you, so use this to switch between working directories you "
                 "already have. Outranks the configured default (`cg settings set "
                 "contribution-dir`); `cg contribution deactivate` clears it.")
    async def cmd_contribution__activate(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            directory = Path(self.args.directory).expanduser().resolve()
            if not (directory / CONTRIBUTION_IDENTITY_FILE_NAME).is_file():
                raise CliError(
                        f"{directory} is not a contribution working directory (no {CONTRIBUTION_IDENTITY_FILE_NAME}). "
                        "Use `cg contribution import DIRECTORY CONTRIBUTION-ID` to create one.")
            await self.set_current_working_dir("contribution", directory)
            self.eprint(f"Active contribution directory set to {directory}")
        p = cmd.get_parser()
        p.add_argument("directory", type=Path, nargs="?", default=Path.cwd(), metavar="DIRECTORY",
                       help="The contribution working directory to activate. Defaults to the current "
                            "directory, so `cd` into one and run this with no arguments.")
        return handler

    @cli_command("Clear the active contribution, so `cg contribution` commands fall back to the "
                 "configured default and the usual directory discovery. Touches no files -- only "
                 "the selection.")
    async def cmd_contribution__deactivate(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            previous = await self.clear_current_working_dir("contribution")
            if previous is None:
                self.eprint("No active contribution directory was set; nothing to do.")
            else:
                self.eprint(f"Active contribution directory cleared (was {previous})")
        return handler

    @cli_command("Choose which test case the debugger runs against. Debugging feeds one stdin, so "
                 "it needs exactly one test. The choice is recorded per working directory and "
                 "survives until you change it; without one, debugging uses the first local test. "
                 "With no ORDINAL, shows the current selection.")
    async def cmd_contribution__select_test(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_dir: Path | None = self.args.contribution_dir
            ordinal: str | None = self.args.ordinal
            side: str = self.args.side
            resolved_dir = resolve_contribution_dir(
                    contribution_dir, settings=self.resolve_default_settings())
            manager = CgContributionManager(resolved_dir, cast(CgClient, None))

            def describe() -> str:
                chosen = manager.resolve_debug_test()
                return f"{chosen.ordinal}/{chosen.side}"

            if self.args.clear:
                manager.clear_selected_test()
                self.eprint(f"Selection cleared; debugging will use {describe()}.")
                return
            if ordinal is None:
                selected = manager.load_selected_test()
                self.eprint(f"Debugging will use {describe()}"
                            f"{'' if selected else ' (default--no explicit selection)'}.")
                return
            manager.select_test(ordinal, side)
            self.eprint(f"Selected {ordinal}/{side}.")
        p = cmd.get_parser()
        p.add_argument("ordinal", type=str, nargs="?", default=None, metavar="ORDINAL",
                       help="Ordinal directory name, e.g. '01'. Omit to show the current "
                            "selection.")
        p.add_argument("side", nargs="?", default="local", choices=["local", "validator"],
                       metavar="SIDE",
                       help="Which side of that ordinal. Defaults to 'local'.")
        p.add_argument("--clear", default=False, action="store_true",
                       help="Forget the explicit selection and fall back to the first local test.")
        return handler

    @cli_command("Print the path of the contribution working directory that would be used. Prints "
                 "nothing but the path, so it composes: cd \"$(cg contribution where)\". Exits "
                 "non-zero if no working directory can be found.")
    async def cmd_contribution__where(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_dir: Path | None = self.args.contribution_dir
            found = find_contribution_dir(contribution_dir, settings=self.resolve_default_settings())
            if found is None:
                raise CliError(
                        "No contribution working directory found. Run "
                        "`cg contribution import DIRECTORY CONTRIBUTION-ID` to create one.")
            # stdout carries the resolved path and nothing else, so this composes:
            #     $EDITOR "$(cg contribution where)/data/solution.src"
            # Anything explanatory goes to stderr, and "not found" is a non-zero exit rather than a
            # friendly line of prose a shell would happily substitute into a path.
            print(found)
        return handler

    @cli_command("Summary of this contribution: submission and review status, sync status against "
                 "the server, votes, comments and views, the moderator approve/reject gate, and "
                 "any validation in progress. Reads a local cache by default, with no network "
                 "access; --refresh fetches fresh and updates that cache. With --json, renders as "
                 "JSON instead of text.")
    async def cmd_contribution__status(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_dir: Path | None = self.args.contribution_dir
            refresh: bool = self.args.refresh
            use_json: bool = self.args.json
            resolved_dir = resolve_contribution_dir(contribution_dir, settings=self.resolve_default_settings())
            client = await self.get_client()
            manager = CgContributionManager(resolved_dir, client)
            try:
                status: CgContributionStatus = await manager.status(remote=refresh)
            except FileNotFoundError as e:
                raise CliError(str(e)) from e

            server = status.server
            refreshed_at_iso = None if status.status_cache_refreshed_at is None else _isoformat_z(status.status_cache_refreshed_at)
            moderation_autoclose_iso: str | None = None
            moderation_remaining_seconds: int | None = None
            if server is not None:
                autoclose = server.last_version.autoclose_time
                if autoclose is not None:
                    moderation_autoclose_iso = _isoformat_z(autoclose)
                    moderation_remaining_seconds = int((autoclose - datetime.now(timezone.utc)).total_seconds())

            if use_json:
                server_dict: JsonData | None = None
                if server is not None:
                    # `.meta/contribution-status.json` stores `server` whole and unredacted (see
                    # CgContributionStatusCache's docstring)--including the full statement/
                    # solution/test-case content, which is not what a "status" summary should
                    # dump. Redact it the same way the git version-data branch already does for
                    # display purposes here, and drop the resulting known-placeholder `draft`/
                    # `readyForModeration`/`type` keys rather than ship misleading values--the
                    # real ones are the top-level local* fields above.
                    server_dict = redact_commit_contribution(server).to_dict()
                    server_dict.pop("draft", None)
                    server_dict.pop("readyForModeration", None)
                    server_dict.pop("type", None)
                output: JsonData = {
                    "contributionDir": str(status.contribution_dir),
                    "pushed": status.pushed,
                    "contributionHandle": status.contribution_handle,
                    "localTitle": status.local_title,
                    "localPuzzleType": status.local_puzzle_type,
                    "localSolutionLanguage": status.local_solution_language,
                    "localDifficulty": status.local_difficulty,
                    "localDraft": status.local_draft,
                    "localReadyForModeration": status.local_ready_for_moderation,
                    "localDirty": status.local_dirty,
                    "mergeInProgress": status.merge_in_progress,
                    "syncStatus": status.sync_status.value,
                    "localVersion": status.local_version,
                    "statusCacheRefreshedAt": refreshed_at_iso,
                    "moderationAutocloseTime": moderation_autoclose_iso,
                    "moderationWindowRemainingSeconds": moderation_remaining_seconds,
                    "moderatorApprovals": None if status.moderator_approvals is None
                        else [m.to_dict() for m in status.moderator_approvals],
                    "moderatorDenials": None if status.moderator_denials is None
                        else [m.to_dict() for m in status.moderator_denials],
                    "server": server_dict,
                }
                print(json.dumps(output, indent=4, sort_keys=True))
                return

            def line(label: str, value: object) -> None:
                print(f"{label:<25}{value}")

            line("Contribution directory:", status.contribution_dir)
            line("Local title:", repr(status.local_title))
            line("Puzzle type:", status.local_puzzle_type or "(not set)")
            line("Language:", status.local_solution_language or "(not set)")
            line("Difficulty:", status.local_difficulty or "(not set)")
            line("Draft:", "yes" if status.local_draft else "no")
            line("Ready for moderation:", "yes" if status.local_ready_for_moderation else "no")
            line("Handle:", status.contribution_handle if status.pushed else "<not yet pushed>")
            line("Contribution id:", server.id if server is not None else "<not yet pushed>")
            if not status.pushed:
                line("Local edits:", "yes (uncommitted)" if status.local_dirty else "none")
                return
            if status.merge_in_progress:
                line("Sync status:", "merge in progress--run `cg contribution merge continue`/`abort`.")
            else:
                line("Sync status:", _SYNC_STATUS_TEXT[status.sync_status])
                line("Local edits:", "yes (uncommitted)" if status.local_dirty else "none")
            line("Last synced version:", status.local_version)
            if server is None:
                print("(no cached server details yet--run `cg contribution status --refresh` to fetch them)")
                return
            print()
            print(f"Server details below are as of last refresh: {refreshed_at_iso}--pass --refresh to update.")
            print()
            line("Contribution status:", server.status)
            line("Editable:", "yes" if server.editable else "no")
            line("Active version:", server.active_version)
            line("Score:", f"{server.score} (+{server.up_votes} / -{server.down_votes})")
            line("Comments:", server.comment_count)
            line("Views:", server.views)
            if moderation_remaining_seconds is not None and moderation_autoclose_iso is not None:
                if moderation_remaining_seconds > 0:
                    days, rem = divmod(moderation_remaining_seconds, 86400)
                    hours = rem // 3600
                    window_text = f"{days}d {hours}h remaining (closes {moderation_autoclose_iso})"
                else:
                    window_text = f"expired (closed {moderation_autoclose_iso})"
                line("Moderation window:", window_text)
            assert status.moderator_approvals is not None  # populated whenever `server` is
            assert status.moderator_denials is not None
            approval_names = ", ".join(m.pseudo for m in status.moderator_approvals)
            line("Approvals:", f"{len(status.moderator_approvals)}/3" + (f" ({approval_names})" if approval_names else ""))
            denial_names = ", ".join(m.pseudo for m in status.moderator_denials)
            line("Rejections:", f"{len(status.moderator_denials)}/3" + (f" ({denial_names})" if denial_names else ""))
            if server.validate_action is not None:
                va = server.validate_action
                progress_pct = round(va.progress * 100)
                done = " (done)" if va.already_done else ""
                line("Validation:", f"in progress, {progress_pct}%{done}")
            if server.status_history:
                latest = server.status_history[-1]
                line(
                        "Latest status change:",
                        f"{latest.status} at {_isoformat_z(latest.date)} ({latest.data.author}/{latest.data.reason})",
                    )
        p = cmd.get_parser()
        p.add_argument("--refresh", default=False, action="store_true",
                       help="Fetch fresh from the server first (forces `fetch()`, which also "
                            "refreshes .meta/contribution-status.json for next time), instead of "
                            "using whatever's cached there already.")
        return handler

    @cli_command("Discard local edits: reset this working directory's content to match the server "
                 "state cg already has cached. No network access -- use `cg contribution merge "
                 "discard-local` to re-fetch from the server first.")
    async def cmd_contribution__discard_local(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_dir: Path | None = self.args.contribution_dir
            resolved_dir = resolve_contribution_dir(contribution_dir, settings=self.resolve_default_settings())
            manager = CgContributionManager(resolved_dir, await self.get_client())
            working = manager.discard_local()
            self.eprint(f"{resolved_dir}: discarded local edits, now matches server (title: {working.data.title!r}).")
        return handler

    @cli_command("Delete this contribution from the server, unrecoverably, and by default remove "
                 "this entire working directory too. Pass --keep-local to detach instead: the same "
                 "local content is left ready to become a brand new contribution on the next push, "
                 "which is how you use an existing contribution as a template. Pass --keep-server "
                 "to do the opposite, leaving the server untouched and removing only your local "
                 "files. Destructive: prompts for confirmation unless --force is given, and "
                 "requires --force outright if stdin/stdout are not a terminal.")
    async def cmd_contribution__delete(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_dir: Path | None = self.args.contribution_dir
            keep_local: bool = self.args.keep_local
            keep_server: bool = self.args.keep_server
            force: bool = self.args.force
            if keep_local and keep_server:
                raise CliError(
                        "--keep-local and --keep-server are mutually exclusive--together they'd "
                        "mean deleting nothing at all."
                    )
            resolved_dir = resolve_contribution_dir(contribution_dir, settings=self.resolve_default_settings())
            manager = CgContributionManager(resolved_dir, await self.get_client(require_credentials=True))
            identity = manager.load_identity()
            if identity is None:
                raise CliError(f"{resolved_dir} has never been created/imported--nothing to delete.")
            # contribution.json's contribution_handle, not the server git branch's mere existence,
            # is authoritative for "has this ever been pushed"--see
            # CgContributionManager.push()'s docstring for why they can disagree (repair needed,
            # a corrupted/missing git-dir) and why trusting the git branch is unsafe here.
            contribution_handle = identity.contribution_handle
            # Only an error with --keep-local/--keep-server: both are explicit statements about
            # server state ("detach from"/"leave alone" the tracked contribution) that don't make
            # sense to honor silently when nothing is actually tracked yet. Plain `delete` (no
            # flags) tolerates a never-pushed directory just fine--nothing to send to
            # deleteContribution, so it just removes the local working directory.
            if contribution_handle is None and (keep_local or keep_server):
                raise CliError(
                        f"{resolved_dir} has no contribution_handle yet (never successfully "
                        "pushed)--nothing for --keep-local/--keep-server to act on."
                    )
            title = manager.load().data.title
            # Best-effort only, purely for a nicer confirmation prompt (shows the version number)--
            # may be None even when contribution_handle is set, e.g. if this working directory
            # needs repair (see above); never used to decide whether to actually delete anything,
            # only contribution_handle is.
            metadata = manager.server_metadata()
            if not force:
                if not (sys.stdin.isatty() and sys.stdout.isatty()):
                    raise CliError(
                            "Refusing to delete without confirmation: stdin/stdout aren't a "
                            "terminal. Pass --force to proceed non-interactively."
                        )
                if keep_server:
                    action = "remove ONLY the local working directory--the server-side contribution is kept, untouched"
                elif keep_local:
                    action = "detach it (server deleted, local files kept)"
                elif contribution_handle is None:
                    action = "remove the local working directory (it was never pushed--nothing exists server-side to delete)"
                else:
                    action = "PERMANENTLY DELETE it (server *and* local files)"
                print(f"About to {action}:")
                print(f"  directory: {resolved_dir}")
                if contribution_handle is None:
                    print(f"  title: {title!r} (never pushed--nothing server-side)")
                elif metadata is None:
                    print(f"  contribution: {contribution_handle!r} (title {title!r};")
                    print("    version unknown--working directory needs repair)")
                else:
                    print(f"  contribution: {contribution_handle!r} (version {metadata.version}, title {title!r})")
                reply = input("Type DELETE (all caps) to confirm, or anything else to cancel: ")
                if reply != "DELETE":
                    raise CliError("Confirmation did not match--aborted, nothing was deleted.")
            await manager.delete(keep_local=keep_local, keep_server=keep_server)
            # The directory is gone, so leaving it selected would make every later command fail with
            # a confusing "not a working directory". Scoped to *this* directory: deleting some other
            # one must not deactivate whatever you're actually working on.
            if (not keep_local
                    and await self.clear_current_working_dir("contribution", only_if=resolved_dir)):
                self.eprint("  (was the active contribution; deactivated)")
            if keep_local:
                self.eprint(
                        f"{resolved_dir}: contribution {contribution_handle!r} deleted from "
                        "the server; local working directory detached and ready for a new push."
                    )
            elif keep_server:
                self.eprint(f"{resolved_dir}: local working directory removed; the server-side contribution was left untouched.")
            elif contribution_handle is None:
                # plain `delete`, never pushed--nothing server-side to have deleted.
                self.eprint(f"{resolved_dir}: local working directory removed (it had never been pushed to the server).")
            else:
                self.eprint(
                        f"{resolved_dir}: contribution {contribution_handle!r} and the "
                        "local working directory have both been deleted."
                    )
        p = cmd.get_parser()
        p.add_argument("--keep-local", default=False, action="store_true",
                       help="Delete server-side only; keep and detach the local working "
                            "directory (ready to become a new contribution on the next push). "
                            "Mutually exclusive with --keep-server.")
        p.add_argument("--keep-server", default=False, action="store_true",
                       help="Remove only the local working directory; leave the server-side "
                            "contribution untouched (just stop tracking it locally). Mutually "
                            "exclusive with --keep-local.")
        p.add_argument("--force", "-f", default=False, action="store_true",
                       help="Skip the interactive confirmation prompt. Required if stdin/stdout "
                            "aren't a terminal.")
        return handler

    @cli_command("Renumber the ordinal directories under tests/ to a clean, sequential, "
                 "zero-padded sort key, preserving their relative order. Use it after inserting "
                 "directories such as '05a'.")
    async def cmd_contribution__renormalize_tests(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_dir: Path | None = self.args.contribution_dir
            resolved_dir = resolve_contribution_dir(contribution_dir, settings=self.resolve_default_settings())
            manager = CgContributionManager(resolved_dir, cast(CgClient, None))
            renormalize_test_case_dirs(manager.tests_dir)
            self.eprint(f"Renormalized {manager.tests_dir}")
        return handler

    @cli_command("Run the reference solution against the test cases under tests/, entirely locally "
                 "with no network access at all. Worth doing before every push: the server "
                 "validates your reference solution against every test case and rejects the whole "
                 "push if any disagree. Runs both the local and validator sides by default; "
                 "--local/--validator narrow it to one. With no ORDINAL, runs every test case; "
                 "give one or more ordinals, e.g. \"3 5 7\", matching the directory names under "
                 "tests/ with zero-padding optional. Exits non-zero if any test case fails. "
                 "Captured stdout is printed only for failing tests, unless --show-stdout.")
    async def cmd_contribution__play(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_dir: Path | None = self.args.contribution_dir
            ordinals: list[str] = self.args.ordinals
            only_local: bool = self.args.local
            only_validator: bool = self.args.validator
            update_expected: bool = self.args.update_expected
            show_stdout: bool = self.args.show_stdout
            timeout: float = self.args.timeout
            build_timeout: float = self.args.build_timeout
            resolved_dir = resolve_contribution_dir(contribution_dir, settings=self.resolve_default_settings())
            manager = CgContributionManager(
                    resolved_dir, cast(CgClient, None),
                    toolchain_dir=self.resolve_toolchain_dir(),
                    toolchain_languages=self.resolve_toolchain_languages(),
                    toolchain_image=self.resolve_toolchain_image())

            view = manager.load()
            solution_language = view.data.solution_language
            if solution_language is None:
                raise CliError(f"{manager.contribution_data_file} has no solutionLanguage set--nothing to run.")

            include_local = only_local or not (only_local or only_validator)
            include_validator = only_validator or not (only_local or only_validator)
            test_cases = manager.list_local_tests(ordinals or None, local=include_local, validator=include_validator)
            if not test_cases:
                raise CliError(f"No matching local test cases found under {manager.tests_dir}.")

            # Build once, up front, rather than per test case: a compile error is reported once
            # instead of once per test, gets its own (generous) timeout rather than eating the
            # per-test budget, and its diagnostics can never be mistaken for program output. A no-op
            # for languages that don't compile (Python3), so this costs nothing there.
            build_result = await manager.build_solution(solution_language, timeout=build_timeout)
            if build_result.output:
                self.eprint(build_result.output.rstrip())
            if not build_result.ok:
                raise CliError(f"{manager.solution_file} failed to build--no test cases were run.")

            multi = len(test_cases) > 1
            stderr_console = Console(stderr=True, highlight=False)
            results: list[CgContributionLocalTestResult] = []
            for test_case in test_cases:
                try:
                    result = await manager.run_local_test(
                            test_case, solution_language, update_expected=update_expected, timeout=timeout)
                except Exception as e:
                    result = CgContributionLocalTestResult(
                            ordinal=test_case.ordinal, side=test_case.side, title=test_case.title,
                            passed=False, updated=False, input=test_case.input_text,
                            expected_output=test_case.output_text, actual_output="", stderr="",
                            timed_out=False, returncode=-1, exception=str(e),
                        )
                results.append(result)

                status = "PASS" if result.passed else "FAIL"
                stderr_console.print(
                        f"[{status}] {result.ordinal} {result.side}: {result.title}",
                        style="bold blue", markup=False,
                    )
                if not result.passed:
                    if result.exception is not None:
                        self.eprint(f"  EXCEPTION: {result.exception}")
                    elif result.timed_out:
                        self.eprint("  timed out")
                    elif result.returncode != 0:
                        self.eprint(f"  crashed (returncode {result.returncode})")
                    elif not update_expected:
                        self.show_diff(result.expected_output, result.actual_output)
                    if result.stderr:
                        stderr_console.print("--- stderr ---", style="bold blue", markup=False)
                        self.eprint(result.stderr)
                if show_stdout or update_expected:
                    _print_captured_output(result.actual_output)
                    if multi:
                        print("-" * 72)

            if multi:
                passed_count = sum(1 for r in results if r.passed)
                stderr_console.print(f"{passed_count}/{len(results)} passed", style="bold blue", markup=False)
            if any(not r.passed for r in results):
                raise CliExit(1)
        p = cmd.get_parser()
        p.add_argument("ordinals", type=str, nargs="*", metavar="ORDINAL",
                       help="Only run these ordinals (tests/'s directory names, e.g. \"03\" or "
                            "\"3\"). Defaults to every ordinal.")
        p.add_argument("--local", action="store_true", help="Only run local-side test cases.")
        p.add_argument("--validator", action="store_true", help="Only run validator-side test cases.")
        p.add_argument("--update-expected", action="store_true",
                       help="Overwrite each test case's output.txt with its actual output instead "
                            "of comparing against it--for accepting the solution's current "
                            "behavior as the new known-good baseline. Only written for runs that "
                            "complete without crashing/timing out. Implies --show-stdout, since "
                            "the point is to review the new output.")
        p.add_argument("--show-stdout", default=False, action="store_true",
                       help="Print captured stdout even for a passing test. Always printed for a "
                            "failing test, or with --update-expected, regardless.")
        p.add_argument("--timeout", type=float, default=DEFAULT_RUN_TIMEOUT_SECONDS, metavar="SECONDS",
                       help=f"Per-test-case wall-clock timeout. Default {DEFAULT_RUN_TIMEOUT_SECONDS}.")
        p.add_argument("--build-timeout", type=float, default=DEFAULT_BUILD_TIMEOUT_SECONDS, metavar="SECONDS",
                       help="Wall-clock timeout for the one-time build step that runs before any "
                            "test case. Separate from --timeout, and far more generous, because a "
                            "cold build can pull/build a container image and compile from scratch. "
                            f"Default {DEFAULT_BUILD_TIMEOUT_SECONDS}. Ignored for languages that "
                            "need no build (e.g. Python3).")
        return handler

    @cli_command("Detect drift between the server and this working directory, and resolve it when "
                 "unambiguous: a no-op if the server has not advanced, a fast-forward if only the "
                 "server changed, or a reported conflict -- left entirely alone -- if both sides "
                 "changed. See `cg contribution diff` to inspect one and `cg contribution merge` "
                 "to resolve it.")
    async def cmd_contribution__rebase(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_dir: Path | None = self.args.contribution_dir
            resolved_dir = resolve_contribution_dir(contribution_dir, settings=self.resolve_default_settings())
            client = await self.get_client()
            manager = CgContributionManager(resolved_dir, client)
            status = await manager.rebase()
            if status == CgRebaseStatus.UP_TO_DATE:
                self.eprint(f"{resolved_dir}: up to date.")
            elif status == CgRebaseStatus.FAST_FORWARDED:
                version = self._version_str(manager.server_metadata())
                self.eprint(f"{resolved_dir}: fast-forwarded to server version {version}.")
            else:
                self.eprint(
                        f"{resolved_dir}: server and local have both changed since the last sync--conflict. "
                        "Run `cg contribution diff` to inspect, and `cg contribution merge` to resolve."
                    )
        return handler

    @staticmethod
    def _version_str(metadata: CgContributionCommitMetadata | None) -> int | str:
        """The version number from a `CgContributionManager.server_metadata()` result, for
           display--"?" if not yet populated (shouldn't normally happen where this is used)."""
        return metadata.version if metadata is not None else "?"

    async def _merge_start(self, resolved_dir: Path) -> None:
        client = await self.get_client()
        manager = CgContributionManager(resolved_dir, client)
        result = await manager.merge_start()
        if result.status == CgMergeStartStatus.ALREADY_IN_PROGRESS:
            self.eprint(
                    f"{resolved_dir}: a merge is already in progress. Run `cg contribution merge "
                    "continue` or `cg contribution merge abort`."
                )
            return
        version = self._version_str(manager.server_metadata())
        if result.status == CgMergeStartStatus.UP_TO_DATE:
            self.eprint(f"{resolved_dir}: server unchanged since the last sync, version {version}--nothing to merge.")
            return
        if not result.text_conflicts and not result.binary_conflicts:
            self.eprint(f"{resolved_dir}: merged cleanly--now at server version {version}. Nothing more to do.")
            return
        self.eprint(f"{resolved_dir}: merge started against server version {version}, with conflicts.")
        if result.text_conflicts:
            self.eprint(
                    "  text conflicts (resolve by hand, then `cg contribution merge continue`): "
                    + ", ".join(result.text_conflicts)
                )
        if result.binary_conflicts:
            self.eprint(
                    "  binary conflicts (kept local; see e.g. `cg contribution git show "
                    "server:<path>` for the server's version): " + ", ".join(result.binary_conflicts)
                )

    async def _launch_interactive_merge(self, resolved_dir: Path, tool_name: str | None) -> None:
        client = await self.get_client()
        manager = CgContributionManager(resolved_dir, client)
        result = await manager.merge_start()
        version = self._version_str(manager.server_metadata())
        if result.status == CgMergeStartStatus.UP_TO_DATE:
            self.eprint(f"{resolved_dir}: server unchanged since the last sync, version {version}--nothing to merge.")
            return
        if result.status == CgMergeStartStatus.STARTED and not result.text_conflicts and not result.binary_conflicts:
            self.eprint(f"{resolved_dir}: merged cleanly--now at server version {version}. Nothing more to do.")
            return
        # ALREADY_IN_PROGRESS, or STARTED with conflicts remaining: launch the tool either way.
        exit_code = manager.git_repo.mergetool(tool_name)
        if manager.merge_in_progress:
            self.eprint(
                    f"mergetool exited with code {exit_code}. Merge is still in progress (resolved "
                    "files are staged, but not committed)--run `cg contribution merge continue` "
                    "(or `abort`) when done."
                )
        else:
            self.eprint(f"mergetool exited with code {exit_code}. Merge already complete.")

    @cli_command("Resolve drift between the server and this working directory. Parent for the "
                 "merge state machine (start/continue/abort/interactive) and for the instant "
                 "discard-local and discard-server resolutions. Bare `cg contribution merge` is an "
                 "alias for `merge start`.")
    async def cmd_contribution__merge(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_dir: Path | None = self.args.contribution_dir
            resolved_dir = resolve_contribution_dir(contribution_dir, settings=self.resolve_default_settings())
            await self._merge_start(resolved_dir)
        return handler

    @cli_command("Begin a merge: fetch, then a real `git merge server` against the working tree. "
                 "If it completes cleanly it is already done -- no `merge continue` needed. If it "
                 "stops with conflicts, git writes conflict markers into the affected files (for a "
                 "binary conflict it keeps your local version); resolve them, then run `cg "
                 "contribution merge continue`. Does nothing, and does not error, if a merge is "
                 "already in progress or there is nothing to merge.")
    async def cmd_contribution__merge__start(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_dir: Path | None = self.args.contribution_dir
            resolved_dir = resolve_contribution_dir(contribution_dir, settings=self.resolve_default_settings())
            await self._merge_start(resolved_dir)
        return handler

    @cli_command("Finish an in-progress merge: stage everything and commit. Refuses if any path "
                 "still contains a leftover conflict marker.")
    async def cmd_contribution__merge__continue(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_dir: Path | None = self.args.contribution_dir
            resolved_dir = resolve_contribution_dir(contribution_dir, settings=self.resolve_default_settings())
            manager = CgContributionManager(resolved_dir, await self.get_client())
            manager.merge_continue()
            version = self._version_str(manager.server_metadata())
            self.eprint(f"{resolved_dir}: merge complete, now at server version {version}.")
        return handler

    @cli_command("Abort an in-progress merge: restore the working directory to its pre-merge "
                 "state. Nothing about the merge is recorded anywhere.")
    async def cmd_contribution__merge__abort(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_dir: Path | None = self.args.contribution_dir
            resolved_dir = resolve_contribution_dir(contribution_dir, settings=self.resolve_default_settings())
            manager = CgContributionManager(resolved_dir, await self.get_client())
            manager.merge_abort()
            self.eprint(f"{resolved_dir}: merge aborted; working directory restored to its pre-merge state.")
        return handler

    @cli_command("Show the current merge conflict state: during an unresolved merge, a combined "
                 "diff against both sides for each conflicted path. Same as bare `cg contribution "
                 "diff` while a merge is in progress. Fails if no merge is in progress.")
    async def cmd_contribution__merge__diff(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_dir: Path | None = self.args.contribution_dir
            resolved_dir = resolve_contribution_dir(contribution_dir, settings=self.resolve_default_settings())
            manager = CgContributionManager(resolved_dir, cast(CgClient, None))
            if not manager.merge_in_progress:
                raise CliError("No merge in progress (run `cg contribution merge` to start one).")
            self._print_diff(manager.git_repo.diff_text(), "No differences in the merge state.")
        return handler

    @cli_command("Start a merge if one is not already in progress, then launch `git mergetool` "
                 "against the working tree. The merge stays in progress after the tool exits -- "
                 "resolved files are staged, not committed -- so run `cg contribution merge "
                 "continue`, or `abort`, when done.")
    async def cmd_contribution__merge__interactive(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_dir: Path | None = self.args.contribution_dir
            resolved_dir = resolve_contribution_dir(contribution_dir, settings=self.resolve_default_settings())
            tool_name: str | None = self.args.tool
            await self._launch_interactive_merge(resolved_dir, tool_name)
        p = cmd.get_parser()
        p.add_argument("--tool", type=str, default=None, metavar="NAME",
                       help="Merge tool to use (see `git help mergetool` for the built-in choices). "
                            "Defaults to `git config merge.tool` if set (configure via `cg "
                            "contribution git config merge.tool <name>`), then git's own default.")
        return handler

    @cli_command("Discard all local edits: fetch, then move straight onto the server's new tip, "
                 "like `git reset --hard`. Unlike `cg contribution rebase`, this never checks "
                 "whether you actually diverged -- it always overwrites. Instant; does not use the "
                 "merge state machine.")
    async def cmd_contribution__merge__discard_local(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_dir: Path | None = self.args.contribution_dir
            resolved_dir = resolve_contribution_dir(contribution_dir, settings=self.resolve_default_settings())
            manager = CgContributionManager(resolved_dir, await self.get_client())
            working = await manager.merge_discard_local()
            self.eprint(f"{resolved_dir}: discarded local changes--now matches the server (title: {working.data.title!r}).")
        return handler

    @cli_command("Update the local server and version-data branches to match the server, leaving "
                 "your working tree untouched. Same as `cg contribution fetch`. Instant; does not "
                 "use the merge state machine.")
    async def cmd_contribution__merge__discard_server(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_dir: Path | None = self.args.contribution_dir
            resolved_dir = resolve_contribution_dir(contribution_dir, settings=self.resolve_default_settings())
            manager = CgContributionManager(resolved_dir, await self.get_client())
            contribution = await manager.merge_discard_server()
            self.eprint(
                    f"{resolved_dir}: server now matches the server (version "
                    f"{contribution.last_version.version}); working directory content left untouched."
                )
        return handler

    def _print_diff(self, text: str, no_changes_message: str) -> None:
        if text:
            print(text, end="")
        else:
            self.eprint(no_changes_message)

    @cli_command("Show what has changed between your working tree and the server state cg has "
                 "cached -- no network access by default. Pass --remote to fetch fresh first, or "
                 "--interactive to launch `git mergetool` instead of printing text. If a merge is "
                 "in progress, shows the merge's own conflict state instead, and --remote is "
                 "refused, since fetching mid-merge is not allowed.")
    async def cmd_contribution__diff(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_dir: Path | None = self.args.contribution_dir
            resolved_dir = resolve_contribution_dir(contribution_dir, settings=self.resolve_default_settings())
            interactive: bool = self.args.interactive
            tool_name: str | None = self.args.tool
            remote: bool = self.args.remote

            if interactive:
                await self._launch_interactive_merge(resolved_dir, tool_name)
                return

            client = await self.get_client()
            manager = CgContributionManager(resolved_dir, client)

            if manager.merge_in_progress:
                if remote:
                    raise CliError(
                            "A merge is in progress--can't fetch (`--remote`) until it's resolved "
                            "(see `cg contribution merge continue`/`abort`)."
                        )
                self._print_diff(manager.git_repo.diff_text(), "No differences in the merge state.")
                return

            if remote:
                await manager.fetch()
            version = self._version_str(manager.server_metadata())
            self._print_diff(
                    manager.git_repo.diff_text(SERVER_BRANCH_NAME),
                    f"No local changes since server version {version}.",
                )
        p = cmd.get_parser()
        p.add_argument("--remote", default=False, action="store_true",
                       help="Fetch fresh from the server first, instead of using whatever's cached.")
        p.add_argument("--interactive", default=False, action="store_true",
                       help="Launch git mergetool instead of printing a text diff.")
        p.add_argument("--tool", type=str, default=None, metavar="NAME",
                       help="Merge tool to use with --interactive--see `git help mergetool`.")
        return handler

    @cli_command("Refresh cg's cached copy of the server state. Leaves it untouched if the version "
                 "has not changed, and skips re-downloading the cover image if it has not changed "
                 "either. `cg contribution rebase` and `cg contribution merge start` do this for "
                 "you; use this to refresh the cache for `cg contribution diff` without either. "
                 "Refuses while a merge is in progress.")
    async def cmd_contribution__fetch(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_dir: Path | None = self.args.contribution_dir
            resolved_dir = resolve_contribution_dir(contribution_dir, settings=self.resolve_default_settings())
            client = await self.get_client()
            manager = CgContributionManager(resolved_dir, client)
            contribution = await manager.fetch()
            self.eprint(f"{resolved_dir}: server refreshed (version {contribution.last_version.version}).")
        return handler

    @cli_command("Run a raw git command against this contribution's repository -- e.g. `cg "
                 "contribution git log --oneline --all --decorate`, `cg contribution git show "
                 "server:solution.py`, `cg contribution git config merge.tool meld`. Resolves "
                 "--git-dir and --work-tree for you; plain `git` run by hand here cannot find this "
                 "repository at all. No `--` needed, and nothing you pass is ever misread as one "
                 "of cg's own options.")
    async def cmd_contribution__git(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_dir: Path | None = self.args.contribution_dir
            resolved_dir = resolve_contribution_dir(contribution_dir, settings=self.resolve_default_settings())
            manager = CgContributionManager(resolved_dir, cast(CgClient, None))
            try:
                git_dir = manager.git_dir
            except FileNotFoundError as e:
                raise CliError(str(e)) from e
            git_args: list[str] = self.args.git_args
            argv = ["git", f"--git-dir={git_dir}", f"--work-tree={manager.data_dir}", *git_args]
            result = subprocess.run(argv, cwd=manager.data_dir, check=False)
            raise CliExit(result.returncode)
        # Built by hand (not via cmd.get_parser(), which the framework would otherwise
        # auto-construct with the usual "-"-prefixed option parsing): prefix_chars set to a
        # character no real git flag starts with means this parser has no concept of an
        # "option-looking token" at all--everything after "git" (including e.g. "-h"/"--oneline")
        # is just a plain positional string to it, verified directly against this exact
        # argparse_wizard/Python version. set_parser() short-circuits the framework's own
        # get_parser() (which only auto-constructs when self.parser is still None) so this
        # replaces it cleanly rather than fighting it.
        parent_subparsers = cmd.get_parent_subparsers_action()
        # help=cmd.help would pass None straight through here (no @cli_command(help=...) was
        # given, just the positional description), which argparse renders as *no* one-line
        # summary in the parent's subcommand list--unlike the framework's own get_parser(), this
        # hand-built add_parser() call doesn't fall back to the description for us, so it must be
        # done explicitly.
        help_text = cmd.description if cmd.help is None else cmd.help
        parser = parent_subparsers.add_parser(
                cmd.short_name, description=cmd.description, help=help_text,
                prefix_chars="\x00", add_help=False,
            )
        cmd.set_parser(parser)
        parser.add_argument("git_args", nargs="*")
        return handler

    @cli_command("Solve an existing CodinGame puzzle in a local working directory: import it, edit "
                 "one file, run its test cases locally, and submit. Currently supports classic "
                 "PUZZLE_INOUT puzzles.")
    async def cmd_puzzle(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        p = cmd.get_parser()
        p.add_argument("--puzzle-dir", "-d", type=Path, default=None, metavar="DIR",
                       help="Working directory to operate on. Defaults to CG_PUZZLE_DIR, then "
                            "the configured default (`cg settings set puzzle-dir`), then the "
                            "current directory or \"./puzzle\" if it contains puzzle.json.")
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Create a puzzle working directory and pull the puzzle into it "
                 "(Puzzle/generateSessionFromPuzzlePrettyId, then TestSession/startTestSession). "
                 "PUZZLE is resolved in this order: numeric puzzle ID; exact pretty ID, e.g. "
                 "'literary-alfabet-soupe'; exact title; case-insensitive title. If you have "
                 "attempted the puzzle before, your saved answer is imported in whatever language "
                 "you last used; otherwise you get a placeholder solution in --language. Makes "
                 "DIRECTORY the active puzzle.")
    async def cmd_puzzle__import(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            puzzle_ref: str = self.args.puzzle_ref
            language: str | None = self.args.language
            resolved_dir = Path(self.args.directory).expanduser().resolve()
            client = await self.get_client()
            manager = CgPuzzleManager(resolved_dir, client)
            puzzle_data = await manager.import_(puzzle_ref, language=language)
            server_data = manager.load_server_data()
            assert server_data is not None
            await self.set_current_working_dir("puzzle", resolved_dir)
            self.eprint(f"Imported puzzle {server_data.puzzle_pretty_id!r} into {resolved_dir}")
            self.eprint(f"  title: {server_data.title!r}")
            self.eprint(f"  solutionLanguage: {puzzle_data.solution_language!r}")
            self.eprint("  (now the active puzzle--`cg puzzle where` prints it, "
                        "`cg puzzle deactivate` clears it)")
        p = cmd.get_parser()
        p.add_argument("directory", type=Path, metavar="DIRECTORY",
                       help="Directory to build the working directory in. Required and always "
                            "first, matching `cg contribution import`/`create`. Becomes the active "
                            "puzzle directory (see `cg puzzle activate`).")
        p.add_argument("puzzle_ref", type=str, metavar="PUZZLE",
                       help="A puzzle reference: numeric puzzle ID, pretty ID (displayed title, "
                            "lowercased with spaces replaced by hyphens, e.g. "
                            "'literary-alfabet-soupe'), exact title, or case-insensitive title--"
                            "tried in that order until one resolves to a real puzzle.")
        p.add_argument("--language", "-l", type=str, default=None, metavar="LANGUAGE",
                       help="Language to start in, e.g. 'C++'. Restores your most recent saved code "
                            "for that language, or writes a placeholder if you've never used it "
                            "here. Omit to use whichever language you last used for this puzzle "
                            "(or Python3 if you've never attempted it at all).")
        return handler

    @cli_command("Rebuild .meta/ -- the test-session handle and the cached statement and "
                 "stub-generator copies -- from puzzle.json, without touching data/. Use it after "
                 "cloning a repo that does not carry .meta/ (it is gitignored), or if anything in "
                 "the cache looks wrong. Deleting .meta/ and repairing is always safe.")
    async def cmd_puzzle__repair(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            puzzle_dir: Path | None = self.args.puzzle_dir
            resolved_dir = resolve_puzzle_dir(puzzle_dir, settings=self.resolve_default_settings())
            client = await self.get_client()
            manager = CgPuzzleManager(resolved_dir, client)
            server_data = await manager.repair()
            self.eprint(f"{resolved_dir}: repaired")
            self.eprint(f"  title: {server_data.title!r}")
            self.eprint(f"  puzzlePrettyId: {server_data.puzzle_pretty_id!r}")
        return handler

    @cli_command("Submit your solution for credit (TestSession/submit): a real, permanent, graded "
                 "submission, validated against the puzzle's hidden validator test cases. There is "
                 "no undo. A puzzle with many heavy validators can take a while, since the server "
                 "runs your code once per validator. Use `cg puzzle play` to test locally first.")
    async def cmd_puzzle__submit(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            puzzle_dir: Path | None = self.args.puzzle_dir
            resolved_dir = resolve_puzzle_dir(puzzle_dir, settings=self.resolve_default_settings())
            client = await self.get_client(require_credentials=True)
            manager = CgPuzzleManager(resolved_dir, client)
            report = await manager.submit()
            if self.args.json:
                print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
                return
            # submit() only returns once find_report_by_submission_when_ready() confirms grading
            # is done, so every field below is guaranteed populated--see CgSubmissionReport.
            assert report.is_ready()
            assert report.score is not None
            assert report.best_score is not None
            assert report.achievements_completed is not None
            assert report.validators is not None
            self.eprint(f"Submission {report.submission_id}: score {report.score:.1f}/100 "
                        f"(best {report.best_score:.1f}/100)")
            self.eprint(f"Achievements completed: {'yes' if report.achievements_completed else 'no'}")
            for validator in report.validators:
                status = "PASS" if validator.success else "FAIL"
                self.eprint(f"  [{status}] {validator.name} (difficulty {validator.difficulty})")
        return handler

    @cli_command("Run your solution against the puzzle's test cases on CodinGame's servers "
                 "(TestSession/play) -- the IDE's \"Test\" button. Not a submission and not graded, "
                 "but it does durably overwrite the code CodinGame has saved for this puzzle and "
                 "language. Prefer `cg puzzle play`, which runs locally with no network access and "
                 "no side effects. With no TEST-INDEX, runs every downloaded test case; give one "
                 "or more 1-based indices to run only those. Exits non-zero if any test fails. "
                 "Captured stdout is printed only for failing tests, unless --show-stdout.")
    async def cmd_puzzle__play_server(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            puzzle_dir: Path | None = self.args.puzzle_dir
            test_indices: list[int] = self.args.test_indices
            show_stdout: bool = self.args.show_stdout
            resolved_dir = resolve_puzzle_dir(puzzle_dir, settings=self.resolve_default_settings())
            client = await self.get_client(require_credentials=True)
            manager = CgPuzzleManager(resolved_dir, client)
            # Resolved and looped here, rather than delegating the whole batch to manager.play(),
            # specifically so each result can be displayed as soon as it's available--one test
            # case's server-side run can take a while, and buffering every result before showing
            # any of them would make a multi-test run look stalled for no reason.
            indices = manager.resolve_play_indices(test_indices or None)
            any_failed = False
            passed_count = 0
            stderr_console = Console(stderr=True, highlight=False)
            for index in indices:
                item = await manager.play_one(index)
                result = item.result
                failed = result.error is not None or not result.comparison.success
                any_failed = any_failed or failed
                passed_count += 0 if failed else 1
                status = "FAIL" if failed else "PASS"
                stderr_console.print(f"[{status}] test {item.index} ({item.label})", style="bold blue", markup=False)
                if failed:
                    if result.error is not None:
                        self.eprint(f"  ERROR: {result.error.message}")
                    if result.comparison.expected is not None and result.comparison.found is not None:
                        self.show_diff(result.comparison.expected, result.comparison.found)
                    if result.output:
                        stderr_console.print("--- output ---", style="bold blue", markup=False)
                        _print_captured_output(result.output)
                elif show_stdout:
                    _print_captured_output(result.output)
            if len(indices) > 1:
                stderr_console.print(f"{passed_count}/{len(indices)} passed", style="bold blue", markup=False)
            if any_failed:
                raise CliExit(1)
        p = cmd.get_parser()
        p.add_argument("test_indices", type=int, nargs="*", metavar="TEST-INDEX",
                       help="1-based test case index/indices to run against. With none "
                            "given, runs every downloaded test case.")
        p.add_argument("--show-stdout", default=False, action="store_true",
                       help="Print captured stdout even for a passing test. Always printed for "
                            "a failing/errored test regardless.")
        return handler

    @cli_command("Run your solution against the puzzle's downloaded test cases entirely locally, "
                 "with no network access at all. Output is compared exactly as CodinGame compares "
                 "it, so a pass here means a pass there. Supports Python3 and C++; C++ builds and "
                 "runs in a container, so no local toolchain is needed. With no TEST-INDEX, runs "
                 "every test case; give one or more 1-based indices to run only those. Exits "
                 "non-zero if any test fails. Captured stdout is printed only for failing tests, "
                 "unless --show-stdout.")
    async def cmd_puzzle__play(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            puzzle_dir: Path | None = self.args.puzzle_dir
            test_indices: list[int] = self.args.test_indices
            show_stdout: bool = self.args.show_stdout
            timeout: float = self.args.timeout
            build_timeout: float = self.args.build_timeout
            resolved_dir = resolve_puzzle_dir(puzzle_dir, settings=self.resolve_default_settings())
            manager = CgPuzzleManager(
                    resolved_dir, cast(CgClient, None),
                    toolchain_dir=self.resolve_toolchain_dir(),
                    toolchain_languages=self.resolve_toolchain_languages(),
                    toolchain_image=self.resolve_toolchain_image())
            stderr_console = Console(stderr=True, highlight=False)
            # Resolved and looped here, rather than delegating the whole batch to
            # manager.play_local(), so each result is displayed as soon as it's available--see
            # `cg puzzle play-server`'s handler for the same reasoning (network latency there;
            # a slow/near-timeout local subprocess run here).
            test_cases = manager.resolve_play_local_test_cases(test_indices or None)

            # Build once, up front, rather than per test case: a compile error is reported once
            # instead of once per test, gets its own (generous) timeout rather than eating the
            # per-test budget, and its diagnostics can never be mistaken for program output. A no-op
            # for languages that don't compile (Python3), so this costs nothing there.
            build_result = await manager.build_solution(timeout=build_timeout)
            if build_result.output:
                self.eprint(build_result.output.rstrip())
            if not build_result.ok:
                raise CliError(f"{manager.solution_file} failed to build--no test cases were run.")

            any_failed = False
            passed_count = 0
            for test_case in test_cases:
                result = await manager.play_local_one(test_case, timeout=timeout)
                status = "PASS" if result.passed else "FAIL"
                stderr_console.print(f"[{status}] test {result.index} ({result.label})", style="bold blue", markup=False)
                if not result.passed:
                    any_failed = True
                    if result.timed_out:
                        self.eprint("  timed out")
                    self.show_diff(result.expected_output, result.actual_output)
                    if result.stderr:
                        stderr_console.print("--- stderr ---", style="bold blue", markup=False)
                        self.eprint(result.stderr)
                else:
                    passed_count += 1
                    if show_stdout:
                        _print_captured_output(result.actual_output)
            if len(test_cases) > 1:
                stderr_console.print(f"{passed_count}/{len(test_cases)} passed", style="bold blue", markup=False)
            if any_failed:
                raise CliExit(1)
        p = cmd.get_parser()
        p.add_argument("test_indices", type=int, nargs="*", metavar="TEST-INDEX",
                       help="1-based downloaded test case index/indices to run (see "
                            ".meta/tests/<index>/). With none given, runs every downloaded test "
                            "case.")
        p.add_argument("--show-stdout", default=False, action="store_true",
                       help="Print captured stdout even for a passing test. Always shown for a "
                            "failing test (as part of its diff) regardless.")
        p.add_argument("--timeout", type=float, default=DEFAULT_RUN_TIMEOUT_SECONDS, metavar="SECONDS",
                       help=f"Per-test-case wall-clock timeout. Default {DEFAULT_RUN_TIMEOUT_SECONDS}.")
        p.add_argument("--build-timeout", type=float, default=DEFAULT_BUILD_TIMEOUT_SECONDS, metavar="SECONDS",
                       help="Wall-clock timeout for the one-time build step that runs before any "
                            "test case. Separate from --timeout, and far more generous, because a "
                            "cold build can pull/build a container image and compile from scratch. "
                            f"Default {DEFAULT_BUILD_TIMEOUT_SECONDS}. Ignored for languages that "
                            "need no build (e.g. Python3).")
        return handler

    @cli_command("Print the puzzle's problem statement from the local cache -- no network access. "
                 "Run `cg puzzle import` or `cg puzzle repair` first if it is missing. Section "
                 "headers and the example input/output are colour-highlighted on a terminal. With "
                 "--json, prints the parsed [{kind, text}, ...] blocks instead.")
    async def cmd_puzzle__description(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            puzzle_dir: Path | None = self.args.puzzle_dir
            use_json: bool = self.args.json
            resolved_dir = resolve_puzzle_dir(puzzle_dir, settings=self.resolve_default_settings())
            manager = CgPuzzleManager(resolved_dir, cast(CgClient, None))
            html = manager.load_statement_html()
            if html is None:
                raise CliError(
                        f"{manager.statement_file} does not exist--run `cg puzzle import` or "
                        "`cg puzzle repair` first."
                    )
            blocks = parse_statement_html(html)

            if use_json:
                print(json.dumps([{"kind": b.kind, "text": b.text} for b in blocks], indent=2))
                return

            console = self.get_console()
            for i, block in enumerate(blocks):
                if block.kind == "header":
                    console.print(block.text, style="bold blue", markup=False)
                elif block.kind in ("example_input", "example_output"):
                    console.print(block.text, style="yellow", markup=False)
                else:
                    console.print(block.text, markup=False)
                if i != len(blocks) - 1:
                    console.print()
        return handler

    @cli_command("Show a unified diff between your local solution and the answer the server "
                 "currently has for this puzzle.")
    async def cmd_puzzle__diff(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            puzzle_dir: Path | None = self.args.puzzle_dir
            resolved_dir = resolve_puzzle_dir(puzzle_dir, settings=self.resolve_default_settings())
            client = await self.get_client(require_credentials=True)
            manager = CgPuzzleManager(resolved_dir, client)
            diff_text = await manager.diff()
            if diff_text:
                print(diff_text, end="")
            else:
                self.eprint(f"{resolved_dir}: no differences from the server's last-submitted answer.")
        return handler

    @cli_command("Summary of this puzzle: title, language, and whether you have local edits. "
                 "Entirely local by default; --refresh also compares against the server's "
                 "last-submitted answer and fetches your live progress and score. With --json, "
                 "renders as JSON instead of text.")
    async def cmd_puzzle__status(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            puzzle_dir: Path | None = self.args.puzzle_dir
            refresh: bool = self.args.refresh
            use_json: bool = self.args.json
            resolved_dir = resolve_puzzle_dir(puzzle_dir, settings=self.resolve_default_settings())
            client = await self.get_client()
            manager = CgPuzzleManager(resolved_dir, client)
            try:
                status: CgPuzzleStatus = await manager.status(refresh=refresh)
            except (FileNotFoundError, CgPuzzleManagerError) as e:
                raise CliError(str(e)) from e

            progress = status.progress
            last_activity_iso = None if progress is None or progress.last_activity is None else _isoformat_z(progress.last_activity)

            if use_json:
                output: JsonData = {
                    "puzzleDir": str(status.puzzle_dir),
                    "puzzleId": status.puzzle_id,
                    "puzzleHandle": status.puzzle_handle,
                    "title": status.title,
                    "puzzlePrettyId": status.puzzle_pretty_id,
                    "puzzleType": status.puzzle_type,
                    "difficulty": status.difficulty,
                    "solutionLanguage": status.solution_language,
                    "localDirty": status.local_dirty,
                    "progress": None if progress is None else progress.to_dict(),
                }
                print(json.dumps(output, indent=4, sort_keys=True))
                return

            def line(label: str, value: object) -> None:
                print(f"{label:<20}{value}")

            line("Puzzle directory:", status.puzzle_dir)
            line("Title:", repr(status.title))
            line("Pretty id:", status.puzzle_pretty_id)
            line("Puzzle id:", status.puzzle_id)
            line("Handle:", status.puzzle_handle)
            line("Puzzle type:", status.puzzle_type or "(unknown--run `cg puzzle repair`)")
            line("Difficulty:", status.difficulty or "(unknown--run `cg puzzle repair`)")
            line("Language:", status.solution_language)
            if status.local_dirty is None:
                line("Local edits:", "not checked--pass --refresh to check")
            else:
                line("Local edits:", "yes (differs from server)" if status.local_dirty else "none")
            if not refresh:
                return
            if progress is None:
                print()
                print("No live progress found for this puzzle id.")
                return
            print()
            print("Live progress below is current as of this call.")
            print()
            line("Level:", progress.level)
            solved = progress.validator_score == 100
            line("Solved:", f"{'yes' if solved else 'no'} ({progress.validator_score}/100)")
            line("Solved by:", f"{progress.solved_count} codingamers")
            line("Attempts:", progress.attempt_count)
            line("XP:", progress.xp_points)
            if last_activity_iso is not None:
                line("Last activity:", last_activity_iso)
        p = cmd.get_parser()
        p.add_argument("--refresh", default=False, action="store_true",
                       help="Also check for local edits against the server's last-submitted "
                            "answer and fetch live progress/score (two live calls).")
        return handler

    @cli_command("Throw local edits away: replace your solution with the answer the server "
                 "currently has for this puzzle. Reads from the server; changes nothing there.")
    async def cmd_puzzle__discard_local(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            puzzle_dir: Path | None = self.args.puzzle_dir
            resolved_dir = resolve_puzzle_dir(puzzle_dir, settings=self.resolve_default_settings())
            client = await self.get_client(require_credentials=True)
            manager = CgPuzzleManager(resolved_dir, client)
            result = await manager.discard_local()
            self.eprint(
                    f"{resolved_dir}: discarded local edits, now matches the server's "
                    f"last-submitted answer (language: {result.solution_language!r})."
                )
        return handler

    @cli_command("Debug-session plumbing for languages whose debugger attaches to a running "
                 "target, such as C++ in its container. Normally invoked for you by the VS Code "
                 "configuration `cg vscode install` generates, rather than typed by hand. "
                 "Languages whose debugger launches the program itself, such as Python3, never use "
                 "these.")
    async def cmd_puzzle__debug(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None

    @cli_command("Build the debug profile and start a stopped debug target fed by TEST-INDEX's "
                 "input, ready for a debugger to attach. Prints the connection details.")
    async def cmd_puzzle__debug__start(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            puzzle_dir: Path | None = self.args.puzzle_dir
            test_index: int = self.args.test_index
            build_timeout: float = self.args.build_timeout
            resolved_dir = resolve_puzzle_dir(puzzle_dir, settings=self.resolve_default_settings())
            manager = CgPuzzleManager(
                    resolved_dir, cast(CgClient, None),
                    toolchain_dir=self.resolve_toolchain_dir(),
                    toolchain_languages=self.resolve_toolchain_languages(),
                    toolchain_image=self.resolve_toolchain_image())
            try:
                session = await manager.start_debug_session(test_index, timeout=build_timeout)
            except CgLanguageOperationNotSupportedError as e:
                raise CliError(str(e)) from e
            if session.output:
                self.eprint(session.output.rstrip())
            if not session.ok:
                raise CliExit(1)
            for key, value in session.details.items():
                print(f"{key}: {value}")
        p = cmd.get_parser()
        p.add_argument("test_index", type=int, metavar="TEST-INDEX",
                       help="Downloaded test case index whose input.txt feeds the debugged run.")
        p.add_argument("--build-timeout", type=float, default=DEFAULT_BUILD_TIMEOUT_SECONDS,
                       metavar="SECONDS", help="Wall-clock timeout for the debug build.")
        return handler

    @cli_command("Stop a debug target started by `cg puzzle debug start`. Always succeeds, "
                 "including when nothing is running.")
    async def cmd_puzzle__debug__stop(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            puzzle_dir: Path | None = self.args.puzzle_dir
            resolved_dir = resolve_puzzle_dir(puzzle_dir, settings=self.resolve_default_settings())
            manager = CgPuzzleManager(
                    resolved_dir, cast(CgClient, None),
                    toolchain_dir=self.resolve_toolchain_dir(),
                    toolchain_languages=self.resolve_toolchain_languages(),
                    toolchain_image=self.resolve_toolchain_image())
            await manager.stop_debug_session()
        return handler

    @cli_command("Compile your solution, if its language needs compiling (a no-op for interpreted "
                 "languages such as Python3). You rarely need this -- `cg puzzle play` builds "
                 "first automatically -- but it is useful to compile without running, or to warm a "
                 "cold container image up front. Near-instant when the source has not changed "
                 "since the last successful build. Compiler diagnostics go to stderr.")
    async def cmd_puzzle__build(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            puzzle_dir: Path | None = self.args.puzzle_dir
            profile: str = self.args.profile
            build_timeout: float = self.args.build_timeout
            resolved_dir = resolve_puzzle_dir(puzzle_dir, settings=self.resolve_default_settings())
            manager = CgPuzzleManager(
                    resolved_dir, cast(CgClient, None),
                    toolchain_dir=self.resolve_toolchain_dir(),
                    toolchain_languages=self.resolve_toolchain_languages(),
                    toolchain_image=self.resolve_toolchain_image())
            result = await manager.build_solution(
                    profile=cast(CgBuildProfile, profile), timeout=build_timeout)
            if result.output:
                self.eprint(result.output.rstrip())
            if not result.ok:
                raise CliExit(1)
            self.eprint("up to date" if result.up_to_date else "built")
        p = cmd.get_parser()
        p.add_argument("--profile", choices=["run", "debug"], default="run",
                       help="Which build to produce. \"debug\" is built for debuggability rather "
                            "than speed (no optimization, full symbols) and is what a debug session "
                            "uses. Ignored by languages that need no build. Default: run.")
        p.add_argument("--build-timeout", type=float, default=DEFAULT_BUILD_TIMEOUT_SECONDS,
                       metavar="SECONDS",
                       help="Wall-clock timeout. Generous by default, because a cold build can pull "
                            f"and build a container image. Default {DEFAULT_BUILD_TIMEOUT_SECONDS}.")
        return handler

    @cli_command("Manage the Docker containers and images cg builds for languages that run in a "
                 "container. One image carries every language cg can containerize (see `cg docker "
                 "toolchain list`); C++ is currently the one with a container-backed build, run and "
                 "debug path. Nothing here holds anything you authored--see `cg docker clean`.")
    async def cmd_docker(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None

    @cli_command("Remove every container and image cg created, across all working directories. "
                 "Always safe and never prompts: a container holds only build artifacts and an "
                 "image is rebuilt from Dockerfiles on disk, so nothing you authored lives in "
                 "either--the next build recreates whatever is needed. Useful to reclaim disk "
                 "space, or to force a clean rebuild after editing a toolchain Dockerfile.")
    async def cmd_docker__clean(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            result = await clean_managed()
            if not result.docker_available:
                self.eprint("docker isn't installed--nothing to clean.")
                return
            for name in result.containers:
                print(f"removed container {name}")
            for image_id in result.images:
                print(f"removed image {image_id}")
            self.eprint(
                    f"removed {len(result.containers)} container(s) and "
                    f"{len(result.images)} image(s)."
                )
        return handler

    @cli_command("Inspect and build the multi-language toolchain image that containerized languages "
                 "run in. One image serves every language, composed from dependency-ordered "
                 "fragments--so building it for two languages that share a toolchain (C and C++, or "
                 "JavaScript and TypeScript) installs that toolchain once.")
    async def cmd_docker__toolchain(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None

    def _requested_toolchain_languages(self) -> list[str] | None:
        """The language set for a `cg docker toolchain` command: --languages, else the configured
           `toolchainLanguages`, else None meaning "everything cg supports"."""
        languages: list[str] | None = self.args.languages
        if languages is not None:
            # Accept both `--languages C++ Python3` and `--languages "C++,Python3"`, since a comma
            # list is what a settings file or CI variable naturally holds.
            flattened = [part.strip() for item in languages for part in item.split(",")]
            return [part for part in flattened if part]
        return self.resolve_default_settings().toolchain_languages

    def _add_languages_argument(self, cmd: CliCommand[Self]) -> None:
        cmd.get_parser().add_argument(
                "--languages", "-l", nargs="+", metavar="LANGUAGE",
                help="CodinGame language names to include, e.g. \"C++\" Python3 (comma-separated "
                     "also accepted). Defaults to the configured toolchainLanguages, or to every "
                     "language cg can containerize. The full set is ~1.9GB--far less than the sum "
                     "of its parts, because the large toolchains share one Debian base--so trimming "
                     "it saves less than you would expect.")

    @cli_command("List the toolchain fragments cg knows about: the languages that can go into an "
                 "image, and the shared subsystems they install onto. A language usually installs "
                 "nothing itself and just depends on a subsystem, which is what lets C and C++, or "
                 "Java and Scala, coexist without either owning the global environment.")
    async def cmd_docker__toolchain__list(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            fragments = all_fragments()
            languages = default_languages()
            by_language = {name: resolve_language_slugs([name])[0] for name in languages}
            if self.args.json:
                print(json.dumps({
                    "languages": dict(sorted(by_language.items())),
                    "fragments": {
                        slug: {"version": f.version, "dependsOn": list(f.depends_on),
                               "installs": bool(f.dockerfile), "activates": bool(f.env_script)}
                        for slug, f in sorted(fragments.items())
                    },
                }, indent=2, sort_keys=True))
                return
            language_slugs = set(by_language.values())
            print("Languages (each may be named to --languages):")
            for name, slug in sorted(by_language.items()):
                depends = ", ".join(fragments[slug].depends_on) or "-"
                print(f"  {name:<14} fragment {slug:<12} depends on {depends}")
            print("\nSubsystems (installed on demand, never named directly):")
            for slug, fragment in sorted(fragments.items()):
                if slug in language_slugs:
                    continue
                depends = ", ".join(fragment.depends_on) or "-"
                print(f"  {slug:<14} v{fragment.version:<11} depends on {depends}")
        return handler

    @cli_command("Print the Dockerfile cg would compose for a set of languages, without building "
                 "anything. Fragments appear in dependency order, deterministically, so a subset's "
                 "output is a prefix of a superset's--which is what lets their images share layers.")
    async def cmd_docker__toolchain__show(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            languages = self._requested_toolchain_languages()
            rendered = render_dockerfile(
                    fragments_for_languages(
                            languages if languages is not None else default_languages()),
                    base_image=BASE_IMAGE, preamble=PREAMBLE,
                )
            # The tag has to cover what would actually be built--cg's base *plus* the user's
            # custom.dockerfile--or it names an image that will never exist. Composed in memory, so
            # asking a read-only question never overwrites their base.dockerfile.
            composed = compose_with_base(self.resolve_toolchain_dir(), rendered)
            print(composed if self.args.composed else rendered, end="")
            self.eprint(f"image tag would be {image_tag_for(composed)}")
        self._add_languages_argument(cmd)
        cmd.get_parser().add_argument(
                "--composed", action="store_true",
                help="Print your custom.dockerfile appended too, i.e. exactly what would be piped "
                     "to `docker build`. The reported image tag already covers it either way.")
        return handler

    @cli_command("Build the toolchain image ahead of time, instead of letting the first run build "
                 "it. With no options this produces exactly the image `cg puzzle play` would build, "
                 "under the same content-addressed tag, so a later run finds it already there.")
    async def cmd_docker__toolchain__build(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            platforms: list[str] = self.args.platform or []
            push: bool = self.args.push
            tag: str | None = self.args.tag
            timeout: float = self.args.build_timeout

            languages = self._requested_toolchain_languages()
            rendered = render_dockerfile(
                    fragments_for_languages(
                            languages if languages is not None else default_languages()),
                    base_image=BASE_IMAGE, preamble=PREAMBLE,
                )
            directory = self.resolve_toolchain_dir()
            _, warnings = ensure_base_dockerfile(directory, rendered)
            for warning in warnings:
                self.eprint(f"warning: {warning}")

            content = compose_dockerfile(directory)
            content_tag = image_tag_for(content)
            if push and tag is None:
                raise CliError(
                        "--push needs --tag: a content-addressed tag like "
                        f"{content_tag!r} has no registry in it, so there is nowhere to push to. "
                        "Pass e.g. --tag docker.io/you/cg-toolchain:v1.")

            if platforms or push:
                await build_image_content(
                        content, tag=tag or content_tag, platforms=platforms, push=push,
                        timeout=timeout)
                self.eprint(f"{'pushed' if push else 'built'} {tag or content_tag}"
                            f"{' for ' + ', '.join(platforms) if platforms else ''}")
                return

            # The ordinary path deliberately goes through ensure_image, the same function the run
            # path uses, so a prebuild and a first run cannot disagree about what to build or what
            # to call it. It also re-points the :latest alias.
            built = await ensure_image(directory, timeout=timeout)
            if tag is not None:
                await tag_image(built, tag)
            self.eprint(f"built {built}" + (f", tagged {tag}" if tag else ""))
        self._add_languages_argument(cmd)
        p = cmd.get_parser()
        p.add_argument("--tag", "-t", metavar="TAG",
                       help="Additional tag for the built image. Required with --push, which needs "
                            "a registry-qualified name. Without it the image gets only its "
                            "content-addressed tag, which is what the run path looks for.")
        p.add_argument("--platform", action="append", metavar="PLATFORM",
                       help="Target platform, e.g. linux/amd64 or linux/arm64. Repeatable. Needs "
                            "`docker buildx`. More than one requires --push: a multi-platform image "
                            "is a manifest list, which the local daemon cannot store. Default: this "
                            "machine's architecture.")
        p.add_argument("--push", action="store_true",
                       help="Push to a registry rather than loading into the local Docker daemon. "
                            "The only way to produce a multi-architecture image.")
        p.add_argument("--build-timeout", type=float, default=DEFAULT_TOOLCHAIN_BUILD_TIMEOUT_SECONDS,
                       metavar="SECONDS",
                       help="Wall-clock timeout. Generous by default: a cold all-languages build "
                            "downloads a JDK, a .NET SDK and a Node tarball. Default "
                            f"{DEFAULT_TOOLCHAIN_BUILD_TIMEOUT_SECONDS}.")
        return handler

    @cli_command("Show the puzzle's editable fields, or set one. Each field is its own "
                 "subcommand, so `cg puzzle set solution-language --help` documents what it "
                 "accepts. Bare `cg puzzle set` lists every field and its current value. These "
                 "live in data/puzzle-data.json.")
    async def cmd_puzzle__set(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            resolved_dir = resolve_puzzle_dir(
                    self.args.puzzle_dir, settings=self.resolve_default_settings())
            manager = CgPuzzleManager(resolved_dir, cast(CgClient, None))
            values = {name: reader(manager) for name, reader in PUZZLE_SET_FIELDS.items()}
            if self.args.json:
                print(json.dumps(values, indent=2, sort_keys=True))
                return
            for name, current in values.items():
                print(f"{name:<22}{_format_field_value(current)}")
        return handler

    @cli_command("Show or switch the language this puzzle is solved in, restoring your own most "
                 "recent code for it. With no LANGUAGE, prints the current one. CodinGame keeps "
                 "your latest source per language, so anything you previously wrote in the target "
                 "language comes back; a language you have never used gets a placeholder. Refuses "
                 "if your solution holds work the server does not have--submit it first, or pass "
                 "--force to discard it. Changes local state only; the server's language follows "
                 "once you run a server-side test or submit in the new one.")
    async def cmd_puzzle__set__solution_language(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            language: str | None = self.args.value
            resolved_dir = resolve_puzzle_dir(
                    self.args.puzzle_dir, settings=self.resolve_default_settings())
            if language is None:
                manager = CgPuzzleManager(resolved_dir, cast(CgClient, None))
                data = manager.load_puzzle_data()
                print(_format_field_value(data.solution_language if data is not None else None))
                return
            client = await self.get_client()
            manager = CgPuzzleManager(
                    resolved_dir, client,
                    toolchain_dir=self.resolve_toolchain_dir(),
                    toolchain_languages=self.resolve_toolchain_languages(),
                    toolchain_image=self.resolve_toolchain_image())
            result = await manager.set_language(language, force=self.args.force)
            self.eprint(f"{resolved_dir}: {result.previous_language!r} -> {result.language!r}")
            if result.from_server:
                self.eprint(f"  restored your saved {result.language} solution "
                            f"({len(result.code.splitlines())} lines).")
            else:
                self.eprint(f"  no saved {result.language} solution on the server--wrote a "
                            "placeholder to start from.")
        p = cmd.get_parser()
        p.add_argument("value", type=str, nargs="?", default=None, metavar="LANGUAGE",
                       help="CodinGame language ID to switch to, e.g. 'C++', 'Python3'. Omit to "
                            "print the current language.")
        p.add_argument("--force", "-f", default=False, action="store_true",
                       help="Switch even if your solution has changes the server doesn't have, "
                            "discarding them.")
        return handler

    @cli_command("Make DIRECTORY the active puzzle, so later `cg puzzle` commands use it without "
                 "--puzzle-dir. `cg puzzle import` sets this for you, so use this to switch "
                 "between working directories you already have. Outranks the configured default "
                 "(`cg settings set puzzle-dir`); `cg puzzle deactivate` clears it.")
    async def cmd_puzzle__activate(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            directory = Path(self.args.directory).expanduser().resolve()
            if not (directory / PUZZLE_IDENTITY_FILE_NAME).is_file():
                raise CliError(
                        f"{directory} is not a puzzle working directory (no {PUZZLE_IDENTITY_FILE_NAME}). "
                        "Use `cg puzzle import DIRECTORY PUZZLE` to create one.")
            await self.set_current_working_dir("puzzle", directory)
            self.eprint(f"Active puzzle directory set to {directory}")
        p = cmd.get_parser()
        p.add_argument("directory", type=Path, nargs="?", default=Path.cwd(), metavar="DIRECTORY",
                       help="The puzzle working directory to activate. Defaults to the current "
                            "directory, so `cd` into one and run this with no arguments.")
        return handler

    @cli_command("Clear the active puzzle, so `cg puzzle` commands fall back to the configured "
                 "default and the usual directory discovery. Touches no files -- only the "
                 "selection.")
    async def cmd_puzzle__deactivate(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            previous = await self.clear_current_working_dir("puzzle")
            if previous is None:
                self.eprint("No active puzzle directory was set; nothing to do.")
            else:
                self.eprint(f"Active puzzle directory cleared (was {previous})")
        return handler

    @cli_command("Choose which test case the debugger runs against. Debugging feeds one stdin, so "
                 "it needs exactly one test. The choice is recorded per working directory and "
                 "survives until you change it; without one, debugging uses the first test case. "
                 "With no INDEX, shows the current selection.")
    async def cmd_puzzle__select_test(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            puzzle_dir: Path | None = self.args.puzzle_dir
            index: int | None = self.args.test_index
            resolved_dir = resolve_puzzle_dir(puzzle_dir, settings=self.resolve_default_settings())
            manager = CgPuzzleManager(resolved_dir, cast(CgClient, None))
            if self.args.clear:
                manager.clear_selected_test()
                self.eprint(f"Selection cleared; debugging will use test {manager.resolve_debug_test_index()}.")
                return
            if index is None:
                selected = manager.load_selected_test()
                chosen = manager.resolve_debug_test_index()
                self.eprint(f"Debugging will use test {chosen}"
                            f"{'' if selected else ' (default--no explicit selection)'}.")
                return
            manager.select_test(index)
            self.eprint(f"Selected test {index}.")
        p = cmd.get_parser()
        p.add_argument("test_index", type=int, nargs="?", default=None, metavar="INDEX",
                       help="1-based test case index, as shown by `cg puzzle play`. Omit to show "
                            "the current selection.")
        p.add_argument("--clear", default=False, action="store_true",
                       help="Forget the explicit selection and fall back to the first test case.")
        return handler

    @cli_command("Print the path of the puzzle working directory that would be used. Prints "
                 "nothing but the path, so it composes: $EDITOR \"$(cg puzzle "
                 "where)/data/solution.py\". Exits non-zero if no working directory can be found.")
    async def cmd_puzzle__where(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            puzzle_dir: Path | None = self.args.puzzle_dir
            found = find_puzzle_dir(puzzle_dir, settings=self.resolve_default_settings())
            if found is None:
                raise CliError(
                        "No puzzle working directory found. Run "
                        "`cg puzzle import DIRECTORY PUZZLE` to create one.")
            # stdout carries the resolved path and nothing else, so this composes:
            #     $EDITOR "$(cg puzzle where)/data/solution.src"
            # Anything explanatory goes to stderr, and "not found" is a non-zero exit rather than a
            # friendly line of prose a shell would happily substitute into a path.
            print(found)
        return handler

    @cli_command("Delete this puzzle working directory. Local only -- the puzzle exists on the "
                 "server independently of you and is not yours to remove. Destructive: prompts for "
                 "confirmation unless --force is given, and requires --force outright if "
                 "stdin/stdout are not a terminal.")
    async def cmd_puzzle__delete(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            puzzle_dir: Path | None = self.args.puzzle_dir
            force: bool = self.args.force
            resolved_dir = resolve_puzzle_dir(puzzle_dir, settings=self.resolve_default_settings())
            manager = CgPuzzleManager(resolved_dir, cast(CgClient, None))
            identity = manager.load_identity()
            if identity is None:
                raise CliError(f"{resolved_dir} has never been imported--nothing to delete.")
            server_data = manager.load_server_data()
            title = server_data.title if server_data is not None else None
            if not force:
                if not (sys.stdin.isatty() and sys.stdout.isatty()):
                    raise CliError(
                            "Refusing to delete without confirmation: stdin/stdout aren't a "
                            "terminal. Pass --force to proceed non-interactively."
                        )
                print("About to PERMANENTLY DELETE this local puzzle working directory (the "
                      "server-side puzzle itself is untouched--this is local-only):")
                print(f"  directory: {resolved_dir}")
                print(f"  puzzle id: {identity.puzzle_id}" + (f" (title {title!r})" if title else ""))
                reply = input("Type DELETE (all caps) to confirm, or anything else to cancel: ")
                if reply != "DELETE":
                    raise CliError("Confirmation did not match--aborted, nothing was deleted.")
            await manager.delete()
            self.eprint(f"{resolved_dir}: local puzzle working directory removed.")
            # See `cg contribution delete` for why this is scoped to the deleted directory.
            if await self.clear_current_working_dir("puzzle", only_if=resolved_dir) is not None:
                self.eprint("  (was the active puzzle; deactivated)")
        p = cmd.get_parser()
        p.add_argument("--force", "-f", default=False, action="store_true",
                       help="Skip the interactive confirmation prompt. Required if stdin/stdout "
                            "aren't a terminal.")
        return handler

    @cli_command("Configuration commands.")
    async def cmd_config(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Create a new config.yaml--project-local (under the current directory, or "
                 "--at DIR) by default, or the shared per-user fallback location with --global. "
                 "Does not consult the top-level --config/-c flag or CG_CONFIG--that's a "
                 "discovery override for reading an existing config, not a placement option for "
                 "creating a new one.")
    async def cmd_config__init(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            use_global: bool = self.args.global_
            force: bool = self.args.force
            at: Path = self.args.at
            if use_global:
                target = default_global_config_file()
                existing: Path | None = None
            else:
                target = at / PROJECT_CONFIG_MARKER_DIR_NAME / CONFIG_SUBDIR_NAME / CONFIG_FILE_NAME
                existing = find_config_file(start_dir=at)
                if existing is not None and existing not in (target, default_global_config_file()):
                    self.eprint(f"Note: this will shadow the existing configuration found at {existing}")
            if target.is_file() and not force:
                raise CliError(f"Config file already exists: {target}. Use --force to overwrite.")
            target.parent.mkdir(parents=True, exist_ok=True)
            # --global: show the actual resolved absolute data dir (no sibling relationship to
            # express relatively). Project-local: always the literal "../data"--not an absolute
            # path resolved for this specific --at location--so the config keeps working with its
            # default data directory even if the project is later renamed/moved elsewhere.
            if use_global:
                default_data_dir_example = str(
                        CgConfig(config_file=target.resolve(), raw_data=CgConfigData()).data_dir)
            else:
                default_data_dir_example = f"../{DATA_SUBDIR_NAME}"
            target.write_text(default_config_template(default_data_dir_example))
            raw_data = CgConfigData.load_yaml(target)
            resolved = CgConfig(config_file=target.resolve(), raw_data=raw_data)
            resolved.data_dir.mkdir(parents=True, exist_ok=True)
            self.eprint(f"Created config file: {resolved.config_file}")
            self.eprint(f"Data directory: {resolved.data_dir}")
        p = cmd.get_parser()
        p.add_argument("--global", dest="global_", default=False, action="store_true",
                       help="Create the shared, per-user fallback config instead of a project-local one.")
        p.add_argument("--at", type=Path, default=Path.cwd(), metavar="DIR",
                       help="Project-local only: directory to create .cg/config/config.yaml under. "
                            "Defaults to the current directory.")
        p.add_argument("--force", "-f", default=False, action="store_true",
                       help="Overwrite an existing config file at the target location.")
        return handler

    @cli_command("Show which config.yaml (if any) would be used, and where its persistent data "
                 "directory resolves to.")
    async def cmd_config__where(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            explicit: str | None = self.args.config
            try:
                config_file = find_config_file(explicit)
            except FileNotFoundError as e:
                raise CliError(str(e)) from e
            if config_file is None:
                print("No configuration file found. Run `cg config init` to create one.")
                return
            raw_data = CgConfigData.load_yaml(config_file)
            resolved = CgConfig(config_file=config_file.resolve(), raw_data=raw_data)
            print(f"Config file: {resolved.config_file}")
            print(f"Data directory: {resolved.data_dir}")
        return handler

    @cli_command("Dump the resolved configuration as JSON.")
    async def cmd_config__dump(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            resolved = await self.get_config()
            print(json.dumps(resolved.to_dump_dict(), indent=2, sort_keys=True))
        return handler

    @cli_command("Settings commands (app-managed persistent state in settings.json, as opposed "
                 "to the user-edited config.yaml--see `cg config`).")
    async def cmd_settings(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Dump the resolved settings as JSON.")
    async def cmd_settings__dump(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            settings = await self.get_settings()
            print(json.dumps(settings.to_dump_dict(), indent=2, sort_keys=True))
        return handler

    @cli_command("Set a settings.json value.")
    async def cmd_settings__set(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Set the default codingame-tools credential profile name.")
    async def cmd_settings__set__default_profile(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            profile_name: str = self.args.profile_name
            validate_profile_name(profile_name)
            settings = await self.get_settings()
            settings.raw_data.default_profile = profile_name
            settings.save()
            self.eprint(f"defaultProfile set to {profile_name!r} in {settings.settings_file}")
        p = cmd.get_parser()
        p.add_argument("profile_name", type=str, metavar="PROFILE-NAME",
                       help="The credential profile name to record as the default--used "
                            "whenever --profile isn't given (see `cg login`, `cg api ...`, etc.), "
                            "and shown resolved by `cg config dump`/`cg settings dump`.")
        return handler

    @cli_command("Set the default contribution working directory.")
    async def cmd_settings__set__contribution_dir(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            contribution_dir: Path = self.args.contribution_dir
            settings = await self.get_settings()
            value = relativize_settings_dir(contribution_dir, settings.settings_file.parent)
            settings.raw_data.contribution_dir = value
            settings.save()
            self.eprint(f"contributionDir set to {value!r} in {settings.settings_file}")
        p = cmd.get_parser()
        p.add_argument("contribution_dir", type=Path, metavar="DIR",
                       help="Directory to use as the default contribution working directory--used "
                            "whenever --contribution-dir isn't given and CG_CONTRIBUTION_DIR isn't "
                            "set (see `cg contribution import`/`cg contribution push`). If given as "
                            "a relative path, it's resolved against the current directory right now "
                            "and stored relative to settings.json's own directory--so the effective "
                            "directory doesn't move around depending on where `cg` is later run from.")
        return handler

    @cli_command("Set the default puzzle working directory.")
    async def cmd_settings__set__puzzle_dir(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            puzzle_dir: Path = self.args.puzzle_dir
            settings = await self.get_settings()
            value = relativize_settings_dir(puzzle_dir, settings.settings_file.parent)
            settings.raw_data.puzzle_dir = value
            settings.save()
            self.eprint(f"puzzleDir set to {value!r} in {settings.settings_file}")
        p = cmd.get_parser()
        p.add_argument("puzzle_dir", type=Path, metavar="DIR",
                       help="Directory to use as the default puzzle working directory--used "
                            "whenever --puzzle-dir isn't given and CG_PUZZLE_DIR isn't set (see "
                            "`cg puzzle import`/`cg puzzle submit`). Same relative-path handling as "
                            "`cg settings set contribution-dir`--see its help for details.")
        return handler

    @cli_command("Delete a settings.json value.")
    async def cmd_settings__delete(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # No handler for the parent command; subcommands will be handled by their own handlers.

    @cli_command("Delete (unset) the default codingame-tools credential profile name override.")
    async def cmd_settings__delete__default_profile(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            settings = await self.get_settings()
            settings.raw_data.default_profile = None
            settings.save()
            self.eprint(
                    f"defaultProfile unset in {settings.settings_file} "
                    f"(now falls back to config.yaml's settings.defaultProfile, or \"default\")."
                )
        return handler

    @cli_command("Delete (unset) the default contribution working directory override.")
    async def cmd_settings__delete__contribution_dir(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            settings = await self.get_settings()
            settings.raw_data.contribution_dir = None
            settings.save()
            self.eprint(
                    f"contributionDir unset in {settings.settings_file} "
                    f"(now falls back to config.yaml's settings.contributionDir, if any)."
                )
        return handler

    @cli_command("Delete (unset) the default puzzle working directory override.")
    async def cmd_settings__delete__puzzle_dir(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            settings = await self.get_settings()
            settings.raw_data.puzzle_dir = None
            settings.save()
            self.eprint(
                    f"puzzleDir unset in {settings.settings_file} "
                    f"(now falls back to config.yaml's settings.puzzleDir, if any)."
                )
        return handler

    @cli_command("Codingame client command-line interface.")
    async def main(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        """Main command handler for the CLI."""

        p = cmd.get_parser()
        p.add_argument(
                "--trace-http", dest="trace_http", default=False, action="store_true",
                help="Log detailed HTTP info (method, URL, headers, cookies) at DEBUG level.",
            )
        p.add_argument(
                "--profile", "-p", default=None,
                help="Profile name to store credentials and browser session state under. Defaults to the client's default profile.",
            )
        p.add_argument(
                "--json", "-j", default=False, action="store_true",
                help="Where supported, output information in JSON format.",
            )
        p.add_argument(
                "--config", "-c", default=None, metavar="PATH",
                help="Explicit config.yaml file, or a directory containing config/config.yaml. "
                     "Overrides the normal discovery search (see `cg config where`). Same as the "
                     "CG_CONFIG environment variable; this flag takes precedence if both are set.",
            )

        # No handler for the main command; bare command is not allowed
        return None
    
    @override
    async def preinit(self) -> None:
        """Perform any pre-initialization setup before the parser is built."""
        self.get_console()

async def async_main(args: list[str] | None = None, prog_name: str | None = None) -> int:
    """Main entry point for the CLI."""
    return await CgCli(args, prog_name).async_run()

def main(args: list[str] | None = None, prog_name: str | None = None) -> int:
    """Main entry point for the CLI."""
    return CgCli(args, prog_name).run()
