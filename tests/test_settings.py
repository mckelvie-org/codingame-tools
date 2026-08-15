"""Unit tests for codingame_tools.settings: CgSettingsData/CgSettings resolution and
   the defaultProfile fallback chain (settings.json -> config.yaml -> hardcoded "default").

These are pure/local tests--no network--so they run under the default `pdm run test` invocation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codingame_tools.config.cg_config import CgConfigData
from codingame_tools.config.resolver import CgConfig
from codingame_tools.settings import (
    SETTINGS_FILE_NAME,
    CgSettings,
    CgSettingsData,
    relativize_settings_dir,
    resolve_settings,
    resolve_settings_dir,
    write_settings,
)


def _make_config(tmp_path: Path, *, default_profile: str | None = None) -> CgConfig:
    config_file = tmp_path / ".cg" / "config" / "config.yaml"
    config_file.parent.mkdir(parents=True)
    raw_data = CgConfigData(settings=CgSettingsData(default_profile=default_profile))
    raw_data.save_yaml(config_file)
    return CgConfig(config_file=config_file, raw_data=raw_data)


def test_resolve_settings_defaults_when_file_missing(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    settings = resolve_settings(config)
    assert settings.raw_data == CgSettingsData()
    assert settings.settings_file == config.data_dir / SETTINGS_FILE_NAME
    assert not settings.settings_file.exists()


def test_default_profile_falls_back_to_hardcoded_default(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    settings = resolve_settings(config)
    assert settings.default_profile == "default"


def test_default_profile_falls_back_to_config_when_settings_unset(tmp_path: Path) -> None:
    config = _make_config(tmp_path, default_profile="from-config")
    settings = resolve_settings(config)
    assert settings.default_profile == "from-config"


def test_default_profile_settings_override_wins_over_config(tmp_path: Path) -> None:
    config = _make_config(tmp_path, default_profile="from-config")
    settings = CgSettings(
        settings_file=config.data_dir / SETTINGS_FILE_NAME,
        raw_data=CgSettingsData(default_profile="from-settings"),
        config=config,
    )
    assert settings.default_profile == "from-settings"


def test_settings_save_and_resolve_round_trip(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    settings = resolve_settings(config)
    settings.raw_data.default_profile = "work"
    settings.save()
    assert settings.settings_file.is_file()

    reloaded = resolve_settings(config)
    assert reloaded.raw_data.default_profile == "work"
    assert reloaded.default_profile == "work"


def test_write_settings_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "settings.json"
    write_settings(CgSettingsData(default_profile="x"), target)
    assert target.is_file()
    assert CgSettingsData.load(target).default_profile == "x"


def test_contribution_dir_falls_back_to_config_when_settings_unset(tmp_path: Path) -> None:
    config_file = tmp_path / ".cg" / "config" / "config.yaml"
    config_file.parent.mkdir(parents=True)
    raw_data = CgConfigData(settings=CgSettingsData(contribution_dir="from-config"))
    raw_data.save_yaml(config_file)
    config = CgConfig(config_file=config_file, raw_data=raw_data)
    settings = resolve_settings(config)

    # relative to data_dir (where settings.json lives), not cwd
    assert settings.contribution_dir == config.data_dir / "from-config"


def test_contribution_dir_settings_override_wins_over_config(tmp_path: Path) -> None:
    config_file = tmp_path / ".cg" / "config" / "config.yaml"
    config_file.parent.mkdir(parents=True)
    raw_data = CgConfigData(settings=CgSettingsData(contribution_dir="from-config"))
    raw_data.save_yaml(config_file)
    config = CgConfig(config_file=config_file, raw_data=raw_data)
    settings = CgSettings(
        settings_file=config.data_dir / SETTINGS_FILE_NAME,
        raw_data=CgSettingsData(contribution_dir="from-settings"),
        config=config,
    )

    # relative to settings_file's own directory (== config.data_dir), not cwd
    assert settings.contribution_dir == config.data_dir / "from-settings"


def test_contribution_dir_none_when_unset_anywhere(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    settings = resolve_settings(config)
    assert settings.contribution_dir is None
    assert settings.puzzle_dir is None


# --- resolve_settings_dir / relativize_settings_dir -----------------------------------------


def test_resolve_settings_dir_none_passes_through() -> None:
    assert resolve_settings_dir(None, Path("/some/base")) is None


def test_resolve_settings_dir_relative_resolved_against_base() -> None:
    base = Path("/some/base")
    assert resolve_settings_dir("sub/dir", base) == base / "sub/dir"


def test_resolve_settings_dir_absolute_used_as_is() -> None:
    base = Path("/some/base")
    assert resolve_settings_dir("/elsewhere/dir", base) == Path("/elsewhere/dir")


def test_relativize_settings_dir_absolute_input_stored_as_is(tmp_path: Path) -> None:
    absolute = tmp_path / "elsewhere"
    assert relativize_settings_dir(absolute, tmp_path / "base") == str(absolute)


def test_relativize_settings_dir_relative_input_resolved_against_cwd_then_rebased(
            tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        ) -> None:
    base = tmp_path / "base"
    base.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    stored = relativize_settings_dir(Path("myrepo"), base)

    # round-trips back to the same absolute location the user actually meant (cwd/myrepo),
    # even though it's now expressed relative to `base` instead
    assert resolve_settings_dir(stored, base) == cwd / "myrepo"


def test_set_then_resolve_round_trips_regardless_of_later_cwd(
            tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        ) -> None:
    """End-to-end: a relative dir typed at set-time must resolve to the same absolute location
       later, even if `cg` is run from a different cwd by then--the bug this was built to fix."""
    base = tmp_path / "base"
    base.mkdir()
    set_time_cwd = tmp_path / "project"
    set_time_cwd.mkdir()
    monkeypatch.chdir(set_time_cwd)
    stored = relativize_settings_dir(Path("contribution"), base)

    later_cwd = tmp_path / "somewhere" / "else"
    later_cwd.mkdir(parents=True)
    monkeypatch.chdir(later_cwd)

    assert resolve_settings_dir(stored, base) == set_time_cwd / "contribution"


def test_to_dump_dict_has_resolved_and_raw(tmp_path: Path) -> None:
    config = _make_config(tmp_path, default_profile="from-config")
    settings = resolve_settings(config)
    d = settings.to_dump_dict()
    assert d["settingsFile"] == str(settings.settings_file)
    assert d["defaultProfile"] == "from-config"
    assert d["rawSettings"] == {}  # nothing set in settings.json itself
