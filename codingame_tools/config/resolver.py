"""Discovery and resolution of the config.yaml file and persistent data directory for CodinGame
   client tools (CLI, contribution manager, and potentially the client itself).

   This module is deliberately unopinionated about what happens when no config is found--it
   simply reports that clearly (`CgConfigNotFoundError`) or returns `None`. Any "create a default
   config" behavior (e.g. a CLI `cg config init` command) is a separate, CLI-specific concern
   layered on top of `write_config()`, not implemented here.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from json_data_types import JsonDict
from platformdirs.api import PlatformDirsABC

from ..common.typedefs import DEFAULT_PROFILE_NAME
from ..settings import CgSettingsData, overlay_settings_data, resolve_settings_dir
from .cg_config import CgConfigData

__all__ = [
    "CG_CONFIG_ENV_VAR",
    "PROJECT_CONFIG_MARKER_DIR_NAME",
    "CONFIG_SUBDIR_NAME",
    "DATA_SUBDIR_NAME",
    "CONFIG_FILE_NAME",
    "APP_NAME",
    "VENDOR_NAME",
    "CgConfigNotFoundError",
    "CgConfig",
    "default_global_config_file",
    "default_global_data_dir",
    "find_config_file",
    "resolve_config",
    "write_config",
]

CG_CONFIG_ENV_VAR = "CG_CONFIG"
"""Environment variable that can override config discovery, same as a `--config`/`-c` CLI flag
   (parsing/wiring that flag is the CLI layer's job--this module just accepts the resolved
   `explicit` value)."""

PROJECT_CONFIG_MARKER_DIR_NAME = ".cg"
"""Name of the project-local marker directory searched for, in the current directory and its
   ancestors."""

CONFIG_SUBDIR_NAME = "config"
"""Name of the subdirectory (within a project-local `.cg/` root) that holds config.yaml. Allows
   multiple sibling profile files (e.g. `config/experimental.yaml`) to share one persistent data
   directory by default--see `CgConfig.data_dir`."""

DATA_SUBDIR_NAME = "data"
"""Name of the sibling subdirectory (next to a project-local `.cg/config/`, or appended to the
   global per-user data directory) that holds persistent app-writable state by default."""

CONFIG_FILE_NAME = "config.yaml"
"""The config file's name."""

APP_NAME = "cg"
"""App name used for the global (per-user) fallback config/data locations."""

VENDOR_NAME = "codingame"
"""Vendor namespace the global fallback locations are nested under."""

_VCS_MARKER_DIR_NAMES = (".git", ".hg", ".svn")
"""Directory names that mark a VCS root, used as an upward-search stopping condition."""


class CgConfigNotFoundError(Exception):
    """Raised when no config.yaml could be located by any discovery step. Does not indicate a
       bug--this is the normal outcome before a config has been set up. Callers that want to
       offer to create one (e.g. a CLI `cg config init` command) should catch this and act on it;
       `resolve_config()` itself never creates anything."""

    def __init__(self) -> None:
        super().__init__(
                "No configuration file found. Run `cg config init` to create one "
                "(or `cg config init --global` for the per-user default location)."
            )


def _global_platformdirs() -> PlatformDirsABC:
    """Return the `platformdirs` backend for the global (per-user) fallback location.

       Forces the Unix/XDG backend on macOS and Linux alike (matching common developer-tool
       convention, e.g. `gh`, `pipx`) rather than macOS's native `~/Library/Application Support`.
       Uses the native Windows backend, unmodified, on real (non-WSL) Windows--WSL reports
       `sys.platform == "linux"`, so it already gets the forced Unix/XDG behavior automatically,
       with no special-casing needed; forcing XDG on native Windows is NOT common practice there
       (no native `$HOME`/dotfile convention, unlike macOS's still-POSIX terminal environment), so
       it deliberately isn't done here.
    """
    if sys.platform == "win32":
        from platformdirs.windows import Windows
        return Windows(appname=APP_NAME, appauthor=VENDOR_NAME)
    from platformdirs.unix import Unix
    # The Unix backend ignores `appauthor`; a "/"-joined appname is how vendor/app nesting is
    # done there instead (confirmed empirically: it produces e.g. ~/.config/codingame/cg).
    return Unix(appname=f"{VENDOR_NAME}/{APP_NAME}")


