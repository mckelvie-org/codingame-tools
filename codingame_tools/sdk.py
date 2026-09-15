"""The CodinGame SDK: the Java toolkit for writing a contribution's referee and viewer.

An interactive puzzle is played by a **referee** -- a Java program that reads a solution's move,
advances the game, and writes the next turn's state back. CodinGame runs it server-side, which is
why an interactive puzzle cannot be tested locally through the API alone (see
`codingame_tools.puzzle_manager`). The SDK is how an author writes that referee, and its game
runner is how they play a game locally, viewer and all.

Two separate things live in two separate places, and the split is deliberate:

  - **The toolchain** -- a JDK, Maven, and the SDK's own jars -- is identical for every game, so it
    is installed once per project under `.cg/sdk/` and shared by every contribution beneath it,
    reached through a classpath rather than copied.
  - **A game** -- the referee, the viewer, the assets, the generated config -- belongs to one
    contribution, so it lives in that contribution's `.meta/`, alongside everything else specific
    to it.

This module owns the first. It resolves the toolchain and records what it found; it does not build
or run a game.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .common.dataclass_wizard_x import CatchAll, JSONWizardX

if TYPE_CHECKING:
    from .config import CgConfig

__all__ = [
    "SDK_GROUP_ID",
    "DEFAULT_SDK_VERSION",
    "MINIMUM_JAVA_VERSION",
    "BUNDLED_MAVEN_VERSION",
    "SDK_MANIFEST_FILE_NAME",
    "CgSdkError",
    "CgSdkInstall",
    "SDK_SUBDIR_NAME",
    "sdk_dir",
    "manifest_path",
    "load_manifest",
    "find_java",
    "java_version",
    "find_maven",
    "install_maven",
    "resolve_sdk_classpath",
]

SDK_GROUP_ID = "com.codingame.gameengine"
"""Maven group for every SDK artifact."""

DEFAULT_SDK_VERSION = "4.5.0"
"""SDK version installed unless another is asked for.

   The published documentation still shows 3.4.1; Maven Central is well ahead of it, so the version
   is pinned here rather than read from the docs."""

SDK_ARTIFACTS = ("core", "runner")
"""Artifacts a game needs to compile and to run locally. `core` is the game manager a referee is
   written against; `runner` is the local game runner and its viewer server. The graphics modules
   (`entities` and friends) come in transitively or are added by a game's own pom."""

MINIMUM_JAVA_VERSION = 17
"""The SDK compiles at Java 17, so anything older cannot build a referee."""

BUNDLED_MAVEN_VERSION = "3.9.16"
"""Maven version fetched when the system has none.

   Downloaded from `archive.apache.org`, which keeps every release indefinitely -- the main mirror
   carries only current ones, so a pinned version would start 404ing the moment it aged out."""

MAVEN_ARCHIVE_URL = ("https://archive.apache.org/dist/maven/maven-3/{version}/binaries/"
                     "apache-maven-{version}-bin.tar.gz")

SDK_MANIFEST_FILE_NAME = "sdk.json"


class CgSdkError(Exception):
    """The SDK toolchain could not be resolved, with a message saying what to do about it."""


@dataclass
class CgSdkInstall(JSONWizardX):
    """`.cg/sdk/sdk.json`: what `cg sdk install` resolved, so later commands need no re-detection.

       A record of an install, not a request for one -- every path here was verified to exist when
       it was written. Delete the file (or the whole `.cg/sdk/`) and reinstall if anything moves."""

    extra_data: CatchAll = field(default_factory=dict)

    sdk_version: str | None = None
    """SDK version resolved into the project-local Maven repository."""

    java_home: str | None = None
    """JDK used, as a `JAVA_HOME`-style directory."""

    java_version: str | None = None
    """Full version string reported by that JDK, e.g. "17.0.11"."""

    maven_path: str | None = None
    """The `mvn` executable used -- either the system's or the one under `.cg/sdk/maven/`."""

    maven_bundled: bool = False
    """Whether that Maven was downloaded here rather than found on PATH."""

    classpath: list[str] = field(default_factory=list)
    """Every jar a game needs to compile against, in Maven's own order."""

    installed_at: str | None = None
    """When this was resolved, ISO-8601 UTC."""


