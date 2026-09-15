"""Tests for resolving the CodinGame SDK toolchain.

The install itself downloads a JDK-sized pile of jars from Maven Central, so nothing here does
that. What is tested is the logic around it: where the toolchain goes, which Java counts as new
enough, what the manifest records, and the checks that keep a downloaded binary honest.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from codingame_tools.sdk import (
    BUNDLED_MAVEN_VERSION,
    DEFAULT_SDK_VERSION,
    MINIMUM_JAVA_VERSION,
    CgSdkError,
    CgSdkInstall,
    _major_version,
    _verify_sha512,
    find_maven,
    load_manifest,
    manifest_path,
    sdk_dir,
)


class TestLocation:
    def test_sdk_sits_beside_the_project_config(self, tmp_path: Path) -> None:
        """`.cg/sdk` alongside `.cg/config` and `.cg/data`, so one project-level install is shared
           by every contribution beneath it rather than copied into each."""
        from codingame_tools.config.cg_config import CgConfigData
        from codingame_tools.config.resolver import CgConfig

        config_file = tmp_path / ".cg" / "config" / "config.yaml"
        config_file.parent.mkdir(parents=True)
        config = CgConfig(config_file=config_file, raw_data=CgConfigData())
        assert sdk_dir(config) == (tmp_path / ".cg" / "sdk").resolve()

    def test_manifest_is_inside_the_sdk_directory(self, tmp_path: Path) -> None:
        assert manifest_path(tmp_path).parent == tmp_path


class TestJavaVersions:
    @pytest.mark.parametrize(("version", "major"), [
        ("17.0.11", 17),
        ("21", 21),
        ("24.0.1", 24),
        # Java's old scheme: 1.8.0_402 is Java 8, not Java 1. Reading the first field alone would
        # call every legacy JDK "version 1" and accept it as newer than 17.
        ("1.8.0_402", 8),
        ("1.7.0", 7),
    ])
    def test_major_version(self, version: str, major: int) -> None:
        assert _major_version(version) == major

    def test_a_legacy_jdk_is_correctly_seen_as_too_old(self) -> None:
        legacy = _major_version("1.8.0_402")
        assert legacy is not None and legacy < MINIMUM_JAVA_VERSION

    def test_unparseable_version_is_not_guessed(self) -> None:
        assert _major_version("banana") is None


class TestChecksum:
    def test_a_matching_checksum_passes(self, tmp_path: Path) -> None:
        archive = tmp_path / "a.tar.gz"
        archive.write_bytes(b"contents")
        _verify_sha512(archive, hashlib.sha512(b"contents").hexdigest())

    def test_a_mismatched_checksum_is_refused(self, tmp_path: Path) -> None:
        """This downloads and then executes a binary, so an unverified archive is a way to run
           someone else's code."""
        archive = tmp_path / "a.tar.gz"
        archive.write_bytes(b"tampered")
        with pytest.raises(CgSdkError, match="SHA-512"):
            _verify_sha512(archive, hashlib.sha512(b"contents").hexdigest())

    def test_the_published_format_is_accepted(self, tmp_path: Path) -> None:
        """Apache publishes the digest alone, but mirrors sometimes append the filename and a
           trailing newline."""
        archive = tmp_path / "a.tar.gz"
        archive.write_bytes(b"contents")
        digest = hashlib.sha512(b"contents").hexdigest()
        _verify_sha512(archive, f"{digest}  apache-maven-{BUNDLED_MAVEN_VERSION}-bin.tar.gz\n")


class TestMavenDiscovery:
    def test_a_bundled_maven_wins_over_the_system_one(self, tmp_path: Path) -> None:
        """Once a project has downloaded its own, it keeps building the same way whatever later
           appears on PATH."""
        bundled = tmp_path / "maven" / f"apache-maven-{BUNDLED_MAVEN_VERSION}" / "bin" / "mvn"
        bundled.parent.mkdir(parents=True)
        bundled.write_text("#!/bin/sh\n")
        bundled.chmod(0o755)
        assert find_maven(tmp_path) == bundled

    def test_a_non_executable_bundled_maven_is_ignored(self, tmp_path: Path) -> None:
        """A half-extracted download must not be reported as usable."""
        bundled = tmp_path / "maven" / "apache-maven-x" / "bin" / "mvn"
        bundled.parent.mkdir(parents=True)
        bundled.write_text("")
        bundled.chmod(0o644)
        found = find_maven(tmp_path)
        assert found != bundled


class TestManifest:
    def test_round_trip(self, tmp_path: Path) -> None:
        install = CgSdkInstall(
                sdk_version=DEFAULT_SDK_VERSION, java_home="/jdk", java_version="17.0.11",
                maven_path="/mvn", maven_bundled=True, classpath=["/a.jar", "/b.jar"],
                installed_at="2026-08-18T00:00:00+00:00")
        install.save(manifest_path(tmp_path))
        restored = load_manifest(tmp_path)
        assert restored is not None
        assert restored.sdk_version == DEFAULT_SDK_VERSION
        assert restored.classpath == ["/a.jar", "/b.jar"]
        assert restored.maven_bundled is True

    def test_absent_manifest_is_not_an_error(self, tmp_path: Path) -> None:
        assert load_manifest(tmp_path) is None

    def test_corrupt_manifest_is_treated_as_absent(self, tmp_path: Path) -> None:
        """The install is reproducible, so a damaged record should send you to `cg sdk install`
           rather than raise at whatever command happened to read it."""
        manifest_path(tmp_path).write_text("{not json", encoding="utf-8")
        assert load_manifest(tmp_path) is None