def default_global_config_file() -> Path:
    """The global (per-user) fallback config.yaml location, e.g.
       `~/.config/codingame/cg/config.yaml` on Linux/macOS, `%AppData%\\codingame\\cg\\config.yaml`
       on native Windows."""
    return Path(_global_platformdirs().user_config_dir) / CONFIG_FILE_NAME


def default_global_data_dir() -> Path:
    """The global (per-user) fallback persistent data directory, used when a config file resolved
       from the global fallback location doesn't override `dataDir` itself. No `config/`+`data/`
       sibling nesting is needed here (unlike the project-local case)--the OS already separates
       config from data into distinct top-level locations."""
    return Path(_global_platformdirs().user_data_dir) / DATA_SUBDIR_NAME


def default_global_cache_dir() -> Path:
    """The global (per-user) cache directory, e.g. `~/.cache/codingame/cg` on Linux/macOS.

       For output that can be regenerated from the source at any time and is never authored by the
       user -- unlike `default_global_data_dir()`, whose contents would be a real loss. Anything
       here must survive being deleted between runs."""
    return Path(_global_platformdirs().user_cache_dir)


def _resolve_explicit(value: str) -> Path:
    """Resolve an explicit `--config`/`CG_CONFIG` value (a config file, or a directory containing
       `config/config.yaml`) to a config file path.

    Raises:
        FileNotFoundError: if the resolved config file does not exist.
    """
    path = Path(value).expanduser()
    candidate = (path / CONFIG_SUBDIR_NAME / CONFIG_FILE_NAME) if path.is_dir() else path
    if not candidate.is_file():
        raise FileNotFoundError(f"Config file not found: {candidate}")
    return candidate


def _config_file_in(directory: Path) -> Path | None:
    """Check `directory` itself for a resolvable config.yaml.

       If `directory` is itself named `.cg` (e.g. the user `cd`'d directly into it), checks
       `directory/config/config.yaml`. Regardless, also checks `directory/.cg/config/config.yaml`
       (the normal case--`directory` is a project root with a `.cg/` subdirectory). Applying both
       checks at every level of the upward walk (not just the very first/starting directory)
       keeps this uniform rather than special-casing only the initial directory.
    """
    if directory.name == PROJECT_CONFIG_MARKER_DIR_NAME:
        candidate = directory / CONFIG_SUBDIR_NAME / CONFIG_FILE_NAME
        if candidate.is_file():
            return candidate
    candidate = directory / PROJECT_CONFIG_MARKER_DIR_NAME / CONFIG_SUBDIR_NAME / CONFIG_FILE_NAME
    if candidate.is_file():
        return candidate
    return None


def _find_project_config_file(start: Path) -> Path | None:
    """Search `start` and its ancestors (in that order) for a resolvable config.yaml (see
       `_config_file_in`).

       Stops the search--after still checking that directory--at the first of: a directory
       containing a `.git`/`.hg`/`.svn` marker (the natural project boundary; precedent: git's own
       `.git` discovery, black/ruff's `pyproject.toml` discovery), the user's home directory, or
       the filesystem root.
    """
    home = Path.home().resolve()
    current = start.resolve()
    while True:
        found = _config_file_in(current)
        if found is not None:
            return found
        if current == home:
            return None
        if any((current / marker).is_dir() for marker in _VCS_MARKER_DIR_NAMES):
            return None
        parent = current.parent
        if parent == current:
            return None  # filesystem root reached
        current = parent


def find_config_file(
            explicit: Path | str | None = None,
            *,
            start_dir: Path | str | None = None,
        ) -> Path | None:
    """Locate the config.yaml file to use, following the documented discovery precedence:

        1. `explicit` (typically the resolved value of a `--config`/`-c` CLI flag), if given.
        2. The `CG_CONFIG` environment variable, if set.
        3. `start_dir` (or the current directory, if not given) and its ancestors, searched
           upward--see `_find_project_config_file` for the stopping policy.
        4. The global (per-user) fallback location, if it exists.

       Whenever a step resolves to a directory rather than a file, that's purely shorthand for
       "look for config/config.yaml inside it"--there is only one resolution algorithm, not a
       directory-mode vs. file-mode branch.

    Raises:
        FileNotFoundError: if `explicit` or `CG_CONFIG` is given but the file it resolves to
                            doesn't exist. Steps 3/4 never raise--they just don't match.

    Returns:
        The resolved config file path, or None if nothing was found at all. This function never
        creates anything.
    """
    if explicit is not None:
        return _resolve_explicit(str(explicit))
    env_value = os.environ.get(CG_CONFIG_ENV_VAR)
    if env_value:
        return _resolve_explicit(env_value)
    start = Path(start_dir) if start_dir is not None else Path.cwd()
    found = _find_project_config_file(start)
    if found is not None:
        return found
    global_file = default_global_config_file()
    if global_file.is_file():
        return global_file
    return None