SDK_SUBDIR_NAME = "sdk"
"""Name of the SDK directory, a sibling of `.cg/config` and `.cg/data`."""


def sdk_dir(config: CgConfig) -> Path:
    """Where this project's shared SDK toolchain lives: `sdk/` beside the resolved config.

       For the usual project layout that is `<project>/.cg/sdk/`, alongside `.cg/config` and
       `.cg/data` -- derived the same way `CgConfig.data_dir` is, so an explicit `--config` or a
       global config puts the toolchain beside that config rather than somewhere unrelated.

       Shared by every contribution beneath the project and reached through a classpath rather
       than copied; a game's own files belong in its contribution's `.meta/`."""
    return (config.config_dir / ".." / SDK_SUBDIR_NAME).resolve()


def manifest_path(root: Path) -> Path:
    """Path to the install manifest inside an SDK directory."""
    return root / SDK_MANIFEST_FILE_NAME


def load_manifest(root: Path) -> CgSdkInstall | None:
    """The recorded install, or None if there is none or it is unreadable."""
    path = manifest_path(root)
    try:
        return CgSdkInstall.load(path) if path.is_file() else None
    except (OSError, ValueError):
        return None


def java_version(java: Path) -> str | None:
    """The version string a `java` executable reports, or None if it cannot be run.

       `java -version` writes to stderr, and its first line is quoted (`openjdk version
       "17.0.11"`), so the number is pulled out rather than assumed to be a bare field."""
    try:
        result = subprocess.run([str(java), "-version"], capture_output=True, text=True,  # noqa: S603
                                check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r'version "([^"]+)"', (result.stderr or "") + (result.stdout or ""))
    return match.group(1) if match else None


def _major_version(version: str) -> int | None:
    """Java's major version, across both numbering schemes: `1.8.0_x` is 8, `17.0.11` is 17."""
    parts = version.split(".")
    try:
        first = int(parts[0])
    except ValueError:
        return None
    if first == 1 and len(parts) > 1:
        try:
            return int(parts[1])
        except ValueError:
            return None
    return first


def find_java() -> tuple[Path, str] | None:
    """A JDK new enough to build a referee, as `(java_executable, version)`.

       Prefers `JAVA_HOME`, then PATH. Returns None when nothing suitable is found -- including
       when a Java is present but too old, which the caller reports differently from absence."""
    candidates: list[Path] = []
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidates.append(Path(java_home) / "bin" / "java")
    found = shutil.which("java")
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if not candidate.is_file():
            continue
        version = java_version(candidate)
        if version is None:
            continue
        major = _major_version(version)
        if major is not None and major >= MINIMUM_JAVA_VERSION:
            return candidate, version
    return None


def find_maven(root: Path) -> Path | None:
    """A usable `mvn`: the one previously downloaded under `root`, else the system's.

       The bundled one wins so that a project keeps working the same way once installed, whatever
       later appears on PATH."""
    bundled = sorted(root.glob("maven/*/bin/mvn"))
    for candidate in bundled:
        if os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which("mvn")
    return Path(found) if found else None


def _verify_sha512(archive: Path, expected: str) -> None:
    digest = hashlib.sha512(archive.read_bytes()).hexdigest()
    if digest != expected.strip().split()[0].lower():
        raise CgSdkError(
                f"the downloaded Maven archive does not match its published SHA-512 checksum "
                f"(expected {expected[:16]}..., got {digest[:16]}...). Nothing was installed.")


