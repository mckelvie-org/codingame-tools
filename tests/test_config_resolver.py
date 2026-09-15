"""Unit tests for codingame_tools.config.resolver: config.yaml discovery precedence, the
   upward-search stopping policy, and the persistent data-directory resolution rules.

These are pure/local tests--no network--so they run under the default `pdm run test` invocation.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from codingame_tools.config.cg_config import CgConfigData
from codingame_tools.config.resolver import (
    CONFIG_FILE_NAME,
    CONFIG_SUBDIR_NAME,
    DATA_SUBDIR_NAME,
    PROJECT_CONFIG_MARKER_DIR_NAME,
    CgConfig,
    CgConfigNotFoundError,
    default_global_config_file,
    find_config_file,
    resolve_config,
    write_config,
)
from codingame_tools.settings import CgSettingsData


def _write_project_config(root: Path, *, data_dir: str | None = None) -> Path:
    """Create `<root>/.cg/config/config.yaml` (and its parent dirs) and return its path."""
    config_dir = root / PROJECT_CONFIG_MARKER_DIR_NAME / CONFIG_SUBDIR_NAME
    config_dir.mkdir(parents=True)
    config_file = config_dir / CONFIG_FILE_NAME
    CgConfigData(data_dir=data_dir).save_yaml(config_file)
    return config_file


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CG_CONFIG", raising=False)


# --- explicit / env precedence -------------------------------------------------------------


def test_explicit_file_path_used_when_given(tmp_path: Path) -> None:
    config_file = tmp_path / "somewhere" / "custom.yaml"
    config_file.parent.mkdir()
    CgConfigData().save_yaml(config_file)
    assert find_config_file(config_file) == config_file


def test_explicit_dir_looks_for_config_subdir(tmp_path: Path) -> None:
    config_file = _write_project_config(tmp_path)
    # Passing the ".cg" directory itself should find "config/config.yaml" inside it.
    resolved = find_config_file(tmp_path / PROJECT_CONFIG_MARKER_DIR_NAME)
    assert resolved == config_file


def test_explicit_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        find_config_file(tmp_path / "does-not-exist.yaml")


def test_env_var_used_when_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_file = tmp_path / "custom.yaml"
    CgConfigData().save_yaml(config_file)
    monkeypatch.setenv("CG_CONFIG", str(config_file))
    assert find_config_file() == config_file


def test_explicit_overrides_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / "env.yaml"
    CgConfigData().save_yaml(env_file)
    explicit_file = tmp_path / "explicit.yaml"
    CgConfigData().save_yaml(explicit_file)
    monkeypatch.setenv("CG_CONFIG", str(env_file))
    assert find_config_file(explicit_file) == explicit_file


# --- cwd-is-.cg special case ----------------------------------------------------------------


def test_cwd_named_dot_cg_is_special_cased(tmp_path: Path) -> None:
    project = tmp_path / "myproject"
    dot_cg = project / PROJECT_CONFIG_MARKER_DIR_NAME
    (dot_cg / CONFIG_SUBDIR_NAME).mkdir(parents=True)
    config_file = dot_cg / CONFIG_SUBDIR_NAME / CONFIG_FILE_NAME
    CgConfigData().save_yaml(config_file)
    assert find_config_file(start_dir=dot_cg) == config_file


# --- upward search -----------------------------------------------------------------------


def test_upward_search_finds_ancestor_dot_cg(tmp_path: Path) -> None:
    project = tmp_path / "myproject"
    subdir = project / "sub" / "deeper"
    subdir.mkdir(parents=True)
    config_file = _write_project_config(project)
    assert find_config_file(start_dir=subdir) == config_file


def test_upward_search_stops_at_vcs_root(tmp_path: Path, fake_global_root: Path) -> None:
    """A .cg/ above a .git directory must not be found--the VCS root is the project boundary."""
    outer_config = _write_project_config(tmp_path)
    project = tmp_path / "myproject"
    (project / ".git").mkdir(parents=True)
    subdir = project / "sub"
    subdir.mkdir()

    assert find_config_file(start_dir=subdir) is None
    # Sanity check: the outer config really is discoverable without the .git boundary in the way.
    assert find_config_file(start_dir=tmp_path) == outer_config


def test_upward_search_still_checks_the_vcs_root_directory_itself(tmp_path: Path) -> None:
    """The stop is inclusive: a .cg/ living in the same directory as .git must still be found."""
    project = tmp_path / "myproject"
    (project / ".git").mkdir(parents=True)
    config_file = _write_project_config(project)
    subdir = project / "sub"
    subdir.mkdir()
    assert find_config_file(start_dir=subdir) == config_file


def test_upward_search_stops_at_home(tmp_path: Path, fake_home: Path, fake_global_root: Path) -> None:
    """A .cg/ above $HOME must not be found."""
    outer_config = _write_project_config(tmp_path)
    subdir = fake_home / "projects" / "myproject"
    subdir.mkdir(parents=True)

    assert find_config_file(start_dir=subdir) is None
    assert find_config_file(start_dir=tmp_path) == outer_config


# --- global fallback -----------------------------------------------------------------------


def test_global_fallback_used_when_nothing_else_found(
    tmp_path: Path, fake_home: Path, fake_global_root: Path
) -> None:
    global_config = fake_global_root / "config" / CONFIG_FILE_NAME
    global_config.parent.mkdir(parents=True)
    CgConfigData().save_yaml(global_config)
    start = fake_home / "empty_dir"
    start.mkdir()
    assert find_config_file(start_dir=start) == global_config


def test_find_config_file_returns_none_when_nothing_found(tmp_path: Path, fake_home: Path, fake_global_root: Path) -> None:
    start = fake_home / "empty_dir"
    start.mkdir()
    assert find_config_file(start_dir=start) is None


def test_resolve_config_raises_not_found(tmp_path: Path, fake_home: Path, fake_global_root: Path) -> None:
    start = fake_home / "empty_dir"
    start.mkdir()
    with pytest.raises(CgConfigNotFoundError):
        resolve_config(start_dir=start)


def test_resolve_config_allow_default_returns_synthetic_config_without_touching_disk(
    tmp_path: Path, fake_home: Path, fake_global_root: Path
) -> None:
    start = fake_home / "empty_dir"
    start.mkdir()
    resolved = resolve_config(start_dir=start, allow_default=True)
    assert resolved.config_file == default_global_config_file()
    assert resolved.raw_data == CgConfigData()
    assert resolved.default_profile == "default"
    assert not resolved.config_file.exists()  # nothing written/read from disk for the synthetic result


def test_resolve_config_allow_default_still_used_when_a_real_config_exists(
    tmp_path: Path, fake_home: Path, fake_global_root: Path
) -> None:
    """allow_default only kicks in when nothing is found--a real config still wins normally."""
    config_file = _write_project_config(tmp_path, data_dir="../custom")
    resolved = resolve_config(start_dir=tmp_path, allow_default=True)
    assert resolved.config_file == config_file


def test_resolve_config_allow_default_does_not_suppress_broken_explicit_override(tmp_path: Path) -> None:
    """A broken --config/CG_CONFIG override is a real error, not "nothing configured yet"--
       allow_default must not swallow it."""
    with pytest.raises(FileNotFoundError):
        resolve_config(tmp_path / "does-not-exist.yaml", allow_default=True)


# --- data_dir resolution --------------------------------------------------------------------


def test_data_dir_defaults_to_sibling_of_config_dir(tmp_path: Path) -> None:
    config_file = _write_project_config(tmp_path)
    resolved = CgConfig(config_file=config_file, raw_data=CgConfigData.load_yaml(config_file))
    expected = tmp_path / PROJECT_CONFIG_MARKER_DIR_NAME / DATA_SUBDIR_NAME
    assert resolved.data_dir == expected


def test_data_dir_relative_override(tmp_path: Path) -> None:
    config_file = _write_project_config(tmp_path, data_dir="../custom-data")
    resolved = CgConfig(config_file=config_file, raw_data=CgConfigData.load_yaml(config_file))
    expected = (tmp_path / PROJECT_CONFIG_MARKER_DIR_NAME / "custom-data").resolve()
    assert resolved.data_dir == expected


def test_data_dir_absolute_override(tmp_path: Path) -> None:
    absolute = tmp_path / "elsewhere"
    config_file = _write_project_config(tmp_path, data_dir=str(absolute))
    resolved = CgConfig(config_file=config_file, raw_data=CgConfigData.load_yaml(config_file))
    assert resolved.data_dir == absolute


def test_data_dir_global_fallback_uses_platformdirs_data_dir(fake_global_root: Path) -> None:
    global_config = fake_global_root / "config" / CONFIG_FILE_NAME
    global_config.parent.mkdir(parents=True)
    CgConfigData().save_yaml(global_config)
    resolved = CgConfig(config_file=global_config, raw_data=CgConfigData.load_yaml(global_config))
    assert resolved.data_dir == fake_global_root / "data" / DATA_SUBDIR_NAME


# --- default_profile resolution ---------------------------------------------------------------


def test_default_profile_defaults_to_hardcoded_default(tmp_path: Path) -> None:
    config_file = _write_project_config(tmp_path)
    resolved = CgConfig(config_file=config_file, raw_data=CgConfigData.load_yaml(config_file))
    assert resolved.default_profile == "default"


def test_default_profile_override(tmp_path: Path) -> None:
    config_file = _write_project_config(tmp_path)
    CgConfigData(settings=CgSettingsData(default_profile="work")).save_yaml(config_file)
    resolved = CgConfig(config_file=config_file, raw_data=CgConfigData.load_yaml(config_file))
    assert resolved.default_profile == "work"


# --- settings resolution (global config.yaml <-> project config.yaml merge) -----------------


def test_settings_falls_back_to_global_config_settings_field_by_field(
            tmp_path: Path, fake_global_root: Path,
        ) -> None:
    """A project config.yaml that doesn't mention defaultProfile at all must not mask the
       global config.yaml's own defaultProfile--regression test for the bug this was built to
       fix (previously: whichever single config.yaml find_config_file() picked won outright,
       with no per-field merge)."""
    global_config = fake_global_root / "config" / CONFIG_FILE_NAME
    global_config.parent.mkdir(parents=True)
    CgConfigData(settings=CgSettingsData(default_profile="sammck")).save_yaml(global_config)
    project_config = _write_project_config(tmp_path)
    CgConfigData(settings=CgSettingsData(project_dir="myrepo")).save_yaml(project_config)

    resolved = CgConfig(config_file=project_config, raw_data=CgConfigData.load_yaml(project_config))

    assert resolved.default_profile == "sammck"  # from the global file, not masked
    # from the project file--relative to data_dir (where settings.json lives), not cwd
    assert resolved.project_dir == resolved.data_dir / "myrepo"


def test_project_settings_field_overrides_global_settings_field(
            tmp_path: Path, fake_global_root: Path,
        ) -> None:
    global_config = fake_global_root / "config" / CONFIG_FILE_NAME
    global_config.parent.mkdir(parents=True)
    CgConfigData(settings=CgSettingsData(default_profile="global-default")).save_yaml(global_config)
    project_config = _write_project_config(tmp_path)
    CgConfigData(settings=CgSettingsData(default_profile="project-default")).save_yaml(project_config)

    resolved = CgConfig(config_file=project_config, raw_data=CgConfigData.load_yaml(project_config))

    assert resolved.default_profile == "project-default"


def test_settings_is_unaffected_by_a_nonexistent_global_config(tmp_path: Path, fake_global_root: Path) -> None:
    project_config = _write_project_config(tmp_path)
    CgConfigData(settings=CgSettingsData(default_profile="only-project")).save_yaml(project_config)

    resolved = CgConfig(config_file=project_config, raw_data=CgConfigData.load_yaml(project_config))

    assert resolved.default_profile == "only-project"
    assert resolved.project_dir is None


def test_project_dir_resolves_relative_to_data_dir_not_cwd(
            tmp_path: Path, fake_global_root: Path, monkeypatch: pytest.MonkeyPatch,
        ) -> None:
    """Regression test: a relative projectDir must resolve against data_dir (where
       settings.json lives), not whatever the current working directory happens to be--otherwise
       the effective directory moves around depending on where `cg` is run from."""
    project_config = _write_project_config(tmp_path)
    CgConfigData(settings=CgSettingsData(project_dir="myrepo")).save_yaml(project_config)
    resolved = CgConfig(config_file=project_config, raw_data=CgConfigData.load_yaml(project_config))
    elsewhere = tmp_path / "some" / "other" / "cwd"
    elsewhere.mkdir(parents=True)
    monkeypatch.chdir(elsewhere)

    assert resolved.project_dir == resolved.data_dir / "myrepo"
    assert resolved.project_dir != elsewhere / "myrepo"


def test_project_dir_absolute_override_used_as_is(tmp_path: Path, fake_global_root: Path) -> None:
    absolute = tmp_path / "elsewhere"
    project_config = _write_project_config(tmp_path)
    CgConfigData(settings=CgSettingsData(project_dir=str(absolute))).save_yaml(project_config)
    resolved = CgConfig(config_file=project_config, raw_data=CgConfigData.load_yaml(project_config))

    assert resolved.project_dir == absolute


def test_settings_for_the_global_config_itself_is_not_overlaid_on_itself(
            fake_global_root: Path,
        ) -> None:
    """When config_file *is* the global fallback location, there's no separate project tier to
       overlay--this must not re-read/double-apply the same file."""
    global_config = fake_global_root / "config" / CONFIG_FILE_NAME
    global_config.parent.mkdir(parents=True)
    CgConfigData(settings=CgSettingsData(default_profile="global-only")).save_yaml(global_config)

    resolved = CgConfig(config_file=global_config, raw_data=CgConfigData.load_yaml(global_config))

    assert resolved.default_profile == "global-only"


# --- write_config ----------------------------------------------------------------------------


def test_write_config_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "config.yaml"
    write_config(CgConfigData(data_dir="../data"), target)
    assert target.is_file()
    assert CgConfigData.load_yaml(target) == CgConfigData(data_dir="../data")


# --- CgConfig.save() / to_dump_dict() -------------------------------------------------------


def test_cg_config_save_writes_back_raw_data(tmp_path: Path) -> None:
    config_file = _write_project_config(tmp_path)
    resolved = CgConfig(config_file=config_file, raw_data=CgConfigData.load_yaml(config_file))
    resolved.raw_data.data_dir = "../custom"
    resolved.save()
    assert CgConfigData.load_yaml(config_file).data_dir == "../custom"


def test_cg_config_to_dump_dict_has_resolved_values_and_raw_config(tmp_path: Path) -> None:
    config_file = _write_project_config(tmp_path)
    resolved = CgConfig(config_file=config_file, raw_data=CgConfigData.load_yaml(config_file))
    d = resolved.to_dump_dict()
    assert d["configFile"] == str(resolved.config_file)
    assert d["dataDir"] == str(resolved.data_dir)
    assert d["rawConfig"] == resolved.raw_data.to_dict()
    # The raw field is unresolved and unset, unlike the resolved "dataDir" above--omitted
    # entirely (skip_defaults), not present as null.
    assert "dataDir" not in d["rawConfig"]


def test_cg_config_to_dump_dict_nests_resolved_settings(tmp_path: Path, fake_global_root: Path) -> None:
    """defaultProfile/projectDir/projectDir must be nested under "settings" in the dump--
       matching CgConfigData.settings's own nested shape--not flattened onto the config object
       directly (that's where they lived before the config/settings merge redesign)."""
    global_config = fake_global_root / "config" / CONFIG_FILE_NAME
    global_config.parent.mkdir(parents=True)
    CgConfigData(settings=CgSettingsData(default_profile="sammck")).save_yaml(global_config)
    project_config = _write_project_config(tmp_path)
    CgConfigData(settings=CgSettingsData(project_dir="myrepo")).save_yaml(project_config)
    resolved = CgConfig(config_file=project_config, raw_data=CgConfigData.load_yaml(project_config))

    d = resolved.to_dump_dict()

    assert "defaultProfile" not in d
    assert "projectDir" not in d
    assert d["settings"] == {
        "defaultProfile": "sammck",
        "projectDir": str(resolved.data_dir / "myrepo"),
    }


def test_cg_config_data_omits_unset_fields_from_to_dict(tmp_path: Path) -> None:
    """Regression test for a JSONWizardX bug where Meta.skip_defaults was silently ignored."""
    assert CgConfigData().to_dict() == {}
    assert CgConfigData(data_dir="../data").to_dict() == {"dataDir": "../data"}


# --- templatePath --------------------------------------------------------------------------------


def _config_with_template_path(tmp_path: Path, value: object) -> CgConfig:
    config_file = tmp_path / ".cg" / "config" / CONFIG_FILE_NAME
    config_file.parent.mkdir(parents=True, exist_ok=True)
    return CgConfig(config_file=config_file,
                    raw_data=CgConfigData.from_dict({"templatePath": value}))


def test_template_path_is_relative_to_the_config_file_like_data_dir(tmp_path: Path) -> None:
    """One rule for every relative path in config.yaml. From `<project>/.cg/config/config.yaml`,
       reaching the project's own `templates/` therefore takes `../../templates`."""
    config = _config_with_template_path(tmp_path, "../../templates")

    assert config.template_path == [tmp_path / "templates"]


def test_a_bare_name_stays_inside_the_config_directory(tmp_path: Path) -> None:
    """The corollary, and the easy mistake: `templates` is `.cg/config/templates`, not the
       project's."""
    config = _config_with_template_path(tmp_path, "templates")

    assert config.template_path == [tmp_path / ".cg" / "config" / "templates"]


def test_template_path_accepts_a_yaml_list(tmp_path: Path) -> None:
    config = _config_with_template_path(tmp_path, ["../../a", "../../b"])

    assert config.template_path == [tmp_path / "a", tmp_path / "b"]


def test_template_path_accepts_path_separated_entries(tmp_path: Path) -> None:
    config = _config_with_template_path(tmp_path, f"../../a{os.pathsep}../../b")

    assert config.template_path == [tmp_path / "a", tmp_path / "b"]


def test_template_path_keeps_absolute_entries(tmp_path: Path) -> None:
    absolute = tmp_path / "somewhere" / "else"
    config = _config_with_template_path(tmp_path, str(absolute))

    assert config.template_path == [absolute]


def test_template_path_is_empty_when_unset(tmp_path: Path) -> None:
    config_file = tmp_path / ".cg" / "config" / CONFIG_FILE_NAME
    config_file.parent.mkdir(parents=True)

    assert CgConfig(config_file=config_file, raw_data=CgConfigData()).template_path == []


def test_template_path_ignores_project_dir(tmp_path: Path) -> None:
    """Unlike the puzzles/ and contributions/ trees, the template path does not follow projectDir:
       it is a config-file path, resolved like every other one in the file."""
    config_file = tmp_path / ".cg" / "config" / CONFIG_FILE_NAME
    config_file.parent.mkdir(parents=True)
    raw = CgConfigData.from_dict({
            "templatePath": "../../templates",
            "settings": {"projectDir": str(tmp_path / "elsewhere")},
        })

    assert CgConfig(config_file=config_file, raw_data=raw).template_path == [tmp_path / "templates"]
