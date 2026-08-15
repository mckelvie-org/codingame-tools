"""Unit tests for how CgRawClient/CgClient resolve `profile_name` when it's not given
   explicitly: best-effort from settings/config, never requiring `cg config init` to have been
   run first (matching this project's existing best-effort credential-resolution philosophy).

These are pure/local tests--no network--so they run under the default `pdm run test` invocation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codingame_tools.client.common.raw_client import CgRawClient
from codingame_tools.config.cg_config import CgConfigData
from codingame_tools.config.resolver import CgConfig
from codingame_tools.settings import CgSettings, CgSettingsData


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CG_CONFIG", raising=False)


async def test_profile_name_defaults_when_nothing_configured(
    fake_home: Path, fake_global_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No config.yaml anywhere (project-local or global)--must not raise, must fall back to the
       hardcoded default profile name, exactly like construction always worked before config/
       settings resolution existed."""
    start = fake_home / "empty_dir"
    start.mkdir()
    monkeypatch.chdir(start)
    client = CgRawClient()
    try:
        assert client.profile_name == "default"
    finally:
        await client.close()


async def test_profile_name_resolved_from_project_config(
    fake_home: Path, fake_global_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = fake_home / "myproject"
    config_dir = project / ".cg" / "config"
    config_dir.mkdir(parents=True)
    CgConfigData(settings=CgSettingsData(default_profile="work")).save_yaml(config_dir / "config.yaml")
    monkeypatch.chdir(project)

    client = CgRawClient()
    try:
        assert client.profile_name == "work"
    finally:
        await client.close()


async def test_explicit_profile_name_skips_settings_resolution(
    fake_home: Path, fake_global_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicitly-given profile_name must win even if a config with a different
       defaultProfile is discoverable--settings resolution shouldn't even be attempted."""
    project = fake_home / "myproject"
    config_dir = project / ".cg" / "config"
    config_dir.mkdir(parents=True)
    CgConfigData(settings=CgSettingsData(default_profile="from-config")).save_yaml(config_dir / "config.yaml")
    monkeypatch.chdir(project)

    client = CgRawClient(profile_name="explicit")
    try:
        assert client.profile_name == "explicit"
    finally:
        await client.close()


async def test_explicit_settings_object_used_directly(
    fake_home: Path, fake_global_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passing a CgSettings object directly must be used as-is, without triggering discovery."""
    monkeypatch.chdir(fake_home)
    synthetic_config = CgConfig(config_file=fake_home / "unused.yaml", raw_data=CgConfigData())
    settings = CgSettings(
        settings_file=fake_home / "unused-settings.json",
        raw_data=CgSettingsData(default_profile="from-settings-object"),
        config=synthetic_config,
    )
    client = CgRawClient(settings=settings)
    try:
        assert client.profile_name == "from-settings-object"
    finally:
        await client.close()


async def test_broken_cg_config_env_var_still_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken explicit override is a real, surfaced error--not silently swallowed the way
       "nothing configured yet" is."""
    monkeypatch.setenv("CG_CONFIG", str(tmp_path / "does-not-exist.yaml"))
    with pytest.raises(FileNotFoundError):
        CgRawClient()