def install_maven(root: Path, version: str = BUNDLED_MAVEN_VERSION) -> Path:
    """Download a portable Maven into `root/maven/` and return its `mvn`.

       Verified against the checksum Apache publishes alongside it: this fetches and then executes
       a binary, so an unverified download would be a straightforward way to run someone else's
       code.

    Raises:
        CgSdkError: if the download or the checksum fails.
    """
    url = MAVEN_ARCHIVE_URL.format(version=version)
    destination = root / "maven"
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        archive = Path(scratch) / f"apache-maven-{version}-bin.tar.gz"
        try:
            urllib.request.urlretrieve(url, archive)  # noqa: S310 -- fixed https Apache URL
            with urllib.request.urlopen(url + ".sha512", timeout=60) as response:  # noqa: S310
                expected = response.read().decode("utf-8", "replace")
        except OSError as e:
            raise CgSdkError(f"could not download Maven {version} from {url}: {e}") from e
        _verify_sha512(archive, expected)
        with tarfile.open(archive) as tar:
            # `filter="data"` refuses absolute paths, `..` and special files -- an archive is
            # untrusted input even from a trusted host.
            tar.extractall(destination, filter="data")
    mvn = destination / f"apache-maven-{version}" / "bin" / "mvn"
    if not mvn.is_file():
        raise CgSdkError(f"Maven {version} unpacked but {mvn} is missing.")
    mvn.chmod(0o755)
    return mvn


_BOOTSTRAP_POM = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.codingame.tools</groupId>
  <artifactId>sdk-bootstrap</artifactId>
  <version>0.0.1</version>
  <packaging>pom</packaging>
  <dependencies>
{dependencies}  </dependencies>
</project>
"""

_DEPENDENCY = """    <dependency>
      <groupId>{group}</groupId>
      <artifactId>{artifact}</artifactId>
      <version>{version}</version>
    </dependency>
"""


def resolve_sdk_classpath(root: Path, mvn: Path, *, version: str = DEFAULT_SDK_VERSION,
                          java_home: Path | None = None, timeout: float = 900.0) -> list[str]:
    """Download the SDK and its dependencies into the project's own Maven repository, and return
       the resulting classpath.

       Resolved through a throwaway pom rather than by fetching jars directly, so Maven works out
       the transitive dependencies -- the graphics modules and their own dependencies -- exactly as
       a game's build will.

       The repository is `root/m2/`, not the user's `~/.m2`: a project-local install must not
       depend on, or quietly alter, a shared one.

    Raises:
        CgSdkError: if Maven fails, carrying its output.
    """
    repository = root / "m2"
    repository.mkdir(parents=True, exist_ok=True)
    dependencies = "".join(
            _DEPENDENCY.format(group=SDK_GROUP_ID, artifact=artifact, version=version)
            for artifact in SDK_ARTIFACTS)
    pom = root / "bootstrap-pom.xml"
    pom.write_text(_BOOTSTRAP_POM.format(dependencies=dependencies), encoding="utf-8")
    output_file = root / "classpath.txt"

    environment = dict(os.environ)
    if java_home is not None:
        environment["JAVA_HOME"] = str(java_home)
    result = subprocess.run(  # noqa: S603
            [str(mvn), "-B", "-q", "-f", str(pom),
             f"-Dmaven.repo.local={repository}",
             "dependency:build-classpath", f"-Dmdep.outputFile={output_file}"],
            capture_output=True, text=True, check=False, timeout=timeout, env=environment)
    if result.returncode != 0:
        raise CgSdkError(
                f"Maven failed to resolve the SDK (exit {result.returncode}).\n"
                f"{(result.stdout or '') + (result.stderr or '')}".strip())
    if not output_file.is_file():
        raise CgSdkError("Maven reported success but wrote no classpath.")
    entries = [e for e in output_file.read_text(encoding="utf-8").strip().split(os.pathsep) if e]
    if not entries:
        raise CgSdkError("Maven resolved an empty classpath.")
    return entries


def utc_now_iso() -> str:
    """Current time as ISO-8601 UTC, for the manifest."""
    return datetime.now(timezone.utc).isoformat()


def is_git_ignored(path: Path) -> bool | None:
    """Whether git ignores `path`. None when git cannot answer -- not a repository, or no git.

       Checked rather than assumed: the SDK install is bulky, machine-specific and reproducible, so
       it must not end up committed, but a project may not have the rule this one does."""
    try:
        result = subprocess.run(  # noqa: S603
                ["git", "check-ignore", "-q", str(path)],  # noqa: S607
                capture_output=True, check=False, timeout=30, cwd=path.parent)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None
