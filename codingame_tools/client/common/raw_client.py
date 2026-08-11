"""
Low-level (JsonData) client for the CodinGame API, plus shared HTTP/servlet primitives
(exceptions, file-transfer result types, default headers).
"""

from __future__ import annotations

import contextlib
import hashlib
import http.cookies
import json
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from http import HTTPStatus
from pathlib import Path
from types import TracebackType
from typing import Final, NamedTuple, cast
from urllib.parse import urlencode

import aiohttp
from json_data_types import JsonData, JsonDict, JsonList

from ...common.dataclass_wizard_x import CatchAll, JSONWizardX
from ...common.typedefs import Never, Self
from ...config import resolve_config
from ...credentials.cg_credentials import CgCredentials, get_credentials_with_override
from ...settings import CgSettings, resolve_settings
from ...version import __version__

__all__ = [
    "compute_content_hash",
    "CgDownloadFileResult",
    "CgUploadFileResult",
    "CgServletError",
    "CgFileUploadError",
    "DEFAULT_HEADERS",
    "MISSING",
    "CgAuthenticationError",
    "CgClientErrorResponse",
    "CgClientHttpError",
    "CgServletGetBytesResult",
    "CgRawClient",
]

logger = logging.getLogger(__name__)

def compute_content_hash(content: bytes) -> str:
    """Compute the SHA256 hash of the given content and return it as a hex string."""
    sha256 = hashlib.sha256()
    sha256.update(content)
    return sha256.hexdigest()


class CgDownloadFileResult(NamedTuple):
    """The result of a successful file download"""

    id: int
    """The globally unique ID of the file, as provided by the server at upload time."""

    content: bytes
    """The content of the downloaded file."""

    content_type: str
    """The content type of the downloaded file, as provided by the server. Normalized to lowercase."""

    hash: str
    """The SHA256 hash of the downloaded file content, as a hex string.
       This can be used to verify the integrity of the downloaded file or to detect changes in local copies.
    """

    filename: str | None = None
    """The filename of the downloaded file, if provided by the server in the
       Content-Disposition header. Does not include a path. This is typically the
       original filename of the uploaded file."""

    @classmethod
    def create(
                cls,
                id: int,
                content: bytes,
                content_type: str,
                filename: str | Path | None = None,
                hash: str | None = None
            ) -> Self:
        """Create a CgDownloadFileResult instance with the given content, content type, and optional filename."""
        file_tail_name = Path(filename).name if filename is not None else None
        if hash is None:
            hash = compute_content_hash(content)
        return cls(
            id=id,
            content=content,
            content_type=content_type,
            hash=hash,
            filename=file_tail_name,
        )


class CgUploadFileResult(NamedTuple):
    """The well-typed result of a successful file upload--parsed from the raw `fileupload`
       servlet response, e.g. `{"result": [{"fieldName": "file", "name": "cover.png",
       "size": 250401, "id": 163935944975958}]}`."""

    id: int
    """The globally unique ID assigned to the uploaded file. Used to download the file later
       (see `CgDownloadFileResult.id`) or to reference it from other APIs that accept file IDs."""

    name: str
    """The filename as echoed back by the server; normally matches the `filename` provided at
       upload time."""

    size: int
    """The size of the uploaded file content, in bytes."""

    field_name: str
    """The multipart form field name the file was uploaded under. Always "file" in current usage."""

    @classmethod
    def from_dict(cls, d: JsonDict) -> Self:
        """Parse a `CgUploadFileResult` from a successful entry of a raw `fileupload` servlet
           response's `"result"` list. Assumes `d` is already known to be a successful entry
           (not an embedded per-file error--see `CgFileUploadError`); callers must check for that
           themselves before calling this."""
        return cls(
            id=cast(int, d["id"]),
            name=cast(str, d["name"]),
            size=cast(int, d["size"]),
            field_name=cast(str, d["fieldName"]),
        )


class CgServletError(Exception):
    """Base class for an embedded per-entry error returned by a servlet in an otherwise-successful
       (200 OK) response--i.e. an application-level error signaled inside the JSON body rather
       than via HTTP status, so it can't be caught as a `CgClientHttpError`.

       This is *not* a claim that all servlets share one common error response shape--currently
       only `fileupload` is known to work this way (see `CgFileUploadError`)--just the common
       subset of fields (`error_type`, `error_message`, `field_name`) that make sense to factor
       out if/when a second servlet turns out to follow the same pattern.
    """

    error_type: str
    """The server's error type code, e.g. "UNSUPPORT_FILE_ERROR"."""

    error_message: str
    """The server's human-readable error message, e.g. "Unsupported file: Format not supported"."""

    field_name: str
    """The form field name the error applies to, if applicable. Defaults to "" when not
       applicable or not provided by the server."""

    def __init__(self, error_type: str, error_message: str, *, field_name: str = "") -> None:
        self.error_type = error_type
        self.error_message = error_message
        self.field_name = field_name
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        """Build the exception's string message. Subclasses adding fields relevant to the error
           should override this to include them, rather than overriding `__init__` message
           construction directly."""
        return f"{self.error_type}: {self.error_message}"