@dataclass
class CgConfig:
    """A resolved, functional configuration: pairs the raw `CgConfigData` loaded from a
       config.yaml file with that file's own location, and resolves defaults (e.g. `data_dir`)
       reliably--this is the class callers should normally use, rather than `CgConfigData`
       directly.

       `data_dir` (and thus `config_file`/`config_dir`) reflect only the single config file that
       `find_config_file()` actually resolved to--never merged across files, since `data_dir`
       determines where *this* config's own settings.json lives. `settings` (and the
       `default_profile`/`contribution_dir`/`puzzle_dir` properties built on it) is different:
       it's overlaid with the global (per-user) config file's own `settings` whenever a separate
       project-local config file is the one that resolved--see `settings` below."""

    config_file: Path
    """The resolved, absolute path to the config.yaml file that was loaded."""

    raw_data: CgConfigData
    """The raw configuration as loaded from `config_file`, unresolved (fields may be None/relative)."""

    @property
    def config_dir(self) -> Path:
        """The directory containing `config_file`."""
        return self.config_file.parent

    @property
    def data_dir(self) -> Path:
        """The resolved persistent data directory.

           If `raw_data.data_dir` is set, it's used (relative paths resolved against
           `config_dir`, `~` expanded). Otherwise: if `config_file` is the global fallback
           location, the global fallback data directory is used; otherwise, a sibling "data"
           directory next to `config_dir` is used (e.g. `.cg/data`, alongside `.cg/config`)--
           including for an explicit/env-provided config file that isn't part of a `.cg/`-style
           layout at all, in which case this literally means "a data directory next to wherever
           that file lives", which may not be what's wanted. Callers relying on shared state
           across such overrides should set an explicit `dataDir` in the file.
        """
        override = self.raw_data.data_dir
        if override is not None:
            override_path = Path(override).expanduser()
            if not override_path.is_absolute():
                override_path = (self.config_dir / override_path).resolve()
            return override_path
        if self.config_file == default_global_config_file():
            return default_global_data_dir()
        return (self.config_dir / ".." / DATA_SUBDIR_NAME).resolve()

    @property
    def settings(self) -> CgSettingsData:
        """This config file's own `settings` (see `CgConfigData.settings`), overlaid on the
           global (per-user) config file's `settings`--i.e. the global file's settings.json-
           shaped fields, then this file's own, each overriding the previous field-by-field.

           If `config_file` *is* the global fallback location (no separate project config
           resolved), or the global config file doesn't exist, this is just `raw_data.settings`
           unchanged--there's nothing else to overlay it onto. Re-reads the global config file
           from disk on every access (uncached)--config files are tiny and this isn't a hot
           path, so simplicity wins over caching here.

           This is NOT the final resolved value--`codingame_tools.settings.CgSettings` layers
           settings.json on top of this as the most-refined tier. See `default_profile`/
           `contribution_dir`/`puzzle_dir` below for this config-level tier's own resolved
           values (i.e. as if settings.json didn't exist)."""
        global_file = default_global_config_file()
        if self.config_file == global_file or not global_file.is_file():
            return self.raw_data.settings
        global_settings = CgConfigData.load_yaml(global_file).settings
        return overlay_settings_data(global_settings, self.raw_data.settings)

    @property
    def default_profile(self) -> str:
        """The default codingame-tools credential profile name to use (see `settings` above for
           the global/project config merge), falling back to `DEFAULT_PROFILE_NAME` ("default")
           if neither sets it. See `CgSettings.default_profile` for the app-writable
           settings.json override that takes precedence over this one."""
        value = self.settings.default_profile
        return value if value is not None else DEFAULT_PROFILE_NAME

    @property
    def contribution_dir(self) -> Path | None:
        """The configured default contribution working directory (see `settings` above for the
           global/project config merge), resolved to an absolute path--a relative value is
           resolved against `data_dir` (where this config's own settings.json lives), NOT the
           current working directory--or `None` if neither config file sets it. See
           `CgSettings.contribution_dir` for the settings.json override that takes precedence
           over this one, and the further cwd-based discovery that follows if even that's unset."""
        return resolve_settings_dir(self.settings.contribution_dir, self.data_dir)

    @property
    def puzzle_dir(self) -> Path | None:
        """The configured default puzzle working directory. Same resolution chain as
           `contribution_dir`--see `CgSettings.puzzle_dir`."""
        return resolve_settings_dir(self.settings.puzzle_dir, self.data_dir)

    @property
    def toolchain_languages(self) -> list[str] | None:
        """Which languages the container toolchain image should carry (see `settings` above for the
           global/project config merge), or `None` to include every language cg can containerize.
           See `CgSettings.toolchain_languages` for the settings.json override."""
        return self.settings.toolchain_languages

    @property
    def toolchain_image(self) -> str | None:
        """A prebuilt toolchain image tag to use instead of composing and building one locally, or
           `None` to build. See `CgSettings.toolchain_image` for the settings.json override."""
        return self.settings.toolchain_image

    def save(self) -> None:
        """Write `raw_data` back to `config_file`."""
        write_config(self.raw_data, self.config_file)

    def to_dump_dict(self) -> JsonDict:
        """Assemble a JSON-friendly summary for e.g. `cg config dump`: resolved values at the top
           level (`"settings"` nested the same way `CgConfigData.settings` itself is, holding the
           global+project merge--`default_profile`/`contribution_dir`/`puzzle_dir` above), plus
           the raw (unresolved) config content--this file alone, not merged--under `"rawConfig"`."""
        return {
            "configFile": str(self.config_file),
            "dataDir": str(self.data_dir),
            "settings": {
                "defaultProfile": self.default_profile,
                "contributionDir": str(self.contribution_dir) if self.contribution_dir is not None else None,
                "puzzleDir": str(self.puzzle_dir) if self.puzzle_dir is not None else None,
            },
            "rawConfig": self.raw_data.to_dict(),
        }


