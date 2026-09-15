"""Persistent, user-editable configuration schema for CodinGame client tools (CLI, contribution
   manager, and potentially the client itself), stored as a YAML file. See
   `codingame_tools.config.resolver` for how the config file is located, and for `CgConfig`--the
   functional wrapper around this raw data that resolves defaults (e.g. `dataDir`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..common.dataclass_wizard_x import CatchAll, JSONWizardX
from ..settings import CgSettingsData

__all__ = [
    "CgConfigData",
]


@dataclass
class CgConfigData(JSONWizardX):
    """Raw, user-editable persistent configuration, as literally stored in a config.yaml file.
       Serialized using `JSONWizardX`'s default camelCase key convention (e.g. `dataDir`),
       matching every other JSON-shaped format in this project rather than switching to
       snake_case for this one file format.

       This is deliberately just a data container--fields here may be unset/None, and relative
       paths are stored as plain strings rather than resolved. See `CgConfig` (in
       `codingame_tools.config.resolver`) for the functional wrapper that resolves defaults and
       is what callers should normally use instead of this class directly.
    """

    # `extra_data` is deliberately the first field with a default: dataclass_wizard 1.0.0 mis-binds
    # any defaulted field positioned immediately before it (silently, no error) to the CatchAll's
    # own value. Keeping it first among the defaulted fields makes that impossible. There are no
    # required fields in this class, so it ends up first overall.
    extra_data: CatchAll = field(default_factory=dict)
    """Unrecognized fields encountered when loading a config file, preserved so that
       round-tripping through `saves()`/`loads()` does not silently drop data."""

    data_dir: str | None = None
    """Override for the persistent, app-writable data directory. A relative path is resolved
       relative to the directory containing this config file; an absolute path (or a `~`-prefixed
       path) is used as-is. If not set, defaults to a sibling "data" directory next to the
       directory containing this config file (e.g. `.cg/data`, alongside `.cg/config`). See
       `CgConfig.data_dir` for the resolved value--this raw field is usually not what callers
       want. Unlike `settings` below, this field is never merged across config files--it governs
       where *this* config file's own data directory (and thus its settings.json) lives, so only
       the single config file that discovery actually resolved to (see
       `codingame_tools.config.resolver.find_config_file`) is consulted for it."""

    template_path: str | list[str] | None = None
    """Search path for solution templates, used by `cg puzzle import`/`reset`/`set
       solution-language` when `--template-path` is not given (and extended by it when it is).

       Either one string, which may hold several directories separated the way `PATH` is, or a
       YAML list of them. Searched in order.

       Each entry is resolved against the directory holding this config file, the same as
       `dataDir` above -- so from `<project>/.cg/config/config.yaml`, `../../templates` means
       `<project>/templates`. One rule for every relative path in the file."""

    settings: CgSettingsData = field(default_factory=CgSettingsData)
    """Settings overridable from this config file--identical shape to settings.json's own
       `CgSettingsData` (`defaultProfile`/`contributionDir`/`puzzleDir`). Unlike settings.json,
       this is hand-edited, not app-written--there is no `cg config set`, only `cg settings set`
       (which only ever touches settings.json, never a config file).

       Resolution order, base to most refined: the global (per-user) config file's `settings`,
       then--if a *different*, project-local config file resolved--that file's own `settings`,
       then settings.json. Each tier overrides the previous one field-by-field (not all-or-
       nothing)--see `CgConfig.settings` for where the first two tiers get combined, and
       `codingame_tools.settings.CgSettings` for where settings.json gets layered on top as the
       final tier.

       `contributionDir`/`puzzleDir`, if given here as relative paths, are resolved against the
       resolved `CgConfig.data_dir` (where settings.json itself lives)--NOT this config file's
       own directory, and NOT the current working directory--regardless of which tier (global
       config, project config, or settings.json) actually set them. See
       `codingame_tools.settings.resolve_settings_dir`."""