class CgFileUploadError(CgServletError):
    """Raised when the `fileupload` servlet accepts the HTTP request itself (a 200 OK) but
       rejects the uploaded file's content--e.g. an unsupported format--returning an embedded
       error object in its response instead of a successful upload entry. Confirmed live (2026-07-27):
       uploading a plain-text file returns
       `{"result": [{"error": {"type": "UNSUPPORT_FILE_ERROR", "message": "Unsupported file: "
       "Format not supported"}, "fieldName": "file", "name": "...", "size": ...}]}`."""

    name: str
    """The filename that was rejected, as echoed back by the server."""

    size: int
    """The size of the rejected file content, in bytes."""

    def __init__(
                self,
                error_type: str,
                error_message: str,
                *,
                field_name: str = "",
                name: str,
                size: int,
            ) -> None:
        self.name = name
        self.size = size
        super().__init__(error_type, error_message, field_name=field_name)

    def _format_message(self) -> str:
        return f"{super()._format_message()} (file={self.name!r}, size={self.size})"


DEFAULT_HEADERS: dict[str, str] = {
        "User-Agent": (
            f"codingame-tools/{__version__} (+https://github.com/mckelvie-org/codingame-tools)"
        ),
        "Accept": "application/json, text/plain, */*",
    }

class _Missing(Enum):
    """Sentinel value for missing parameters."""
    TOKEN = object()

MISSING = _Missing.TOKEN
"""Sentinel value for missing parameters."""

class CgAuthenticationError(Exception):
    """Raised when the client is not authenticated and an operation requires authentication."""

    def __init__(self, message: str | None = None):
        super().__init__(message or "Codingame client is not authenticated. Please login first.")

@dataclass
class CgClientErrorResponse(JSONWizardX):
    """Represents a well-formed JSON error response from the CodinGame API."""

    code: str
    """The error code string returned by the API; e.g., 'BODY_MUST_BE_JSON_ARRAY'.
       This property is always present in a well-formed error response, and must not be present
       in any non-error response."""

    # `extra_data` is deliberately the first field with a default: dataclass_wizard 1.0.0 mis-binds
    # any defaulted field positioned immediately before it (silently, no error) to the CatchAll's
    # own value. Keeping it first among the defaulted fields makes that impossible.
    extra_data: CatchAll = field(default_factory=dict)
    """Unrecognized fields encountered when loading the error response, preserved."""

    message: str | None = None
    """The error message returned by the API."""


_MAX_ERROR_DETAIL_CHARS = 600
"""How much of an unrecognized error body to put in the exception message. Enough for CodinGame's
   validation errors, which name the offending field, without pasting a whole HTML error page into a
   traceback."""


def _describe_error_content(content: JsonData | bytes | None) -> str:
    """A one-line rendering of a response body for an error message, or "" if there is nothing
       useful to say.

       Exists because the body is the only thing that distinguishes one 422 from another. CodinGame
       returns a structured `{"code": ...}` object for many failures, and `CgClientErrorResponse`
       handles those--but not all of them, and a bare "422 Unprocessable Entity" with the
       explanation sitting unused in `content` is close to useless to whoever has to act on it."""
    if content is None:
        return ""
    if isinstance(content, (bytes, bytearray, memoryview)):
        text = bytes(content).decode("utf-8", errors="replace")
    elif isinstance(content, str):
        text = content
    else:
        with contextlib.suppress(Exception):
            text = json.dumps(content, separators=(",", ":"))
        if not isinstance(text, str):  # pragma: no cover--json.dumps only fails on exotic input
            text = repr(content)
    text = " ".join(text.split())
    if not text:
        return ""
    if len(text) > _MAX_ERROR_DETAIL_CHARS:
        text = text[:_MAX_ERROR_DETAIL_CHARS] + f"... ({len(text)} chars total)"
    return text