def resolve_config(
            explicit: Path | str | None = None,
            *,
            start_dir: Path | str | None = None,
            allow_default: bool = False,
        ) -> CgConfig:
    """Locate and load the config.yaml file, following the documented discovery precedence (see
       `find_config_file`).

       If `allow_default` is True and no config file can be found (discovery steps 1-4 all
       fail), returns a synthetic `CgConfig` pointing at the global fallback location, backed by
       `CgConfigData()` (all defaults)--nothing is read from or written to disk for this synthetic
       result; it just gives `CgConfig`'s resolution properties (`data_dir`, `default_profile`,
       etc.) something sensible to fall back to, for best-effort callers (e.g. client
       construction) that shouldn't require `cg config init` to have been run first. This does
       NOT suppress `FileNotFoundError` from a broken explicit/`CG_CONFIG` override--that's a
       real, surfaced error regardless of `allow_default`, since the caller asked for something
       specific that doesn't exist, which is different from "nothing configured yet".

    Raises:
        FileNotFoundError: if `explicit`/`CG_CONFIG` is given but doesn't resolve to a real file.
        CgConfigNotFoundError: if no config file could be located anywhere, and `allow_default`
                                is False.
    """
    config_file = find_config_file(explicit, start_dir=start_dir)
    if config_file is None:
        if allow_default:
            return CgConfig(config_file=default_global_config_file(), raw_data=CgConfigData())
        raise CgConfigNotFoundError()
    raw_data = CgConfigData.load_yaml(config_file)
    return CgConfig(config_file=config_file.resolve(), raw_data=raw_data)


def write_config(config: CgConfigData, config_file: Path | str) -> None:
    """Write `config` to `config_file` as YAML, creating parent directories if necessary.

       A generic primitive--no opinion about default content, overwrite confirmation, or
       `--global` vs. project-local placement; that's the CLI layer's job (e.g. `cg config init`).
    """
    path = Path(config_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    config.save_yaml(path)