class CgClientHttpError(Exception):
    """Raised for HTTP-level failures making a request to the CodinGame API. Contains the status
       code and content of the response, if available, and--since the client is built on
       aiohttp--the underlying `aiohttp.ClientResponse` too, for debugging purposes."""
    status_code: int
    """The HTTP status code of the response; e.g., 400."""

    raw_message: str
    """The unadorned error message provided at construction time. If none was
       provided, this will be the default phrase for the HTTP status code; e.g., "Bad Request".
    """

    content: JsonData | bytes | None
    """The decoded content of the response, if available. If the response was valid JSON, this will be the
       decoded JsonData value (which may be a dict, list, str, int, float, bool, or None). If the response
       could not be decoded as JSON or text, this may be raw bytes. If None, the content was not available.
    """

    api_error_response: CgClientErrorResponse | None = None
    """If the response content was a well-formed JSON error response, this will be a CgClientErrorResponse instance."""

    response: aiohttp.ClientResponse | None
    """The underlying aiohttp response, if one was involved (some errors are raised before any
       response exists)."""

    def __init__(
                self,
                message: str | None = None,
                *,
                response: aiohttp.ClientResponse | None = None,
                content: JsonData | bytes | None | _Missing = MISSING,
                status_code: int | None = None,
            ):
        """Create a CgClientHttpError, providing available context.

        Args:
            message:      Optional error message. If not provided, will use the status code's default phrase
            response:     Optional aiohttp.ClientResponse object. If provided, will be used to determine the status
                          code and content if not provided.
            content:      Optional decoded content of the response. If not provided, will attempt to use already
                          cached content bytes read from the response, if provided. If not provided and no cached content is
                          available, will be None.
            status_code:  Optional status code. If not provided, will attempt to read from the response if provided, or 200 otherwise.
        """
        if status_code is None:
            status_code = response.status if response is not None else 200
        if content is MISSING:
            content = None
            if response is not None:
                with contextlib.suppress(Exception):
                    # This is invasive, but we can't await in a constructor, so we try to read the cached content if available.
                    # it's only used for debugging/descriptive purposes anyway.
                    content = response._body
        if isinstance(content, dict) and "code" in content:
            with contextlib.suppress(Exception):
                self.api_error_response = CgClientErrorResponse.from_dict(content)
        self.status_code = status_code
        self.raw_message = message or HTTPStatus(status_code).phrase
        self.content = content
        self.response = response
        if self.api_error_response is None:
            message = f"CodinGame HTTP Error: {status_code} {self.raw_message}"
            # The body is the only thing that tells one 422 from another, so say it rather than
            # leaving it in `content` for someone to find with a debugger.
            detail = _describe_error_content(content)
            if detail:
                message = f"{message}: {detail}"
        else:
            if self.api_error_response.message is not None:
                message = (
                        f"CodinGame API Error: {status_code} {self.raw_message}: "
                        f"{self.api_error_response.code}: {self.api_error_response.message}"
                    )
            else:
                message = f"CodinGame API Error: {status_code} {self.raw_message}: {self.api_error_response.code}"
        super().__init__(message)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.status_code}, {self.raw_message!r})"

    @classmethod
    def normalize(
                cls,
                e: aiohttp.ClientResponseError,
                *,
                content: JsonData | bytes | None | _Missing = MISSING,
                response: aiohttp.ClientResponse | None=None,
            ) -> Self:
        """Normalize an exception raised by aiohttp into a CgClientHttpError, preserving the status code and message.
        Args:
            e:            The original aiohttp.ClientResponseError exception.
            content:      Optional decoded content of the response. If not provided, will attempt to use already
                          cached content bytes read from the response, if provided. If not provided and no cached content is
                          available, will be None.
            response:     Optional aiohttp.ClientResponse object. If provided, will be used to determine the status code
                          and content if not provided.
        """
        return cls(e.message, response=response, content=content, status_code=e.status)


class CgServletGetBytesResult(NamedTuple):
    """The result of `CgRawClient.servlet_get_bytes`: a servlet GET response's raw content
       bytes, paired with the `aiohttp.ClientResponse` (for its headers, e.g. Content-Type/
       Content-Disposition). Only `content` and `response.headers`/`.status` remain usable--
       aiohttp releases the underlying connection once the request's `async with` block exits, so
       `response.read()`/`.text()` must not be called again."""

    content: bytes
    response: aiohttp.ClientResponse


class CgRawClient:
    """Low-level (JsonData) client for the CodinGame API, built on aiohttp."""

    CODINGAME_BASE_URL: Final[str] = "https://www.codingame.com"
    """Base URL for the CodinGame website. Used for most API requests, except the "static" endpoint."""

    CODINGAME_SERVLET_URL: Final[str]  = CODINGAME_BASE_URL + "/servlet"
    """Base URL for the CodinGame servlet endpoint. Used for file uploads and downloads."""

    CODINGAME_SERVICES_URL: Final[str] = CODINGAME_BASE_URL + "/services/"
    """Base URL for the CodinGame "services" requests. Used for most API requests."""

    CODINGAME_STATIC_BASE_URL: Final[str] = "https://static.codingame.com"
    """Base URL for the CodinGame static content endpoint. Used for file downloads."""

    CODINGAME_STATIC_SERVLET_URL: Final[str] = CODINGAME_STATIC_BASE_URL + "/servlet"
    """Base URL for the CodinGame static servlet endpoint. Used for file downloads."""

    profile_name: str | None = None
    """The name of the profile to use for persistent credentials. Allows for multiple independent session profiles;
       e.g., if multiple CodinGame accounts are used. If None, defaults to the default profile. May
       be provided at construction or at authenticate() time."""

    credentials: CgCredentials | None = None
    """If the client is logged in, this will hold the credentials used for authentication."""

    saved_credentials: CgCredentials | None = None
    """Known contents of the saved credentials, if any. This is used to determine whether
       the credentials have changed and need to be saved."""

    login_attempted: bool = False
    """Whether a login attempt has been made. If True, further implicit login attempts will not be made."""

    app_name: str | None = None
    """The name of the application using the client. Used to allow different applications to have different
       cached credentials in the same environment. If None, a default application name is used."""

    default_http_headers: dict[str, str]
    """The HTTP headers used for requests."""

    codingamer_id: int | None = None
    """The codingamer ID of the currently logged-in user, if available. This is derived from the first part of the rememberMe cookie."""

    session: aiohttp.ClientSession
    """The aiohttp session used for requests."""

    _trace_configs: list[aiohttp.TraceConfig]

    def __init__(
                self,
                *,
                profile_name: str | None = None,
                default_http_headers: dict[str, str] | None = None,
                trace_configs: list[aiohttp.TraceConfig] | None = None,
                app_name: str | None = None,
                settings: CgSettings | None = None,
            ):
        """Create a CgRawClient.

        Args:
            profile_name: Optional name of the profile to use for persistent credentials. Allows
                          for multiple independent session profiles; e.g., if multiple CodinGame accounts are used.
                          If None, the default profile name is resolved from `settings` (see below).
                          This parameter may be overridden at authenticate() time.
            default_http_headers:
                          Optional default HTTP headers for requests. If None, default headers are used.
            trace_configs: Optional list of aiohttp.TraceConfig for the session. If None, an empty list is used.
            app_name: Optional name of the application using the client. Used to allow different applications to have different
                      cached credentials in the same environment. If None, a default application name is used.
            settings: Optional CgSettings to resolve the default profile name from, used only when
                      `profile_name` is None. If not given (and `profile_name` is also not given),
                      the normal config/settings discovery path is used, best-effort--matching how
                      credential resolution elsewhere in this class never requires setup to exist
                      first: if no config.yaml can be found, a synthetic all-defaults CgConfig is
                      used instead of raising (see `resolve_config(allow_default=True)`), so this
                      never requires `cg config init` to have been run. The `CgConfig` is not a
                      separate parameter since it's already reachable as `settings.config`.
        """
        if profile_name is None:
            if settings is None:
                settings = resolve_settings(resolve_config(allow_default=True))
            profile_name = settings.default_profile
        self.profile_name = profile_name
        self.app_name = app_name
        self.default_http_headers = default_http_headers or DEFAULT_HEADERS
        if trace_configs is None:
            trace_configs = []
        self._trace_configs = list(trace_configs)
        self.session = aiohttp.ClientSession(
            headers=self.default_http_headers,
            trace_configs=self._trace_configs,
        )

    def __enter__(self) -> Never:
        raise NotImplementedError("__enter__ not supported for CgRawClient--use __aenter__ instead.")

    def __exit__(
                self,
                exc_type: type[BaseException] | None,
                exc_val: BaseException | None,
                exc_tb: TracebackType | None
            ) -> Never:
        raise NotImplementedError("__exit__ not supported for CgRawClient--use __aexit__ instead.")

    async def __aenter__(self) -> CgRawClient:
        await self.session.__aenter__()
        return self

    async def __aexit__(
                self,
                exc_type: type[BaseException] | None,
                exc_val: BaseException | None,
                exc_tb: TracebackType | None
            ) -> bool | None:
        # aiohttp.ClientSession.__aexit__ always returns None (never suppresses the exception).
        await self.session.__aexit__(exc_type, exc_val, exc_tb)
        return None

    async def close(self) -> None:
        """Close the client session."""
        await self.session.close()

    def set_cookie(
                self,
                name: str,
                value: str | None = None,
                *,
                domain: str = "www.codingame.com",
            ) -> None:
        """Set a cookie for the client session.

           The cookie will be sent with all requests to the specified domain for the remainder
           of the client session.

           If value is None, the cookie will be deleted.
        """
        if value is None:
            self.session.cookie_jar.clear(predicate=lambda morsel: morsel.key == name and morsel["domain"] == domain)
        else:
            cookie = http.cookies.SimpleCookie()
            cookie[name] = value
            morsel: http.cookies.Morsel[str] = cookie[name]
            morsel["domain"] = domain
            morsel["path"] = "/"
            self.session.cookie_jar.update_cookies(cookie)

    def set_credentials(
                self,
                credentials: CgCredentials | None,
            ) -> CgCredentials:
        """Set the credentials for the client session.

           If credentials are provided, they will be used to authenticate/reauthenticate the client session.
           If not provided, empty credentials are used, effectively logging out the client session.

           The client is only considered logged in if both a rememberMe and a cgSession cookie are present;
           credentials with only one (or neither) are treated the same as no credentials at all. This is
           because enough CodinGame endpoints require cgSession specifically (not just rememberMe) that a
           partial session isn't useful in practice.

           The rememberMe and cgSession cookies are updated to match the credentials.

           Persistent credentials are not affected.

           Returns:
                The (deep-copied) credentials that are now cached. If there are no credentials, returns an empty CgCredentials() object.
        """
        if credentials is not None and (credentials.remember_me_cookie is None or credentials.cg_session_cookie is None):
            credentials = None
        if credentials is None:
            credentials = CgCredentials()
            # Both cookies are required for the client to be considered logged in--enough CodinGame
            # endpoints require cgSession specifically that a rememberMe-only session isn't useful.
            self.credentials = None
            self.login_attempted = False
            self.codingamer_id = None
            self.set_cookie("rememberMe", None)
            self.set_cookie("cgSession", None)
        else:
            # The codingamer ID is derived from the first 7 characters of the rememberMe cookie
            remember_me_cookie = credentials.remember_me_cookie
            cg_session_cookie = credentials.cg_session_cookie
            assert remember_me_cookie is not None and cg_session_cookie is not None
            try:
                self.codingamer_id = int(remember_me_cookie[:7])
            except (ValueError, TypeError) as e:
                raise ValueError("Invalid rememberMe cookie format; cannot derive codingamer ID.") from e
            self.credentials = credentials
            self.set_cookie("rememberMe", remember_me_cookie)
            self.set_cookie("cgSession", cg_session_cookie)
            self.login_attempted = True

        return credentials

    def clear_credentials(self) -> None:
        """Clear the credentials for the client session, effectively logging out the client session.

           Persistent credentials are not affected.
        """
        self.set_credentials(None)

    def resolve_credentials(
                self,
                *,
                profile_name: str | _Missing | None = MISSING,
                remember_me_token: str | None = None,
                cg_session_token: str | None = None,
                credentials: CgCredentials | None = None,
                force: bool = False,
            ) -> CgCredentials:
        """Resolve the current credentials for the client, with parameter and environment variable overrides.

        Resolution order:
            1. If force is False and credentials are already cached in the client, use those values.
            2. If non-null `remember_me_token` / `cg_session_token` are provided, use those values.
            3. If `credentials` is provided, use non-null token values from that object.
            4. check the `REMEMBER_ME_TOKEN_ENV_VAR` / `CG_SESSION_TOKEN_ENV_VAR` environment variables for overrides.
            5. If neither is provided and force is False, check the in-process cache for the app's credentials.
            6. If not in the cache, check the per-app private credentials file (which populates the cache on success).
            7. If none of the above are available, return an empty `CgCredentials()`

        The result of this function is not cached in the client; it is up to the caller to call `set_credentials()`
        if they want to cache the result.

        Args:
            profile_name: Optional name of the profile to use for persistent credentials. Allows
                          for multiple independent session profiles; e.g., if multiple CodinGame accounts are used.
                          If not provided or MISSING, defaults to the profile_name provided at client construction time.
                          If None, defaults to the default profile.
            remember_me_token: Optional override for the `rememberMe` cookie value.
            cg_session_token: Optional override for the `cgSession` cookie value.
            credentials: Optional `CgCredentials` object to use as the base for resolution.
            force: If True, ignore the in-process cache and reload from the credentials file.

        Returns:
            Resolved `CgCredentials` object, with parameter and environment variable overrides applied.
            If there are no valid credentials, returns an empty `CgCredentials()` object.
        """
        if not force and self.credentials is not None:
            credentials = deepcopy(self.credentials)
        else:
            if profile_name is MISSING:
                profile_name = self.profile_name
            credentials = get_credentials_with_override(
                profile_name=profile_name,
                credentials=credentials,
                remember_me_token=remember_me_token,
                cg_session_token=cg_session_token,
            )
        return credentials

    def is_logged_in(self) -> bool:
        """Return True if the client is logged in (i.e., has valid credentials), False otherwise."""
        return self.credentials is not None

    async def validate_credentials(self) -> None:
        """Verifies that current client credentials are valid by making a test request to the CodinGame API.
           Raises CgAuthenticationError if the credentials are invalid or if the request fails for any reason.

           The client session must be logged in (i.e., have valid credentials) before calling this method.

           This method can be overridden in subclasses to perform a more specific test request, if desired.

           Uses Notification/findUnreadNotifications as the test request rather than
           CodinGamer/findCodinGamerPublicInformations, since the latter is public and succeeds even
           when unauthenticated--it would not actually detect invalid/expired credentials. findUnreadNotifications
           requires authentication (it returns 422 when called without a valid session), and empirically appears
           to be side-effect-free (repeated calls return identical results, including `seenDate`).
        """
        if self.credentials is None:
            raise CgAuthenticationError("Client session is not logged in.")
        # Perform a test request to verify credentials
        try:
            codingamer_id = self.codingamer_id
            if codingamer_id is None:
                raise CgAuthenticationError("Client session is not logged in.")
            await self.service_request_to_list("Notification", "findUnreadNotifications", [ codingamer_id ])
        except CgClientHttpError as e:
            raise CgAuthenticationError("Invalid client credentials.") from e

    async def authenticate(
                self,
                *,
                profile_name: str | _Missing | None = MISSING,
                remember_me_token: str | None = None,
                cg_session_token: str | None = None,
                credentials: CgCredentials | None = None,
                force: bool = False,
                require_credentials: bool = False,
                validate: bool = False
            ) -> None:
        """Authenticate the client session, at one of three independent strictness levels
           (`require_credentials` x `validate`; a fourth level, no authentication at all, is
           available by simply not calling this method--see `service_request`'s `require_login`):

               require_credentials=False, validate=False (the default): best-effort. Resolves
                   credentials and applies them to the session if available, but does not raise
                   if none are available--the session is simply left unauthenticated.
               require_credentials=True,  validate=False: login required. Raises
                   CgAuthenticationError if no credentials are available. Does not check that
                   they are still valid/unexpired.
               require_credentials=True,  validate=True:  validated login required. Raises if no
                   credentials are available, and separately raises if they fail a live
                   validation check against the server (e.g. expired/revoked).

           (`require_credentials=False, validate=True` is also accepted: best-effort resolution,
           and if that happens to find credentials, they are validated too; if it doesn't, this
           is still not an error.)

        Resolution order:
            1. If force is False and credentials are already cached in the client, do nothing.
            2. If non-null `remember_me_token` / `cg_session_token` are provided, use those values.
            3. If `credentials` is provided, use non-null token values from that object.
            4. check the `REMEMBER_ME_TOKEN_ENV_VAR` / `CG_SESSION_TOKEN_ENV_VAR` environment variables for overrides.
            5. If neither is provided and force is False, check the in-process cache for the app's credentials.
            6. If not in the cache, check the per-app private credentials file (which populates the cache on success).
            7. If none of the above are available, return an empty `CgCredentials()`

        Args:
            profile_name: Optional name of the profile to use for persistent credentials. Allows
                          for multiple independent session profiles; e.g., if multiple CodinGame accounts are used.
                          If not provided or MISSING, the profile provided at client construction time is used.
                          If None, defaults to the default profile.
            remember_me_token: Optional override for the `rememberMe` cookie value.
            cg_session_token: Optional override for the `cgSession` cookie value.
            credentials: Optional `CgCredentials` object to use as the base for resolution.
            force: If True, ignore the client session and in-process cache and reload from the credentials file.
            require_credentials: If True, raise CgAuthenticationError if no usable credentials could
                          be resolved. If False (the default), silently leave the session unauthenticated.
            validate: If True, verify that the resolved credentials are valid by making a test request.
                          Has no effect if no credentials were resolved and `require_credentials` is False.
        """
        try:
            self.login_attempted = True
            if not force and self.credentials is not None:
                return
            resolved_credentials = self.resolve_credentials(
                profile_name=profile_name,
                remember_me_token=remember_me_token,
                cg_session_token=cg_session_token,
                credentials=credentials,
                force=force
            )
            have_credentials = (
                resolved_credentials.remember_me_cookie is not None
                and resolved_credentials.cg_session_cookie is not None
            )
            if not have_credentials:
                if require_credentials:
                    raise CgAuthenticationError(
                            "Both a rememberMe and a cgSession cookie are required to log in; "
                            "only one (or neither) was available."
                        )
                return
            self.set_credentials(resolved_credentials)
            if validate:
                await self.validate_credentials()
        except Exception:
            self.clear_credentials()
            raise

    async def require_authenticate(self) -> None:
        """Ensure that the client session is logged in (i.e., has both a rememberMe and a cgSession
           cookie--see `set_credentials` for why both are required). Implicitly log in if possible.
           If not, raise CgAuthenticationError."""
        if self.credentials is None and not self.login_attempted:
            await self.authenticate(require_credentials=True)
        if self.credentials is None:
            raise CgAuthenticationError()

    async def get_json_data_response(self, response: aiohttp.ClientResponse) -> JsonData:
        """Get a JSON-decoded response from an aiohttp response, raising CgClientHttpError if the
           response could not be decoded at all or if the status code is not 2xx.

           Unlike a strict JSON-RPC-style API, CodinGame's services may return any JSON-serializable
           value at the top level, not just an object--e.g., a bare array, or a bare `null` (some
           endpoints return `null` when unauthenticated; others return it as a legitimate "no result"
           value even when authenticated, e.g. ClashOfCode/getClashRankByCodinGamerId for a codingamer
           who has never played). A successfully-decoded JSON `null` is returned as Python `None`--a
           valid `JsonData` value. Every code path that fails to obtain/decode any content at all
           raises before returning, so a returned `None` unambiguously means "the body was the JSON
           literal `null`", never "nothing could be read". This method does not attempt to
           distinguish a JSON string value from equivalent raw (non-JSON) text content, though.

           Returns:
               The JSON-decoded data: a dict, list, str, int, float, bool, or None.

           Raises:
               CgClientHttpError:
                   If a transport error occurs, if the response content could not be decoded at all,
                   or if the status code is not 2xx.
        """

        # Note here that we attempt to decode the response as JSON even if the status code is not 2xx,
        # because some endpoints return JSON error messages with non-2xx status codes. The content will be included in the
        # raised CgClientHttpError for debugging purposes, and so that the caller can translate the error into a
        # more specific exception if desired.
        content: JsonData | bytes | None = None

        try:
            try:
                # First, we try to decode the response as JSON. If it fails, we try to read it as text or bytes.
                content = cast(JsonData, await response.json())
            except aiohttp.ContentTypeError as not_json_error:
                # Content-Type was not application/json, so we try to read the response as text or bytes.
                try:
                    content = await response.text()
                    # In some contexts, codingame does not properly supply a content-type header,
                    # so we try to parse the response as json anyway.
                    if response.content_type == "application/octet-stream":
                        with contextlib.suppress(json.JSONDecodeError):
                            content = cast(JsonData, json.loads(content))
                except Exception:
                    # content is neither JSON nor text, so we try to read it as bytes.
                    try:
                        content = await response.read()
                    except Exception:
                        # Could not fetch content at all. Before raising our own error, we try to
                        # raise the original error to get the correct status code and message.
                        response.raise_for_status()
                        ctype = response.headers.get(aiohttp.hdrs.CONTENT_TYPE, "<unspecified>").lower()
                        raise CgClientHttpError(
                                f"Unable to read response content in response (Content-Type: {ctype!r})",
                                response=response,
                            ) from not_json_error
            # at this point, content has been assigned a real decoded value--JSON data (possibly the
            # JSON `null` literal, decoded as Python None), a string, or bytes. Every path that failed
            # to assign one has already raised above.
            response.raise_for_status()
        except aiohttp.ClientResponseError as e:
            raise CgClientHttpError.normalize(e, content=content, response=response) from e
        if isinstance(content, (bytes, bytearray, memoryview)):
            # Raw bytes are not valid JsonData; this means we couldn't decode the content as JSON or text.
            raise CgClientHttpError(
                    f"Unable to decode response content as JSON or text (Content-Type: {response.content_type!r})",
                    response=response,
                    content=content
                )
        return content

    async def get_json_dict_response(self, response: aiohttp.ClientResponse) -> JsonDict:
        """Like `get_json_data_response`, but additionally requires the decoded content to be a JSON dict.

           Convenience wrapper for the common case where an endpoint is known to always return a
           JSON object on success.

           Returns:
               The JSON-decoded dictionary.

           Raises:
               CgClientHttpError:
                   If a transport error occurs, if the response content could not be decoded at all,
                   if the status code is not 2xx, or if the decoded content is not a dict.
        """
        content = await self.get_json_data_response(response)
        if not isinstance(content, dict):
            raise CgClientHttpError(
                    f"Invalid response type: expected a JSON dictionary, got {type(content).__name__}",
                    response=response,
                    content=content
                )
        return content

    async def get_json_list_response(self, response: aiohttp.ClientResponse) -> JsonList:
        """Like `get_json_data_response`, but additionally requires the decoded content to be a JSON list.

           Convenience wrapper for the common case where an endpoint is known to always return a
           JSON array on success.

           Returns:
               The JSON-decoded list.

           Raises:
               CgClientHttpError:
                   If a transport error occurs, if the response content could not be decoded at all,
                   if the status code is not 2xx, or if the decoded content is not a list.
        """
        content = await self.get_json_data_response(response)
        if not isinstance(content, list):
            raise CgClientHttpError(
                    f"Invalid response type: expected a JSON list, got {type(content).__name__}",
                    response=response,
                    content=content
                )
        return content

    async def _prepare_service_request(
                self,
                service_name: str,
                func_name: str,
                args: list[JsonData] | None,
                require_login: bool,
            ) -> tuple[str, list[JsonData]]:
        """Shared setup for `service_request`/`service_request_to_dict`/`service_request_to_list`:
           normalizes `args`, builds the endpoint URL, and ensures authentication if required."""
        if args is None:
            args = []
        endpoint_url = f"{self.CODINGAME_SERVICES_URL}{service_name}/{func_name}"
        if require_login:
            await self.require_authenticate()
        return endpoint_url, args

    async def service_request(
                self,
                service_name: str,
                func_name: str,
                args: list[JsonData] | None = None,
                *,
                require_login: bool = True
            ) -> JsonData:
        """Make an API request to a CodinGame service endpoint, returning its JSON-decoded response.

           This is the most common type of request made to the CodinGame API. It is used for most endpoints,
           except for file uploads and downloads.

           Generates a POST request to the URL https://www.codingame.com/services/{service_name}/{func_name}
           with a JSON body of `args`.

           In general, the session must be authenticated.

           This is a low-level method that does not distinguish between normal responses and error responses,
           and does not assume the response is a JSON object--some endpoints return a bare array, or a bare
           `null`, depending on the service and function called.

            Args:
                service_name: The name of the CodinGame service; e.g., "Vote", or "Contribution".
                func_name:    The name of the function to call within the service; e.g., "findContribution".
                args:         A list of JsonData positional arguments to pass to the function.
                require_login:
                              If True (the default), the session must be logged in (i.e., have both a
                              rememberMe and a cgSession cookie). If False, the request will be made
                              without requiring authentication, for endpoints that are genuinely public.

            Returns:
                The JSON-decoded response data. May be a successful response or an error response,
                depending on the service and function called.

            Raises:
                CgAuthenticationError:
                    If the session is not authenticated and cannot implicitly login.
                CgClientHttpError:
                    If a transport error occurs, if the response content could not be decoded at all,
                    or if the status code is not 2xx.
        """
        endpoint_url, args = await self._prepare_service_request(service_name, func_name, args, require_login)
        async with self.session.post(endpoint_url, json=args) as response:
            result = await self.get_json_data_response(response)
        return result

    async def service_request_to_dict(
                self,
                service_name: str,
                func_name: str,
                args: list[JsonData] | None = None,
                *,
                require_login: bool = True
            ) -> JsonDict:
        """Like `service_request`, but additionally requires (and type-checks) that the response is a JSON dict.

           See `service_request` for details on the request; see `get_json_dict_response` for details on
           the additional error condition.

            Raises:
                CgAuthenticationError:
                    If the session is not authenticated and cannot implicitly login.
                CgClientHttpError:
                    If a transport error occurs, if the response content could not be decoded at all,
                    if the status code is not 2xx, or if the decoded content is not a dict.
        """
        endpoint_url, args = await self._prepare_service_request(service_name, func_name, args, require_login)
        async with self.session.post(endpoint_url, json=args) as response:
            result = await self.get_json_dict_response(response)
        return result

    async def service_request_to_list(
                self,
                service_name: str,
                func_name: str,
                args: list[JsonData] | None = None,
                *,
                require_login: bool = True
            ) -> JsonList:
        """Like `service_request`, but additionally requires (and type-checks) that the response is a JSON list.

           See `service_request` for details on the request; see `get_json_list_response` for details on
           the additional error condition.

            Raises:
                CgAuthenticationError:
                    If the session is not authenticated and cannot implicitly login.
                CgClientHttpError:
                    If a transport error occurs, if the response content could not be decoded at all,
                    if the status code is not 2xx, or if the decoded content is not a list.
        """
        endpoint_url, args = await self._prepare_service_request(service_name, func_name, args, require_login)
        async with self.session.post(endpoint_url, json=args) as response:
            result = await self.get_json_list_response(response)
        return result

    @staticmethod
    def _build_servlet_url(base_url: str, servlet_name: str, params: dict[str, str] | None = None) -> str:
        """Build a servlet URL from a base URL (e.g. `CODINGAME_SERVLET_URL`), a servlet name
           (e.g. "fileupload"), and optional query string parameters."""
        url = f"{base_url}/{servlet_name}"
        if params:
            url += "?" + urlencode(params)
        return url

    async def servlet_get_bytes(
                self,
                base_url: str,
                servlet_name: str,
                params: dict[str, str] | None = None,
                *,
                require_login: bool = True,
            ) -> CgServletGetBytesResult:
        """Make a GET request to a CodinGame servlet endpoint, returning its raw content bytes
           along with the response (for its headers).

           Generates a GET request to `{base_url}/{servlet_name}`, with `params` (if any)
           URL-encoded as a query string.

           This is a low-level, content-shape-agnostic method--unlike `service_request*`, it does
           not assume a JSON response, since servlets like `fileservlet` return arbitrary binary
           content. Named `*_bytes` (rather than a general-purpose `servlet_get`) because the body
           is read and returned directly as `bytes`: aiohttp releases the underlying connection
           once the request's `async with` block exits, after which the response object's own
           `.read()`/`.text()` can no longer be called (though its `.headers`/`.status` remain
           readable)--a hypothetical future `servlet_get_json` (or similar) for a JSON-returning
           GET servlet would need its own decode-before-return method, not a shared one returning
           the raw response.

        Args:
            base_url:     The servlet's base URL, e.g. `CODINGAME_STATIC_SERVLET_URL`.
            servlet_name: The servlet's name, e.g. "fileservlet".
            params:       Optional query string parameters.
            require_login:
                          If True (the default), the session must be logged in. If False, the
                          request is made with whatever credentials (if any) are already attached
                          to the session--some servlets are genuinely public.

        Returns:
            A CgServletGetBytesResult(content, response)--the response body as bytes, and the
            aiohttp.ClientResponse (for reading headers such as Content-Type/Content-Disposition).

        Raises:
            CgAuthenticationError:
                If require_login is True and the session is not authenticated and cannot
                implicitly login.
            CgClientHttpError:
                If a transport error occurs, or if the status code is not 2xx.
        """
        url = self._build_servlet_url(base_url, servlet_name, params)
        if require_login:
            await self.require_authenticate()
        async with self.session.get(url) as response:
            try:
                response.raise_for_status()
                content = await response.read()
            except aiohttp.ClientResponseError as e:
                raise CgClientHttpError.normalize(e, content=None, response=response) from e
        return CgServletGetBytesResult(content, response)

    async def servlet_post(
                self,
                base_url: str,
                servlet_name: str,
                *,
                data: aiohttp.FormData | bytes | str | None = None,
                params: dict[str, str] | None = None,
                require_login: bool = True,
            ) -> JsonDict:
        """Make a POST request to a CodinGame servlet endpoint, returning its JSON-decoded dict response.

           Generates a POST request to `{base_url}/{servlet_name}` (with `params`, if any,
           URL-encoded as a query string) with the given request body.

           This is a low-level method that does not distinguish between normal responses and
           error responses, provided they are a valid JsonDict.

        Args:
            base_url:     The servlet's base URL, e.g. `CODINGAME_SERVLET_URL`.
            servlet_name: The servlet's name, e.g. "fileupload".
            data:         The request body, e.g. an `aiohttp.FormData` for a multipart request.
            params:       Optional query string parameters.
            require_login:
                          If True (the default), the session must be logged in.

        Returns:
            The JSON-decoded response as a dict. May be a successful response or an error
            response, depending on the servlet.

        Raises:
            CgAuthenticationError:
                If require_login is True and the session is not authenticated and cannot
                implicitly login.
            CgClientHttpError:
                If a transport error occurs, if the response content could not be decoded at all,
                if the status code is not 2xx, or if the decoded content is not a dict.
        """
        url = self._build_servlet_url(base_url, servlet_name, params)
        if require_login:
            await self.require_authenticate()
        async with self.session.post(url, data=data) as response:
            result = await self.get_json_dict_response(response)
        return result
