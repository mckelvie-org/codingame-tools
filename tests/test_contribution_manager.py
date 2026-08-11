"""Unit tests for codingame_tools.contribution_manager.manager.CgContributionManager
   (`import_`/`push`/`fetch`/`rebase`/`merge_discard_local`/`merge_discard_server`/`discard_local`/
   `merge_start`/`merge_continue`/`merge_abort`), against a fake, duck-typed client
   (services.contribution, servlets.file_servlet, servlets.file_upload)--no real
   CgClient/network involved. Real git subprocess calls run against `tmp_path`.

These are pure/local tests--no network--so they run under the default `pdm run test` invocation.
`git` itself is required on PATH (see `requires_git`)--near-universal in dev/CI environments, but
skipped gracefully if genuinely absent, for parity with `requires_diff3` elsewhere in this suite.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from codingame_tools.client.common.protocol.contribution import (
    CgContribution,
    CgContributionData,
    CgContributionModerator,
    CgContributionVersion,
    CgDeleteContributionResult,
    CgTestCase,
)
from codingame_tools.client.common.raw_client import CgDownloadFileResult, CgUploadFileResult, compute_content_hash
from codingame_tools.contribution_manager.manager import (
    CgContributionManager,
    CgContributionManagerError,
    CgContributionSyncStatus,
    CgMergeStartStatus,
    CgRebaseStatus,
)
from codingame_tools.contribution_manager.schema import CgContributionView

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
pytestmark = requires_git

COVER_CONTENT = b"fake-png-bytes"


def _make_test_case(title: str, i: str, o: str, *, is_test: bool, is_validator: bool) -> CgTestCase:
    return CgTestCase(title=title, test_in=i, test_out=o, is_test=is_test, is_validator=is_validator, need_validation=True)


def _make_full_data(
            *, cover_binary_id: int | None = 555, statement: str = "The statement",
            solution_language: str | None = "Python3", solution: str | None = "print('hi')",
        ) -> CgContributionData:
    return CgContributionData(
            title="My Puzzle",
            statement=statement,
            input_description="Input desc",
            output_description="Output desc",
            constraints="1 <= N <= 100",
            difficulty="easy",
            stub_generator="read int N;",
            topics=[],
            test_cases=[
                    _make_test_case("Case A", "1", "2", is_test=True, is_validator=False),
                    _make_test_case("Case A", "3", "4", is_test=False, is_validator=True),
                ],
            solution_language=solution_language,
            solution=solution,
            cover_binary_id=cover_binary_id,
        )


def _make_two_pair_data(**kwargs: Any) -> CgContributionData:
    """`_make_full_data()`, with a second local/validator pair appended--so `tests/` has two
       ordinal directories ("01"/"02") instead of one, letting a test remove just one of them
       and leave a renumbering gap to detect."""
    data = _make_full_data(**kwargs)
    extra = [
            _make_test_case("Case B", "5", "6", is_test=True, is_validator=False),
            _make_test_case("Case B", "7", "8", is_test=False, is_validator=True),
        ]
    return dataclasses.replace(data, test_cases=[*data.test_cases, *extra])


def _make_contribution(
            data: CgContributionData, *,
            public_handle: str = "handle-1",
            version: int = 3,
            draft: bool = True,
            ready_for_moderation: bool = False,
        ) -> CgContribution:
    return CgContribution(
            id=1, active_version=version, score=0, votable_id=2, codingamer_id=7412395,
            views=0, commentable_id=3, title=data.title, status="PENDING", nickname="tester",
            public_handle=public_handle, codingamer_handle="cg-handle",
            last_version=CgContributionVersion(
                    version=version, data=data, statement_html="<p>rendered</p>",
                    draft=draft, ready_for_moderation=ready_for_moderation,
                ),
            avatar=0, comment_count=0, up_votes=0, down_votes=0, editable=True,
            draft=draft, ready_for_moderation=ready_for_moderation, contribution_type="PUZZLE_INOUT",
        )


class _FakeContributionHelper:
    def __init__(self, service: _FakeContributionService) -> None:
        self._service = service

    async def update_contribution(
                self, contribution_id: str, puzzle_type: str, contribution_data: CgContributionData,
                draft: bool, ready_for_moderation: bool, prev_version: int,
                codingamer_id: int | None = None, **kwargs: Any,
            ) -> CgContribution:
        self._service.update_calls.append({
                "contribution_id": contribution_id, "puzzle_type": puzzle_type,
                "contribution_data": contribution_data, "draft": draft,
                "ready_for_moderation": ready_for_moderation, "prev_version": prev_version,
            })
        return self._service.update_result

    async def create_contribution(
                self, puzzle_type: str, contribution_data: CgContributionData,
                draft: bool, ready_for_moderation: bool, codingamer_id: int | None = None,
                **kwargs: Any,
            ) -> str:
        self._service.create_calls.append({
                "puzzle_type": puzzle_type, "contribution_data": contribution_data,
                "draft": draft, "ready_for_moderation": ready_for_moderation,
            })
        return self._service.create_result


class _FakeContributionService:
    def __init__(
                self, find_result: CgContribution, update_result: CgContribution | None = None,
                create_result: str = "new-handle",
            ) -> None:
        self.find_result = find_result
        self.update_result = update_result if update_result is not None else find_result
        self.create_result = create_result
        self.update_calls: list[dict[str, Any]] = []
        self.create_calls: list[dict[str, Any]] = []
        self.delete_calls: list[str] = []
        self.find_call_count = 0
        self.moderator_results: dict[str, list[CgContributionModerator]] = {"validate": [], "deny": []}
        self.find_contribution_moderators_calls: list[tuple[int, str]] = []
        self.helper = _FakeContributionHelper(self)

    async def find_contribution(self, contribution_id: str, arg2: bool = True) -> CgContribution:
        self.find_call_count += 1
        return self.find_result

    async def delete_contribution(self, contribution_id: str, codingamer_id: int | None = None) -> CgDeleteContributionResult:
        self.delete_calls.append(contribution_id)
        return CgDeleteContributionResult(action_id=1, result=True)

    async def find_contribution_moderators(
                self, contribution_numeric_id: int, action: str,
            ) -> list[CgContributionModerator]:
        self.find_contribution_moderators_calls.append((contribution_numeric_id, action))
        return self.moderator_results[action]


class _FakeServices:
    def __init__(self, contribution: _FakeContributionService) -> None:
        self.contribution = contribution


class _FakeFileServlet:
    def __init__(self, result: CgDownloadFileResult) -> None:
        self.result = result
        self.calls: list[int] = []

    async def __call__(
                self, id: int, format: str | None = None, timestamp: object = None, *, require_login: bool = True,
            ) -> CgDownloadFileResult:
        self.calls.append(id)
        return self.result


class _FakeFileUpload:
    def __init__(self, result: CgUploadFileResult) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def __call__(
                self, content: bytes, *, filename: str | None = None,
                content_type: str = "application/octet-stream", params: object = None,
            ) -> CgUploadFileResult:
        self.calls.append({"content": content, "filename": filename, "content_type": content_type})
        return self.result


class _FakeServlets:
    def __init__(self, file_servlet: _FakeFileServlet, file_upload: _FakeFileUpload) -> None:
        self.file_servlet = file_servlet
        self.file_upload = file_upload


class _FakeClient:
    def __init__(self, contribution_service: _FakeContributionService, servlets: _FakeServlets) -> None:
        self.services = _FakeServices(contribution_service)
        self.servlets = servlets


async def _start_conflicting_merge(
            manager: CgContributionManager, service: _FakeContributionService, data: CgContributionData,
        ) -> None:
    """Commit a local edit onto `main`, then advance the fake server with a conflicting edit to
       the same field, and start a merge--leaving it genuinely in progress (unresolved conflict
       markers), unlike a same-content/no-op version bump (which git merges cleanly and
       auto-commits, never leaving anything "in progress")."""
    (manager.data_dir / "statement.cgmd").write_text("Local edit\n")
    manager.git_repo.commit_worktree("local edit")
    new_data = _make_full_data(statement="Server edit")
    service.find_result = _make_contribution(new_data, version=4)
    result = await manager.merge_start()
    assert manager.merge_in_progress, f"test setup didn't actually produce an in-progress merge: {result}"


def _make_fake_client(
            find_result: CgContribution,
            *,
            update_result: CgContribution | None = None,
            new_upload_id: int = 999,
            cover_content: bytes = COVER_CONTENT,
        ) -> tuple[_FakeClient, _FakeContributionService, _FakeFileUpload, _FakeFileServlet]:
    contribution_service = _FakeContributionService(find_result, update_result)
    file_servlet = _FakeFileServlet(
            CgDownloadFileResult.create(id=555, content=cover_content, content_type="image/png", filename="cover.png"))
    file_upload = _FakeFileUpload(CgUploadFileResult(id=new_upload_id, name="cover.png", size=len(cover_content), field_name="file"))
    servlets = _FakeServlets(file_servlet, file_upload)
    client = _FakeClient(contribution_service, servlets)
    return client, contribution_service, file_upload, file_servlet


# --- import_ -----------------------------------------------------------------------------


async def test_import_writes_identity_view_content_files_and_git_repo(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, _, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]

    view = await manager.import_("handle-1")

    assert (tmp_path / "data" / "statement.cgmd").read_text() == "The statement\n"
    assert (tmp_path / "data" / "input_description.cgmd").read_text() == "Input desc\n"
    assert (tmp_path / "data" / "output_description.cgmd").read_text() == "Output desc\n"
    assert (tmp_path / "data" / "constraints.cgmd").read_text() == "1 <= N <= 100\n"
    assert (tmp_path / "data" / "stub_generator.cgstub").read_text() == "read int N;\n"
    assert (tmp_path / "data" / "cover.png").read_bytes() == COVER_CONTENT
    assert (tmp_path / "data" / "solution.py").read_text() == "print('hi')\n"
    assert not any(tmp_path.glob("solution.*"))  # nothing at the root; the file lives in data/
    assert (tmp_path / "data" / "tests" / "01").is_dir()

    assert view.puzzle_type == "PUZZLE_INOUT"
    assert view.draft is True
    assert view.ready_for_moderation is False
    assert view.data.statement is None  # always-empty by convention
    assert view.data.title == "My Puzzle"

    identity = manager.load_identity()
    assert identity is not None
    assert identity.contribution_handle == "handle-1"

    assert manager.git_dir.is_dir()
    repo = manager.git_repo
    assert repo.resolve_ref("main") == repo.resolve_ref("server")
    assert repo.merge_base("main", "server") == repo.resolve_ref("main")

    metadata = manager.server_metadata()
    assert metadata is not None
    assert metadata.contribution_id == "handle-1"
    assert metadata.version == 3
    assert metadata.cover_binary_id == 555
    assert metadata.cover_binary_hash == compute_content_hash(COVER_CONTENT)

    assert manager.contribution_data_file.is_file()
    assert CgContributionView.load(manager.contribution_data_file) == view


async def test_import_writes_gitignore_for_meta(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, _, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]

    await manager.import_("handle-1")

    # Not inside an existing outer git repo (tmp_path is bare) -> embedded layout, git-dir at
    # data/.git. .meta/ is at the working directory root either way, so the .gitignore protecting it
    # from a future outer project is written there, and data/ stays free of anything generated.
    meta = manager.load_meta()
    assert meta is not None
    assert meta.git_repo == "data/.git"
    assert manager.git_dir == tmp_path / "data" / ".git"
    assert manager.meta_dir == tmp_path / ".meta"
    assert (tmp_path / ".gitignore").read_text() == ".meta/\n"
    assert not (tmp_path / "data" / ".gitignore").exists()


async def test_meta_is_never_inside_data_in_either_git_dir_layout(tmp_path: Path) -> None:
    """`data/` holds user state and only user state: it is the git work tree, it is what gets
       pushed to CodinGame, and it is the only part worth backing up. `git_dir_in_data` moves the
       git-dir and nothing else--an earlier version dragged `.meta/` along with it, which put
       generated, disposable state inside the one directory that must not have any."""
    outer = tmp_path / "outer"
    outer.mkdir()
    subprocess.run(["git", "init", "-q", str(outer)], check=True, capture_output=True)

    for root, expected_in_data in ((tmp_path / "standalone", True), (outer / "inside", False)):
        client, _, _, _ = _make_fake_client(_make_contribution(_make_full_data()))
        manager = CgContributionManager(root, client)  # type: ignore[arg-type]
        await manager.import_("handle-1")

        meta = manager.load_meta()
        assert meta is not None
        assert meta.git_repo == ("data/.git" if expected_in_data else ".meta/.contribution-git"), root

        assert manager.meta_dir == root / ".meta", root
        assert manager.status_cache_file.parent == root / ".meta", root
        assert not (root / "data" / ".meta").exists(), root
        assert (root / ".gitignore").read_text() == ".meta/\n"

        expected_git_dir = root / "data" / ".git" if expected_in_data else root / ".meta" / ".contribution-git"
        assert manager.git_dir == expected_git_dir, root
        assert manager.git_dir.is_dir(), root

        # Nothing generated is committed either. The embedded layout used to need a synthetic
        # .gitignore in every tree on `server`, to keep `git clean -fd` from deleting the git-dir
        # out of data/.meta/; git excludes its own data/.git inherently, so that is gone.
        tracked = subprocess.run(
                ["git", f"--git-dir={manager.git_dir}", "ls-tree", "--name-only", "-r", "server"],
                check=True, capture_output=True, text=True).stdout.splitlines()
        assert ".gitignore" not in tracked, root
        assert not any(name.startswith(".meta/") for name in tracked), root


async def test_losing_meta_does_not_orphan_an_embedded_repository(tmp_path: Path) -> None:
    """`.meta/` is disposable by design--deleting it and repairing is documented as always valid--so
       the recorded git-dir location must never be the only copy. A standalone contribution's
       `data/.git` outlives `.meta/`, and finding it on disk is what stops `repair()` from
       initializing a second, empty repository beside it and abandoning the real history."""
    client, _, _, _ = _make_fake_client(_make_contribution(_make_full_data()))
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    original_head = manager.git_repo.resolve_ref("main")

    shutil.rmtree(manager.meta_dir)
    assert manager.load_meta() is None

    assert manager.git_dir == tmp_path / "data" / ".git"
    assert manager.git_repo.resolve_ref("main") == original_head


async def test_losing_meta_is_survivable_even_once_an_outer_git_project_appears(tmp_path: Path) -> None:
    """The hazard that made this fact worth recording at all: deriving the location from "is there
       an outer repo?" gives a *different* answer than it did at creation time once someone runs
       `git init` in a parent directory. Finding the existing repository beats re-deriving."""
    client, _, _, _ = _make_fake_client(_make_contribution(_make_full_data()))
    manager = CgContributionManager(tmp_path / "wd", client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    assert manager.git_dir == tmp_path / "wd" / "data" / ".git"

    shutil.rmtree(manager.meta_dir)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)

    # Re-derivation would now say "inside a git project" -> .meta/.contribution-git, which doesn't
    # exist, so repair() would build a fresh empty repo there. Instead it finds the real one and
    # refuses, which is the whole point.
    assert manager.git_dir == tmp_path / "wd" / "data" / ".git"
    with pytest.raises(CgContributionManagerError, match="already been imported"):
        await manager.repair()


async def test_repair_rewrites_the_meta_record_it_was_run_to_replace(tmp_path: Path) -> None:
    """Repair mode exists precisely because `.meta/` went missing, so it has to put the record
       back--otherwise every later command pays for the on-disk probe."""
    client, _, _, _ = _make_fake_client(_make_contribution(_make_full_data()))
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    shutil.rmtree(manager.meta_dir)
    shutil.rmtree(tmp_path / "data" / ".git")
    await manager.import_("handle-1")  # repair mode: identity + data/ present, git-dir gone

    meta = manager.load_meta()
    assert meta is not None
    assert meta.git_repo == "data/.git"


async def test_a_stale_git_dir_in_data_is_dropped_from_the_identity_file(tmp_path: Path) -> None:
    """Through 1.0.x the location lived in `contribution.json`. `CatchAll` would round-trip the key
       forever, leaving the identity manifest implying it still owns a fact it no longer does."""
    client, _, _, _ = _make_fake_client(_make_contribution(_make_full_data()))
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    raw = json.loads(manager.identity_file.read_text())
    raw["gitDirInData"] = True
    manager.identity_file.write_text(json.dumps(raw))
    shutil.rmtree(manager.meta_dir)
    shutil.rmtree(tmp_path / "data" / ".git")

    await manager.import_("handle-1")

    assert "gitDirInData" not in json.loads(manager.identity_file.read_text())


async def test_two_repositories_with_no_record_refuses_rather_than_guessing(tmp_path: Path) -> None:
    """Picking one would silently abandon the other's history, which is the exact failure this
       whole resolution order exists to avoid."""
    client, _, _, _ = _make_fake_client(_make_contribution(_make_full_data()))
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    (tmp_path / ".meta" / ".contribution-git").mkdir(parents=True, exist_ok=True)
    (manager.meta_file).unlink()

    with pytest.raises(CgContributionManagerError, match="Refusing to guess"):
        _ = manager.git_dir


async def test_contribution_json_plus_data_is_a_complete_portable_export(tmp_path: Path) -> None:
    """The portability contract: `contribution.json` + `data/` are the exportable state. Copy those
       two anywhere, repair, and you have an equivalent working directory.

       The destination here is *inside a git project* while the source was standalone, so the two
       copies legitimately end up with different git-dir layouts. That is precisely why the layout
       may not live in `contribution.json`--it would travel with the export and be wrong on arrival,
       putting an embedded `.git` inside someone else's project."""
    source = tmp_path / "source"
    client, _, _, _ = _make_fake_client(_make_contribution(_make_full_data()))
    source_manager = CgContributionManager(source, client)  # type: ignore[arg-type]
    await source_manager.import_("handle-1")
    assert source_manager.git_dir == source / "data" / ".git"

    # Export: the identity file and data/, and nothing else. data/.git is excluded the same way
    # syncing through an outer git repo would exclude it--git does not track a nested .git.
    outer = tmp_path / "outer"
    outer.mkdir()
    subprocess.run(["git", "init", "-q", str(outer)], check=True, capture_output=True)
    destination = outer / "imported"
    destination.mkdir()
    shutil.copy2(source_manager.identity_file, destination / "contribution.json")
    shutil.copytree(source / "data", destination / "data", ignore=shutil.ignore_patterns(".git"))

    client2, _, _, _ = _make_fake_client(_make_contribution(_make_full_data()))
    destination_manager = CgContributionManager(destination, client2)  # type: ignore[arg-type]
    await destination_manager.repair()

    # Same contribution, same content--different local plumbing.
    assert destination_manager.git_dir == destination / ".meta" / ".contribution-git"
    assert not (destination / "data" / ".git").exists()
    assert destination_manager.load_identity() == source_manager.load_identity()
    for name in ("statement.cgmd", "solution.py", "contribution-data.json"):
        assert (destination / "data" / name).read_bytes() == (source / "data" / name).read_bytes(), name

    status = await destination_manager.status()
    assert status.sync_status is CgContributionSyncStatus.UP_TO_DATE


async def test_the_pre_1_1_meta_in_data_layout_is_rejected_rather_than_silently_reinitialized(
            tmp_path: Path,
        ) -> None:
    """Versions before 1.1 put the git-dir at `data/.meta/.contribution-git/`. Today's path for
       that layout is `data/.git`, which simply doesn't exist there--so without this guard
       `repair()` would happily init a second, empty repo and abandon the local history in the
       first one."""
    client, _, _, _ = _make_fake_client(_make_contribution(_make_full_data()))
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    legacy = tmp_path / "data" / ".meta" / ".contribution-git"
    shutil.move(str(manager.git_dir), str(legacy))

    with pytest.raises(CgContributionManagerError, match="before codingame-tools 1.1"):
        _ = manager.git_dir
    with pytest.raises(CgContributionManagerError, match="before codingame-tools 1.1"):
        await manager.repair()
    with pytest.raises(CgContributionManagerError, match="before codingame-tools 1.1"):
        await manager.import_("handle-1")


async def test_import_with_no_cover_image_leaves_cover_hash_none(tmp_path: Path) -> None:
    data = _make_full_data(cover_binary_id=None)
    contribution = _make_contribution(data)
    client, _, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]

    await manager.import_("handle-1")

    assert not (tmp_path / "data" / "cover.png").exists()
    metadata = manager.server_metadata()
    assert metadata is not None
    assert metadata.cover_binary_hash is None


async def test_import_with_unmapped_language_uses_the_fallback_extension(tmp_path: Path) -> None:
    data = _make_full_data(solution_language="SomeUnknownLanguage")
    contribution = _make_contribution(data)
    client, _, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]

    await manager.import_("handle-1")

    # No extension cg recognizes, so the file keeps the neutral fallback name.
    assert (tmp_path / "data" / "solution.src").read_text() == "print('hi')\n"
    assert list(tmp_path.glob("solution.*")) == []  # nothing at the root


async def test_import_refuses_to_retarget_an_existing_directory(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, _, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    other_contribution = _make_contribution(data, public_handle="handle-2")
    client2, _, _, _ = _make_fake_client(other_contribution)
    manager2 = CgContributionManager(tmp_path, client2)  # type: ignore[arg-type]
    with pytest.raises(CgContributionManagerError):
        await manager2.import_("handle-2")


async def test_import_refuses_if_git_repo_already_exists(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, _, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    with pytest.raises(CgContributionManagerError):
        await manager.import_("handle-1")


async def test_import_repairs_when_git_dir_missing_but_content_present(tmp_path: Path) -> None:
    """Simulates cloning an outer project that tracks contribution.json/data/ but not the git-dir
       itself (deliberately outer-gitignored)--see manager.py's import_() docstring."""
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, _, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    (tmp_path / "data" / "statement.cgmd").write_text("Local edit surviving the clone\n")

    shutil.rmtree(manager.git_dir)  # remove the git-dir, leaving data/'s content
    assert not manager.git_dir.exists()

    view = await manager.import_("handle-1")

    assert (tmp_path / "data" / "statement.cgmd").read_text() == "Local edit surviving the clone\n"
    assert view.data.title == "My Puzzle"
    repo = manager.git_repo
    assert repo.resolve_ref("main") == repo.resolve_ref("server")


async def test_import_repair_mode_renormalizes_non_canonical_test_case_dirs(tmp_path: Path) -> None:
    """Repair mode snapshots whatever's already on disk (preserved from the outer clone)
       verbatim--if that layout isn't already canonical, this commit would permanently encode it,
       causing the same spurious-diff risk as an un-renormalized push()--see manager.import_()'s
       docstring."""
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, _, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    (manager.tests_dir / "01").rename(manager.tests_dir / "05")  # simulate a non-canonical layout

    shutil.rmtree(manager.git_dir)
    await manager.import_("handle-1")

    assert not (manager.tests_dir / "05").exists()
    assert (manager.tests_dir / "01").is_dir()


async def test_reimport_with_language_change_regenerates_symlink(tmp_path: Path) -> None:
    data = _make_full_data(solution_language="Python3")
    contribution = _make_contribution(data)
    client, _, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    assert not any(tmp_path.glob("solution.*"))  # nothing at the root; the file lives in data/

    shutil.rmtree(manager.git_dir)  # force repair mode (fresh init_repo, per above)
    new_data = _make_full_data(solution_language="Java", solution="class Main {}")
    contribution2 = _make_contribution(new_data, version=4)
    client2, _, _, _ = _make_fake_client(contribution2)
    manager2 = CgContributionManager(tmp_path, client2)  # type: ignore[arg-type]

    await manager2.import_("handle-1")

    # Repair mode preserves data/'s on-disk content (the OLD Python3 solution.src)--this isn't a
    # live re-fetch overwrite, so the symlink still reflects what was already there.
    assert (tmp_path / "data" / "solution.py").read_text() == "print('hi')\n"
    assert not any(tmp_path.glob("solution.*"))  # nothing at the root; the file lives in data/


# --- repair ----------------------------------------------------------------------------------


async def test_repair_with_handle_delegates_to_import(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, _, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    (tmp_path / "data" / "statement.cgmd").write_text("Local edit surviving the clone\n")

    shutil.rmtree(manager.git_dir)
    assert not manager.git_dir.exists()

    view = await manager.repair()

    assert (tmp_path / "data" / "statement.cgmd").read_text() == "Local edit surviving the clone\n"
    assert view.data.title == "My Puzzle"
    repo = manager.git_repo
    assert repo.resolve_ref("main") == repo.resolve_ref("server")
    identity = manager.load_identity()
    assert identity is not None
    assert identity.contribution_handle == "handle-1"


async def test_repair_without_handle_reconstructs_purely_local(tmp_path: Path) -> None:
    """create()d, edited, but never pushed--repair() must reconstruct main from data/'s current
       on-disk (edited) content, without any network access at all, and without establishing a
       server branch (there's no server-side contribution yet to base one on)."""
    client = object()
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.create(title="My Puzzle")
    (tmp_path / "data" / "statement.cgmd").write_text("Edited before ever pushing\n")

    shutil.rmtree(manager.git_dir)
    assert not manager.git_dir.exists()

    view = await manager.repair()  # would raise if it ever touched client (a plain object())

    assert (tmp_path / "data" / "statement.cgmd").read_text() == "Edited before ever pushing\n"
    assert view.data.title == "My Puzzle"
    identity = manager.load_identity()
    assert identity is not None
    assert identity.contribution_handle is None  # still never pushed
    repo = manager.git_repo
    assert repo.resolve_ref("main") is not None
    assert repo.resolve_ref("server") is None


async def test_repair_requires_prior_create_or_import(tmp_path: Path) -> None:
    manager = CgContributionManager(tmp_path, object())  # type: ignore[arg-type]
    with pytest.raises(FileNotFoundError):
        await manager.repair()


async def test_repair_refuses_if_git_dir_already_exists(tmp_path: Path) -> None:
    client = object()
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.create(title="My Puzzle")

    with pytest.raises(CgContributionManagerError):
        await manager.repair()


async def test_repair_without_handle_requires_data_dir(tmp_path: Path) -> None:
    client = object()
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.create(title="My Puzzle")

    shutil.rmtree(manager.data_dir)  # git_dir_in_data here, so this also removes the git-dir

    with pytest.raises(FileNotFoundError):
        await manager.repair()


# --- create --------------------------------------------------------------------------------


async def test_create_is_purely_local_with_no_contribution_handle_yet(tmp_path: Path) -> None:
    """create() never touches the network--no server-side contribution exists until the first
       push()--see manager.push()'s docstring for the create-vs-update duality this sets up."""
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, file_servlet, file_upload = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]

    view = await manager.create(title="My Puzzle")

    assert service.create_calls == []
    assert service.find_call_count == 0
    assert file_servlet.calls == []
    assert file_upload.calls == []

    assert view.data.title == "My Puzzle"
    # seeded placeholder content--confirmed live that a title-only payload gets refused by the
    # server on push(), so create() must leave something minimally valid on disk.
    assert (tmp_path / "data" / "statement.cgmd").read_text()
    assert view.puzzle_type == "PUZZLE_INOUT"
    identity = manager.load_identity()
    assert identity is not None
    assert identity.contribution_handle is None

    repo = manager.git_repo
    assert repo.resolve_ref("main") is not None  # a real local commit exists
    assert repo.resolve_ref("server") is None  # but nothing server-side yet
    assert manager.server_metadata() is None


async def test_create_accepts_custom_puzzle_type(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, _, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]

    view = await manager.create(title="My Puzzle", puzzle_type="PUZZLE_OPTI")

    assert view.puzzle_type == "PUZZLE_OPTI"


async def test_create_default_language_is_python_with_a_working_stub(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, _, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]

    view = await manager.create(title="My Puzzle")

    assert view.data.solution_language == "Python3"
    # The stub's own value ends in a newline; the file additionally carries its terminator.
    assert (tmp_path / "data" / "solution.py").read_text() == "n = input()\nprint(n)\n\n"
    assert not any(tmp_path.glob("solution.*"))  # nothing at the root; the file lives in data/


async def test_create_non_python_language_leaves_an_empty_source_file(tmp_path: Path) -> None:
    """Only Python3 has a stub that genuinely passes the seeded test cases, so every other language
       gets an *empty* solution.src rather than a placeholder. Empty is this client's spelling of a
       null solutionSource, which `updateContribution` accepts without running solution validation;
       a placeholder would be non-null, fail validation, and block the push."""
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, _, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]

    view = await manager.create(title="My Puzzle", language="Java")

    assert view.data.solution_language == "Java"
    # The file carries Java's own extension, and is a real file rather than a link to one.
    solution_file = tmp_path / "data" / "solution.java"
    assert solution_file.is_file()
    assert not solution_file.is_symlink()
    assert solution_file.read_text().strip() == ""
    assert not any(tmp_path.glob("solution.*"))  # nothing at the root; the file lives in data/


async def test_create_unmapped_language_uses_the_fallback_extension(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, _, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]

    await manager.create(title="My Puzzle", language="SomeUnknownLanguage")

    # No known extension, so the fallback name is used--but the file still exists (empty) to
    # type into.
    assert (tmp_path / "data" / "solution.src").read_text().strip() == ""
    assert list(tmp_path.glob("solution.*")) == []


async def test_push_after_create_creates_a_minimal_stub_then_updates_with_real_content(tmp_path: Path) -> None:
    """The default (direct_create=False) first-push behavior: a minimal, throwaway,
       in-memory-only stub is createContribution'd first (never the real, possibly large, local
       content--see push()'s docstring for why), then the real content goes through a normal
       updateContribution, version 1 -> 2."""
    data = _make_full_data()
    stub_find_result = _make_contribution(data, public_handle="created-handle-1", version=1)
    client, service, _, _ = _make_fake_client(stub_find_result)
    service.create_result = "created-handle-1"
    service.update_result = _make_contribution(data, public_handle="created-handle-1", version=2)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.create(title="My Puzzle")

    result = await manager.push()

    # step 1: the minimal stub, not the real (locally seeded) content.
    assert len(service.create_calls) == 1
    stub_call = service.create_calls[0]
    assert stub_call["puzzle_type"] == "PUZZLE_INOUT"
    assert stub_call["contribution_data"].title == "My Puzzle"
    assert stub_call["contribution_data"].solution_language is None  # the real content has a Python stub; this doesn't
    # always a private draft, never for moderation--not caller-configurable, regardless of
    # contribution-data.json's own draft/ready_for_moderation flags (those apply to the real
    # content in step 2 below, not this throwaway stub).
    assert stub_call["draft"] is True
    assert stub_call["ready_for_moderation"] is False

    # step 2: the real (create()-seeded) content, via a normal update--version 1 -> 2.
    assert len(service.update_calls) == 1
    update_call = service.update_calls[0]
    assert update_call["contribution_id"] == "created-handle-1"
    assert update_call["prev_version"] == 1
    assert update_call["contribution_data"].solution_language == "Python3"
    assert update_call["contribution_data"].solution == "n = input()\nprint(n)\n"

    assert result.public_handle == "created-handle-1"
    identity = manager.load_identity()
    assert identity is not None
    assert identity.contribution_handle == "created-handle-1"
    repo = manager.git_repo
    assert repo.resolve_ref("main") == repo.resolve_ref("server")
    metadata = manager.server_metadata()
    assert metadata is not None
    assert metadata.version == 2  # not 1--the stub was v1, the real content is v2
    assert metadata.contribution_id == "created-handle-1"


async def test_push_direct_create_skips_the_stub_and_creates_with_real_content(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data, public_handle="created-handle-1", version=1)
    client, service, _, _ = _make_fake_client(contribution)
    service.create_result = "created-handle-1"
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.create(title="My Puzzle")

    result = await manager.push(direct_create=True)

    assert len(service.create_calls) == 1
    call = service.create_calls[0]
    assert call["contribution_data"].solution_language == "Python3"  # the real content, not a stub
    assert call["draft"] is True
    assert call["ready_for_moderation"] is False
    assert service.update_calls == []  # a single createContribution call, no follow-up update

    assert result.public_handle == "created-handle-1"
    metadata = manager.server_metadata()
    assert metadata is not None
    assert metadata.version == 1


async def test_push_after_create_uses_update_contribution_on_second_push(tmp_path: Path) -> None:
    data = _make_full_data()
    stub_find_result = _make_contribution(data, public_handle="created-handle-1", version=1)
    client, service, _, _ = _make_fake_client(stub_find_result)
    service.create_result = "created-handle-1"
    service.update_result = _make_contribution(data, public_handle="created-handle-1", version=2)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.create(title="My Puzzle")
    await manager.push()  # first push: stub (v1) + real-content update (v2)

    (tmp_path / "data" / "statement.cgmd").write_text("A real statement now\n")
    real_v3 = _make_contribution(data, public_handle="created-handle-1", version=3)
    service.update_result = real_v3
    service.find_result = real_v3

    result = await manager.push()

    assert len(service.create_calls) == 1  # unchanged--still just the one, from the first push's stub step
    assert len(service.update_calls) == 2  # the first push's real-content update, plus this one
    assert service.update_calls[0]["prev_version"] == 1  # updating from the stub's version
    assert service.update_calls[1]["contribution_id"] == "created-handle-1"
    assert service.update_calls[1]["prev_version"] == 2  # updating from the first push's real content
    assert result.last_version.version == 3
    metadata = manager.server_metadata()
    assert metadata is not None
    assert metadata.version == 3


async def test_push_refuses_instead_of_recreating_when_handle_set_but_git_dir_missing(tmp_path: Path) -> None:
    """contribution.json's contribution_handle, not the server git branch's mere existence, is
       what push() trusts for its create-vs-update decision--otherwise a working directory that
       needs repair (git-dir missing, e.g. from an outer project clone that deliberately
       didn't bring it along) or one whose git-dir was corrupted/tampered with would silently look
       like a brand new contribution to push(), calling createContribution *again* for a
       contribution that already exists--a real duplicate, not a recoverable mistake."""
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    shutil.rmtree(manager.git_dir)  # simulate a missing/corrupted git-dir
    assert not manager.git_dir.exists()
    identity = manager.load_identity()
    assert identity is not None
    assert identity.contribution_handle == "handle-1"  # contribution.json itself is untouched

    with pytest.raises(CgContributionManagerError):
        await manager.push()

    assert service.create_calls == []  # refused before ever calling createContribution
    assert service.update_calls == []


async def test_delete_uses_contribution_handle_even_if_git_dir_missing(tmp_path: Path) -> None:
    """delete() must not rely on being able to read git trailers to find the contribution to
       delete--contribution.json's contribution_handle is enough on its own, and this needs to
       keep working even against a working directory that's missing its git-dir entirely (e.g.
       needs repair), so a real server-side contribution never accidentally survives
       (orphaned) just because the local git state happens to be stale."""
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    shutil.rmtree(manager.git_dir)  # simulate a missing/corrupted git-dir

    await manager.delete()

    assert service.delete_calls == ["handle-1"]
    assert not tmp_path.exists()


async def test_create_refuses_if_directory_already_tracks_a_contribution(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    with pytest.raises(CgContributionManagerError):
        await manager.create(title="Another Puzzle")

    assert service.create_calls == []  # refused before ever calling createContribution


# --- push ------------------------------------------------------------------------------


async def test_push_requires_puzzle_type(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, _, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    view = await manager.import_("handle-1")
    manager.save(dataclasses.replace(view, puzzle_type=None))

    with pytest.raises(CgContributionManagerError):
        await manager.push()


async def test_push_requires_a_prior_import(tmp_path: Path) -> None:
    view = CgContributionView(puzzle_type="PUZZLE_INOUT", data=CgContributionData(title="x"))
    manager = CgContributionManager(tmp_path, object())  # type: ignore[arg-type]
    manager.save(view)
    with pytest.raises(FileNotFoundError):
        await manager.push()


async def test_push_reuses_cover_binary_id_when_content_unchanged(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    updated = _make_contribution(data, version=4)
    client, service, file_upload, _ = _make_fake_client(contribution, update_result=updated)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    result = await manager.push(force=True)

    assert result.last_version.version == 4
    assert len(service.update_calls) == 1
    assert service.update_calls[0]["contribution_data"].cover_binary_id == 555
    assert file_upload.calls == []  # not re-uploaded--content hash matched

    metadata = manager.server_metadata()
    assert metadata is not None
    assert metadata.version == 4
    assert metadata.cover_binary_hash == compute_content_hash(COVER_CONTENT)
    repo = manager.git_repo
    assert repo.resolve_ref("main") == repo.resolve_ref("server")


async def test_push_reuploads_cover_when_content_changed(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    updated = _make_contribution(data, version=4)
    client, service, file_upload, _ = _make_fake_client(contribution, update_result=updated, new_upload_id=777)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    (tmp_path / "data" / "cover.png").write_bytes(b"changed-bytes")

    await manager.push()

    assert len(file_upload.calls) == 1
    assert service.update_calls[0]["contribution_data"].cover_binary_id == 777


async def test_push_reflects_edited_sidecar_files(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    updated = _make_contribution(data, version=4)
    client, service, _, _ = _make_fake_client(contribution, update_result=updated)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    (tmp_path / "data" / "statement.cgmd").write_text("Edited statement\n")

    await manager.push()

    submitted = service.update_calls[0]["contribution_data"]
    # The file's terminator is this client's, not part of the value--see common.text_files.
    assert submitted.statement == "Edited statement"
    assert [tc.title for tc in submitted.test_cases] == ["Case A", "Case A"]
    # Untouched, so it goes back exactly as it arrived--it used to gain a newline here.
    assert submitted.solution == "print('hi')"


async def test_untouched_import_then_push_is_the_identity(tmp_path: Path) -> None:
    """The regression the `common.text_files` conversion exists for: an import followed by a push with
       no edits must submit byte-identical text, including for values that genuinely end in a
       newline.

       Those used to lose one newline per cycle, silently. Measured against the pending
       community-review queue, the trailing-newline habit is per-*author*, so this hit every test
       case of roughly 1 in 12 contributions rather than the occasional stray one. Cycling twice is
       deliberate: a single round trip looked correct under the old scheme too, and only the second
       exposed the erosion."""
    data = CgContributionData(
            title="My Puzzle",
            statement="Statement ending in a newline\n",
            input_description="No newline here",
            output_description="Output desc\n",
            constraints="1 <= N <= 100",
            difficulty="easy",
            stub_generator="read int N;\n",
            topics=[],
            test_cases=[
                    _make_test_case("Case A", "1\n", "2\n", is_test=True, is_validator=False),
                    _make_test_case("Case A", "3", "4", is_test=False, is_validator=True),
                ],
            solution_language="Python3",
            solution="print('hi')\n",
            cover_binary_id=None,
        )

    for cycle in range(2):
        root = tmp_path / f"cycle{cycle}"
        contribution = _make_contribution(data)
        client, service, _, _ = _make_fake_client(
                contribution, update_result=_make_contribution(data, version=4))
        manager = CgContributionManager(root, client)  # type: ignore[arg-type]
        await manager.import_("handle-1")

        await manager.push(force=True)  # no edits at all

        submitted = service.update_calls[0]["contribution_data"]
        assert submitted.statement == data.statement
        assert submitted.input_description == data.input_description
        assert submitted.output_description == data.output_description
        assert submitted.constraints == data.constraints
        assert submitted.stub_generator == data.stub_generator
        assert submitted.solution == data.solution
        assert [(tc.test_in, tc.test_out) for tc in submitted.test_cases] \
            == [(tc.test_in, tc.test_out) for tc in data.test_cases]


async def test_create_seeds_every_editable_file(tmp_path: Path) -> None:
    """`create()` leaves a working directory in which every file an author edits already exists.

       Otherwise the author has to know which filenames to conjure -- they can't be listed, opened
       or diffed until they've been guessed correctly."""
    data = _make_full_data()
    client, _, _, _ = _make_fake_client(_make_contribution(data))
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]

    await manager.create(title="My Puzzle")

    for name in ("statement.cgmd", "input_description.cgmd", "output_description.cgmd",
                 "constraints.cgmd", "stub_generator.cgstub", "solution.py", "cover.png"):
        assert (tmp_path / "data" / name).is_file(), f"create() did not seed data/{name}"


async def test_create_seeds_a_1920x1080_cover_placeholder(tmp_path: Path) -> None:
    """The cover is shipped as package data, so this also proves the asset survives packaging --
       a wheel missing it would fail here rather than at a user's first `create`.

       1920x1080 measured from a real published contribution's cover. PNG dimensions live at a fixed
       offset in the IHDR chunk, so no imaging library is needed to check them (and none is a
       runtime dependency -- see scripts/gen_cover_placeholder.py)."""
    import struct

    data = _make_full_data()
    client, _, _, _ = _make_fake_client(_make_contribution(data))
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]

    await manager.create(title="My Puzzle")

    cover = (tmp_path / "data" / "cover.png").read_bytes()
    assert cover[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    assert struct.unpack(">II", cover[16:24]) == (1920, 1080)


async def test_the_seeded_scaffold_is_self_consistent(tmp_path: Path) -> None:
    """The seeded stub generator must describe the seeded test cases.

       It's the one seeded file that isn't inert: CodinGame runs it to generate the starter code
       every solver begins from, so a stub generator that disagrees with the test data hands them a
       program that reads the wrong thing. Nothing else checks that these two agree, and the
       disagreement would surface much later, as somebody else's confusion."""
    from codingame_tools.contribution_manager.manager import (
        STARTER_STUB_GENERATOR,
        _starter_contribution_data,
    )

    seeded = _starter_contribution_data("My Puzzle")

    # One line holding one integer, which is exactly what `read n:int` consumes.
    for test_case in seeded.test_cases:
        assert test_case.test_in.splitlines() == ["1"], test_case.title
    assert "read n:int" in STARTER_STUB_GENERATOR
    assert "loop" not in STARTER_STUB_GENERATOR, "a loop would need more input lines than are seeded"

    # And every editable field carries a placeholder, not None (which would omit the file).
    for field_name in ("statement", "input_description", "output_description", "constraints",
                       "stub_generator"):
        assert getattr(seeded, field_name), f"{field_name} not seeded"


async def test_push_passes_view_puzzle_type_and_flags(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data, draft=False, ready_for_moderation=True)
    updated = _make_contribution(data, version=4)
    client, service, _, _ = _make_fake_client(contribution, update_result=updated)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    view = await manager.import_("handle-1")
    assert view.draft is False
    assert view.ready_for_moderation is True

    await manager.push(force=True)

    call = service.update_calls[0]
    assert call["puzzle_type"] == "PUZZLE_INOUT"
    assert call["draft"] is False
    assert call["ready_for_moderation"] is True
    assert call["prev_version"] == 3


async def test_push_refuses_while_merge_in_progress(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    await _start_conflicting_merge(manager, service, data)

    with pytest.raises(CgContributionManagerError):
        await manager.push()


# --- active_version refresh (updateContribution's response can report it stale) -----------


async def test_push_refreshes_stale_active_version_via_find_contribution(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)  # version=3, active_version=3
    stale_update_result = dataclasses.replace(_make_contribution(data, version=4), active_version=3)
    client, service, _, _ = _make_fake_client(contribution, update_result=stale_update_result)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    service.find_result = _make_contribution(data, version=4)  # active_version=4

    result = await manager.push(force=True)

    assert result.active_version == 4


async def test_push_gives_up_refreshing_after_max_attempts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(seconds: float) -> None:
        return None
    monkeypatch.setattr("codingame_tools.contribution_manager.manager.asyncio.sleep", no_sleep)

    data = _make_full_data()
    contribution = _make_contribution(data)
    stale_update_result = dataclasses.replace(_make_contribution(data, version=4), active_version=3)
    client, service, _, _ = _make_fake_client(contribution, update_result=stale_update_result)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    service.find_result = stale_update_result

    result = await manager.push(force=True)

    assert result.active_version == 3  # gave up, still stale--but didn't hang or raise


async def test_push_renormalizes_non_canonical_test_case_dirs(tmp_path: Path) -> None:
    """A push()'d tree becomes server's new tip verbatim--if tests/'s ordinal directory names
       aren't canonical when that happens, a later fetch() (always canonical, via
       _materialize_data()) would show a spurious diff against this commit even with identical
       test content. See manager.push()'s docstring."""
    data = _make_full_data()
    contribution = _make_contribution(data)
    updated = _make_contribution(data, version=4)
    client, service, _, _ = _make_fake_client(contribution, update_result=updated)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    (manager.tests_dir / "01").rename(manager.tests_dir / "05")  # simulate a non-canonical layout

    await manager.push()

    assert not (manager.tests_dir / "05").exists()
    assert (manager.tests_dir / "01").is_dir()
    repo = manager.git_repo
    assert repo.diff_name_status("main", "server") == []  # no lingering path-only divergence


# --- fetch ---------------------------------------------------------------------------------


async def test_fetch_is_noop_when_version_unchanged(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, _, _, file_servlet = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    server_before = manager.git_repo.resolve_ref("server")
    file_servlet.calls.clear()

    await manager.fetch()

    assert manager.git_repo.resolve_ref("server") == server_before
    assert file_servlet.calls == []


async def test_fetch_refreshes_status_cache_even_when_version_unchanged(tmp_path: Path) -> None:
    """A moderator vote (or a new comment/view/etc.) doesn't bump the content version, so it
       would never be seen if the status cache refresh were gated on the same "version changed"
       check as the git commit--`fetch()` must refresh `.meta/contribution-status.json`
       unconditionally, even on this no-git-commit path."""
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    approver = CgContributionModerator(
            user_id=6132028, pseudo="NicknamedTwice", public_handle="d2434f", avatar=1, cover=2)
    service.moderator_results = {"validate": [approver], "deny": []}
    server_before = manager.git_repo.resolve_ref("server")

    await manager.fetch()

    assert manager.git_repo.resolve_ref("server") == server_before  # no new commit--version unchanged
    cache = manager.read_status_cache()
    assert cache is not None
    assert cache.moderator_approvals == [approver]  # but the cache still picked up the new vote


async def test_fetch_reuses_cached_cover_when_binary_id_unchanged(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, file_servlet = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    file_servlet.calls.clear()

    new_data = _make_full_data(statement="Server edit")  # same cover_binary_id=555
    service.find_result = _make_contribution(new_data, version=4)

    await manager.fetch()

    assert file_servlet.calls == []
    metadata = manager.server_metadata()
    assert metadata is not None
    assert metadata.cover_binary_hash == compute_content_hash(COVER_CONTENT)


async def test_fetch_downloads_when_binary_id_changed(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, file_servlet = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    file_servlet.calls.clear()

    new_data = _make_full_data(cover_binary_id=666)
    service.find_result = _make_contribution(new_data, version=4)
    file_servlet.result = CgDownloadFileResult.create(id=666, content=b"new-cover-bytes", content_type="image/png")

    await manager.fetch()

    assert file_servlet.calls == [666]
    assert manager.git_repo.read_file_at("server", "cover.png") == b"new-cover-bytes"
    # working tree is never touched by fetch()
    assert (tmp_path / "data" / "cover.png").read_bytes() == COVER_CONTENT


async def test_fetch_self_heals_when_cached_cover_is_stale(tmp_path: Path) -> None:
    """Reuse only happens if the cached bytes' hash still matches what's recorded--if not
       (simulated here via a hash that doesn't match, since we can't easily corrupt a git blob in
       place), fetch re-downloads instead of raising--the cache is opportunistic, not sacred."""
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, file_servlet = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    # cover_binary_id unchanged (555) but cover_binary_hash trailer won't match COVER_CONTENT's
    # real hash if we swap in a same-id-different-bytes scenario is impossible via the public API
    # (id implies content server-side)--so exercise the self-heal path via a *changed* id whose
    # download then also fails to match on a second fetch, confirming no exception either way.
    new_data = _make_full_data(cover_binary_id=666)
    service.find_result = _make_contribution(new_data, version=4)
    file_servlet.result = CgDownloadFileResult.create(id=666, content=b"cover-v4", content_type="image/png")
    await manager.fetch()

    newer_data = _make_full_data(cover_binary_id=666, statement="v5")  # same id, would try reuse
    service.find_result = _make_contribution(newer_data, version=5)
    file_servlet.result = CgDownloadFileResult.create(id=666, content=b"cover-v4", content_type="image/png")

    await manager.fetch()  # doesn't raise regardless of reuse-vs-redownload outcome

    assert manager.git_repo.read_file_at("server", "cover.png") == b"cover-v4"


async def test_fetch_refuses_while_merge_in_progress(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    await _start_conflicting_merge(manager, service, data)

    with pytest.raises(CgContributionManagerError):
        await manager.fetch()


# --- rebase --------------------------------------------------------------------------------


async def test_rebase_up_to_date_when_server_unchanged(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, _, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    status = await manager.rebase()

    assert status == CgRebaseStatus.UP_TO_DATE
    assert (tmp_path / "data" / "statement.cgmd").read_text() == "The statement\n"


async def test_rebase_fast_forwards_when_only_server_changed(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    new_data = _make_full_data(statement="Updated on server")
    service.find_result = _make_contribution(new_data, version=4)

    status = await manager.rebase()

    assert status == CgRebaseStatus.FAST_FORWARDED
    assert (tmp_path / "data" / "statement.cgmd").read_text() == "Updated on server\n"
    repo = manager.git_repo
    assert repo.resolve_ref("main") == repo.resolve_ref("server")
    # a true fast-forward, not a fresh sibling commit
    assert await manager.rebase() == CgRebaseStatus.UP_TO_DATE


async def test_rebase_reports_conflict_and_changes_nothing_when_both_diverged(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    (tmp_path / "data" / "statement.cgmd").write_text("Local edit\n")
    repo = manager.git_repo
    repo.commit_worktree("local edit")
    new_data = _make_full_data(statement="Server edit")
    service.find_result = _make_contribution(new_data, version=4)

    status = await manager.rebase()

    assert status == CgRebaseStatus.CONFLICT
    assert (tmp_path / "data" / "statement.cgmd").read_text() == "Local edit\n"


async def test_rebase_up_to_date_even_with_uncommitted_local_edits_if_server_unchanged(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, _, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    (tmp_path / "data" / "statement.cgmd").write_text("Local edit\n")

    status = await manager.rebase()

    assert status == CgRebaseStatus.UP_TO_DATE
    assert (tmp_path / "data" / "statement.cgmd").read_text() == "Local edit\n"


async def test_rebase_refuses_while_merge_in_progress(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    await _start_conflicting_merge(manager, service, data)

    with pytest.raises(CgContributionManagerError):
        await manager.rebase()


# --- status ----------------------------------------------------------------------------------


async def test_read_status_cache_self_heals_on_corrupt_file(tmp_path: Path) -> None:
    """Opportunistic cache, same self-healing spirit as fetch()'s cover-image reuse: a corrupt/
       unparseable .meta/contribution-status.json is treated as absent, not fatal."""
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, _, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    assert manager.read_status_cache() is not None  # sanity: import_() did write it

    manager.status_cache_file.write_text("not valid json{{{")

    assert manager.read_status_cache() is None


async def test_status_not_pushed_for_a_create_only_directory(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.create(title="My Puzzle")

    status = await manager.status()

    assert not status.pushed
    assert status.contribution_handle is None
    assert status.local_title == "My Puzzle"
    assert status.sync_status == CgContributionSyncStatus.NOT_PUSHED
    assert status.local_version is None
    assert status.server is None
    assert service.find_call_count == 0  # never fetched--create() is purely local


async def test_status_up_to_date_after_import_uses_cached_server_data(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data, version=3)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    find_calls_after_import = service.find_call_count

    status = await manager.status()

    assert status.pushed
    assert status.contribution_handle == "handle-1"
    assert status.sync_status == CgContributionSyncStatus.UP_TO_DATE
    assert not status.local_dirty
    assert not status.merge_in_progress
    assert status.local_version == 3
    assert status.server is not None
    assert status.server.status == "PENDING"
    assert status.server.public_handle == "handle-1"
    assert status.local_difficulty == "easy"
    # served from .meta/contribution-status.json (written by import_()), not a fresh findContribution
    assert service.find_call_count == find_calls_after_import
    # import_() already refreshed the status cache (including moderator votes)--populated (as
    # empty lists here, since the fake service defaults to no votes cast) without remote=True
    assert status.moderator_approvals == []
    assert status.moderator_denials == []
    assert status.status_cache_refreshed_at is not None


async def test_status_remote_refreshes_moderator_approve_reject_votes(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data, version=3)  # CgContribution.id is hardcoded to 1 in _make_contribution
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    service.find_contribution_moderators_calls.clear()  # isolate the status(remote=True) call below
    approver = CgContributionModerator(
            user_id=6132028, pseudo="NicknamedTwice", public_handle="d2434f", avatar=1, cover=2)
    service.moderator_results = {"validate": [approver], "deny": []}

    status = await manager.status(remote=True)

    assert status.moderator_approvals == [approver]
    assert status.moderator_denials == []
    assert service.find_contribution_moderators_calls == [(1, "validate"), (1, "deny")]


async def test_status_local_draft_reflects_uncommitted_edits_while_cached_server_reflects_last_sync(tmp_path: Path) -> None:
    """`status.local_draft`/`local_ready_for_moderation`/`local_puzzle_type` reflect `data/
       contribution-data.json`'s current on-disk value (what the next `push()` would send).
       `status.server` (from the unredacted `.meta/contribution-status.json` cache--unlike the
       git `version-data` branch's copy, this one is NOT redacted) reflects the server's state as
       of the last fetch--right after `import_()` the two agree, but a local edit not yet pushed
       makes them diverge, which is exactly why local edits should be read from `local_*`, not
       `server`."""
    data = _make_full_data()
    contribution = _make_contribution(data, draft=False, ready_for_moderation=True)
    client, _, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    status = await manager.status()
    assert status.local_draft is False
    assert status.local_ready_for_moderation is True
    assert status.local_puzzle_type == "PUZZLE_INOUT"
    assert status.server is not None
    assert status.server.draft is False
    assert status.server.ready_for_moderation is True

    view = manager.load()
    manager.save(dataclasses.replace(view, draft=True))

    status2 = await manager.status()
    assert status2.local_draft is True  # the uncommitted local edit
    assert status2.server is not None
    assert status2.server.draft is False  # unchanged--still the last-synced server value


async def test_status_local_ahead_after_local_commit(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, _, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    (tmp_path / "data" / "statement.cgmd").write_text("Local edit\n")
    manager.git_repo.commit_worktree("local edit")

    status = await manager.status()

    assert status.sync_status == CgContributionSyncStatus.LOCAL_AHEAD
    assert not status.local_dirty  # committed, not just sitting uncommitted


async def test_status_local_ahead_with_uncommitted_edits(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, _, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    (tmp_path / "data" / "statement.cgmd").write_text("Local edit\n")

    status = await manager.status()

    assert status.sync_status == CgContributionSyncStatus.LOCAL_AHEAD
    assert status.local_dirty


async def test_status_server_ahead_only_refreshes_with_remote(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    new_data = _make_full_data(statement="Updated on server")
    service.find_result = _make_contribution(new_data, version=4)

    stale = await manager.status()
    assert stale.sync_status == CgContributionSyncStatus.UP_TO_DATE  # cache not yet refreshed
    assert stale.local_version == 3

    fresh = await manager.status(remote=True)
    assert fresh.sync_status == CgContributionSyncStatus.SERVER_AHEAD
    assert fresh.local_version == 4


async def test_status_diverged_when_both_sides_changed(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    (tmp_path / "data" / "statement.cgmd").write_text("Local edit\n")
    manager.git_repo.commit_worktree("local edit")
    new_data = _make_full_data(statement="Server edit")
    service.find_result = _make_contribution(new_data, version=4)

    status = await manager.status(remote=True)

    assert status.sync_status == CgContributionSyncStatus.DIVERGED


async def test_status_reports_merge_in_progress(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    await _start_conflicting_merge(manager, service, data)

    status = await manager.status()

    assert status.merge_in_progress
    assert status.sync_status == CgContributionSyncStatus.MERGE_IN_PROGRESS
    assert not status.local_dirty  # not meaningful mid-merge, deliberately not computed


async def test_status_raises_if_never_imported_or_created(tmp_path: Path) -> None:
    contribution = _make_contribution(_make_full_data())
    client, _, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]

    with pytest.raises(FileNotFoundError):
        await manager.status()


# --- merge_discard_local / merge_discard_server --------------------------------------------


async def test_merge_discard_local_always_overwrites(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    (tmp_path / "data" / "statement.cgmd").write_text("Local edit\n")

    new_data = _make_full_data(statement="Server edit")
    service.find_result = _make_contribution(new_data, version=4)

    await manager.merge_discard_local()

    assert (tmp_path / "data" / "statement.cgmd").read_text() == "Server edit\n"
    repo = manager.git_repo
    assert repo.resolve_ref("main") == repo.resolve_ref("server")


async def test_merge_discard_server_leaves_working_content_untouched(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    (tmp_path / "data" / "statement.cgmd").write_text("Local edit\n")

    new_data = _make_full_data(statement="Server edit")
    service.find_result = _make_contribution(new_data, version=4)

    result = await manager.merge_discard_server()

    assert result.last_version.version == 4
    assert (tmp_path / "data" / "statement.cgmd").read_text() == "Local edit\n"
    metadata = manager.server_metadata()
    assert metadata is not None
    assert metadata.version == 4


async def test_merge_discard_local_refuses_while_merge_in_progress(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    await _start_conflicting_merge(manager, service, data)
    with pytest.raises(CgContributionManagerError):
        await manager.merge_discard_local()


async def test_merge_discard_server_refuses_while_merge_in_progress(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    await _start_conflicting_merge(manager, service, data)
    with pytest.raises(CgContributionManagerError):
        await manager.merge_discard_server()


# --- discard_local --------------------------------------------------------------------------------


async def test_discard_local_discards_local_edits_and_untracked_files_without_network(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, file_servlet = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    (tmp_path / "data" / "statement.cgmd").write_text("Local edit\n")
    (tmp_path / "data" / "stray_untracked_file.txt").write_text("should be removed\n")
    find_calls_before = service.find_call_count
    file_servlet.calls.clear()

    view = manager.discard_local()

    assert (tmp_path / "data" / "statement.cgmd").read_text() == "The statement\n"
    assert not (tmp_path / "data" / "stray_untracked_file.txt").exists()
    assert view.data.title == "My Puzzle"
    assert service.find_call_count == find_calls_before
    assert file_servlet.calls == []
    assert not any(tmp_path.glob("solution.*"))  # nothing at the root; the file lives in data/


async def test_discard_local_requires_a_prior_import(tmp_path: Path) -> None:
    manager = CgContributionManager(tmp_path, object())  # type: ignore[arg-type]
    with pytest.raises(FileNotFoundError):
        manager.discard_local()


async def test_discard_local_refuses_while_merge_in_progress(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    await _start_conflicting_merge(manager, service, data)
    with pytest.raises(CgContributionManagerError):
        manager.discard_local()


async def test_discard_local_preserves_identity(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, _, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    manager.discard_local()

    identity = manager.load_identity()
    assert identity is not None
    assert identity.contribution_handle == "handle-1"
    assert manager.server_metadata() is not None


# --- merge_start / merge_continue / merge_abort ---------------------------------------------


async def test_merge_start_reports_up_to_date_when_server_unchanged(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, _, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    (tmp_path / "data" / "statement.cgmd").write_text("Local edit\n")  # even with local edits present

    result = await manager.merge_start()

    assert result.status == CgMergeStartStatus.UP_TO_DATE
    assert not manager.merge_in_progress
    assert (tmp_path / "data" / "statement.cgmd").read_text() == "Local edit\n"  # untouched


async def test_merge_start_is_idempotent(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    (tmp_path / "data" / "statement.cgmd").write_text("Local edit\n")
    manager.git_repo.commit_worktree("local edit")  # real local commit, so the merge has conflicts
    service.find_result = _make_contribution(_make_full_data(statement="Server edit"), version=4)

    first = await manager.merge_start()
    assert first.status == CgMergeStartStatus.STARTED
    assert first.text_conflicts == ("statement.cgmd",)
    assert manager.merge_in_progress

    second = await manager.merge_start()

    assert second.status == CgMergeStartStatus.ALREADY_IN_PROGRESS
    # untouched, not re-attempted--conflict markers from the first attempt still there
    assert "<<<<<<<" in (tmp_path / "data" / "statement.cgmd").read_text()


async def test_merge_start_auto_applies_remote_only_change(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    new_data = _make_full_data(statement="Server edit")
    service.find_result = _make_contribution(new_data, version=4)

    result = await manager.merge_start()

    assert result.status == CgMergeStartStatus.STARTED
    assert result.text_conflicts == ()
    assert result.binary_conflicts == ()
    assert (tmp_path / "data" / "statement.cgmd").read_text() == "Server edit\n"
    assert not manager.merge_in_progress  # clean merge auto-commits, nothing left to continue


async def test_merge_start_leaves_local_only_change_untouched(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    (tmp_path / "data" / "statement.cgmd").write_text("Local edit\n")
    manager.git_repo.commit_worktree("local edit")

    # An unrelated server-side change, so the merge machinery actually runs--otherwise a purely
    # local-only change, with the server unchanged, short-circuits to UP_TO_DATE.
    new_data = _make_full_data(solution="print('server')")
    service.find_result = _make_contribution(new_data, version=4)

    result = await manager.merge_start()

    assert result.status == CgMergeStartStatus.STARTED
    assert result.text_conflicts == ()
    assert (tmp_path / "data" / "statement.cgmd").read_text() == "Local edit\n"
    assert (tmp_path / "data" / "solution.py").read_text() == "print('server')\n"


async def test_merge_start_writes_diff3_markers_for_text_conflict(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    (tmp_path / "data" / "statement.cgmd").write_text("Local edit\n")
    manager.git_repo.commit_worktree("local edit")

    new_data = _make_full_data(statement="Server edit")
    service.find_result = _make_contribution(new_data, version=4)

    result = await manager.merge_start()

    assert "statement.cgmd" in result.text_conflicts
    content = (tmp_path / "data" / "statement.cgmd").read_text()
    assert "<<<<<<<" in content
    assert "Local edit" in content
    assert "Server edit" in content
    assert manager.merge_in_progress


async def test_merge_start_keeps_local_cover_on_binary_conflict(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, file_servlet = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    local_cover = b"\x89PNG\x00locally-changed-cover"
    remote_cover = b"\x89PNG\x00remote-changed-cover"
    (tmp_path / "data" / "cover.png").write_bytes(local_cover)
    manager.git_repo.commit_worktree("local cover edit")

    new_data = _make_full_data(cover_binary_id=666)
    service.find_result = _make_contribution(new_data, version=4)
    file_servlet.result = CgDownloadFileResult.create(id=666, content=remote_cover, content_type="image/png")

    result = await manager.merge_start()

    assert "cover.png" in result.binary_conflicts
    assert (tmp_path / "data" / "cover.png").read_bytes() == local_cover  # kept local


async def test_merge_start_clean_merge_renormalizes_test_case_dirs(tmp_path: Path) -> None:
    """A clean merge (git's own auto-commit, no conflicts) can still leave tests/ with a
       renumbering gap if the two sides' non-conflicting changes touch different ordinals--e.g.
       here, local removes ordinal "01" (Case A) while remote independently edits an unrelated
       field, leaving only "02" (Case B) on disk after the merge. See manager.merge_start()'s
       docstring for why this needs fixing up as part of the same commit."""
    data = _make_two_pair_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    assert (manager.tests_dir / "01").is_dir()
    assert (manager.tests_dir / "02").is_dir()

    shutil.rmtree(manager.tests_dir / "01")  # locally remove Case A, leaving only "02"
    manager.git_repo.commit_worktree("remove Case A locally")

    new_data = _make_two_pair_data(statement="Server edit")  # unrelated remote change--tests/ untouched
    service.find_result = _make_contribution(new_data, version=4)

    result = await manager.merge_start()

    assert result.status == CgMergeStartStatus.STARTED
    assert not manager.merge_in_progress  # clean merge, auto-committed
    assert (manager.tests_dir / "01").is_dir()  # "02" renumbered down to "01"
    assert not (manager.tests_dir / "02").exists()
    assert (tmp_path / "data" / "statement.cgmd").read_text() == "Server edit\n"


async def test_merge_start_leaves_solution_symlink_untouched_while_conflicted(tmp_path: Path) -> None:
    """`solution.py` lives at `contribution_dir`'s root, *outside* `data/` (git's work tree)--so
       an in-progress merge (which only ever touches paths inside `data/`) can't affect it either
       way. It's only ever refreshed at merge's terminal points (`merge_continue()`/
       `merge_abort()`--see those tests), never mid-conflict."""
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    (tmp_path / "data" / "statement.cgmd").write_text("Local edit\n")
    manager.git_repo.commit_worktree("local edit")
    new_data = _make_full_data(statement="Server edit")
    service.find_result = _make_contribution(new_data, version=4)  # conflicting change -> stays in progress
    assert not any(tmp_path.glob("solution.*"))  # nothing at the root; the file lives in data/

    result = await manager.merge_start()

    assert manager.merge_in_progress
    assert "statement.cgmd" in result.text_conflicts
    assert not any(tmp_path.glob("solution.*"))  # nothing at the root; the file lives in data/


async def test_merge_start_handles_added_test_case_from_remote(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    new_test_cases = [*data.test_cases, _make_test_case("Case B", "5", "6", is_test=True, is_validator=False)]
    new_data = dataclasses.replace(data, test_cases=new_test_cases)
    service.find_result = _make_contribution(new_data, version=4)

    result = await manager.merge_start()

    assert result.text_conflicts == () and result.binary_conflicts == ()
    assert (tmp_path / "data" / "tests" / "02" / "Case-B" / "local" / "input.txt").read_text() == "5\n"


async def test_merge_continue_requires_in_progress_merge(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, _, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    with pytest.raises(CgContributionManagerError):
        manager.merge_continue()


async def test_merge_continue_refuses_when_markers_remain(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    (tmp_path / "data" / "statement.cgmd").write_text("Local edit\n")
    manager.git_repo.commit_worktree("local edit")
    new_data = _make_full_data(statement="Server edit")
    service.find_result = _make_contribution(new_data, version=4)
    await manager.merge_start()

    with pytest.raises(CgContributionManagerError):
        manager.merge_continue()


async def test_merge_continue_renormalizes_test_case_dirs(tmp_path: Path) -> None:
    """Same renumbering-gap scenario as the clean-merge test, but this time forced through an
       actual text conflict (so merge_continue() itself, not merge_start()'s auto-commit path,
       is what needs to renormalize/fold in the fix--see manager.merge_continue()'s docstring for
       why the renormalize has to happen *after* the merge commit exists)."""
    data = _make_two_pair_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    shutil.rmtree(manager.tests_dir / "01")  # locally remove Case A, leaving only "02"
    (tmp_path / "data" / "statement.cgmd").write_text("Local edit\n")
    manager.git_repo.commit_worktree("remove Case A locally, edit statement")

    new_data = _make_two_pair_data(statement="Server edit")  # conflicts with the local statement edit
    service.find_result = _make_contribution(new_data, version=4)
    result = await manager.merge_start()
    assert "statement.cgmd" in result.text_conflicts
    assert manager.merge_in_progress

    (tmp_path / "data" / "statement.cgmd").write_text("Resolved by hand\n")
    manager.merge_continue()

    assert not manager.merge_in_progress
    assert (manager.tests_dir / "01").is_dir()  # "02" renumbered down to "01"
    assert not (manager.tests_dir / "02").exists()


async def test_merge_continue_succeeds_once_markers_resolved(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    (tmp_path / "data" / "statement.cgmd").write_text("Local edit\n")
    manager.git_repo.commit_worktree("local edit")
    new_data = _make_full_data(statement="Server edit")
    service.find_result = _make_contribution(new_data, version=4)
    await manager.merge_start()

    (tmp_path / "data" / "statement.cgmd").write_text("Resolved by hand\n")
    manager.merge_continue()

    assert not manager.merge_in_progress
    metadata = manager.server_metadata()
    assert metadata is not None
    assert metadata.version == 4
    assert not any(tmp_path.glob("solution.*"))  # nothing at the root; the file lives in data/
    repo = manager.git_repo
    assert repo.merge_base("main", "server") == repo.resolve_ref("server")


async def test_merge_abort_requires_in_progress_merge(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, _, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    with pytest.raises(CgContributionManagerError):
        manager.merge_abort()


async def test_merge_abort_restores_pre_merge_state(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    (tmp_path / "data" / "statement.cgmd").write_text("Local edit\n")
    manager.git_repo.commit_worktree("local edit")
    new_data = _make_full_data(statement="Server edit")
    service.find_result = _make_contribution(new_data, version=4)
    await manager.merge_start()

    manager.merge_abort()

    assert not manager.merge_in_progress
    assert (tmp_path / "data" / "statement.cgmd").read_text() == "Local edit\n"  # restored to pre-merge local state
    assert not any(tmp_path.glob("solution.*"))  # nothing at the root; the file lives in data/
    metadata = manager.server_metadata()
    assert metadata is not None
    # server itself is NOT rolled back--merge_start()'s fetch() (step 1) already advanced it
    # before the merge attempt even began; merge_abort() only undoes the merge attempt against
    # main, not that prior fetch (see CgContributionManager.merge_abort's docstring).
    assert metadata.version == 4


# --- delete ----------------------------------------------------------------------------------


async def test_delete_removes_the_local_directory_by_default(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    await manager.delete()

    assert service.delete_calls == ["handle-1"]
    assert not tmp_path.exists()


async def test_delete_keep_local_detaches_and_resets_identity(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    await manager.delete(keep_local=True)

    assert service.delete_calls == ["handle-1"]
    assert tmp_path.exists()
    assert (tmp_path / "data" / "statement.cgmd").read_text() == "The statement\n"  # content preserved

    identity = manager.load_identity()
    assert identity is not None
    assert identity.contribution_handle is None

    repo = manager.git_repo
    assert repo.resolve_ref("main") is not None  # main's own history untouched
    assert repo.resolve_ref("server") is None
    assert repo.resolve_ref("version-data") is None
    assert manager.server_metadata() is None


async def test_delete_keep_local_then_push_creates_a_new_contribution(tmp_path: Path) -> None:
    """The "fork/template" workflow: use an existing contribution's content as a starting point
       for a brand new one, without touching the original (except deleting it here, since this
       reuses the same working directory--a real templating workflow would `import_` into a fresh
       directory instead, then never call `delete()` on the original at all).

       Uses a two-test-case-pair contribution specifically to demonstrate the risk this whole
       "clone as template" workflow poses that the two-step default first push protects
       against--a carried-over test suite could be large/complex, and must never be sent to the
       create-time API call, only the (much smaller, throwaway) minimal stub."""
    data = _make_two_pair_data()
    contribution = _make_contribution(data, public_handle="old-handle")
    new_handle_stub = _make_contribution(data, public_handle="new-handle-1", version=1)
    new_handle_real = _make_contribution(data, public_handle="new-handle-1", version=2)
    client, service, _, _ = _make_fake_client(contribution)
    service.create_result = "new-handle-1"
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("old-handle")

    await manager.delete(keep_local=True)
    service.find_result = new_handle_stub
    service.update_result = new_handle_real

    result = await manager.push()

    assert service.delete_calls == ["old-handle"]
    assert len(service.create_calls) == 1
    stub_call = service.create_calls[0]
    assert stub_call["contribution_data"].title == data.title
    assert len(stub_call["contribution_data"].test_cases) == 2  # the minimal stub, not the cloned 4-test-case suite

    assert len(service.update_calls) == 1
    update_call = service.update_calls[0]
    assert update_call["contribution_id"] == "new-handle-1"
    assert len(update_call["contribution_data"].test_cases) == 4  # the real, cloned content

    assert result.public_handle == "new-handle-1"
    assert result.last_version.version == 2
    identity = manager.load_identity()
    assert identity is not None
    assert identity.contribution_handle == "new-handle-1"


async def test_delete_succeeds_if_never_pushed(tmp_path: Path) -> None:
    """Plain delete() (no keep_local/keep_server) tolerates a never-pushed working directory
       just fine--there's nothing to send to deleteContribution, so it just removes the local
       directory, same as it would for any other directory."""
    client = object()
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.create(title="My Puzzle")

    await manager.delete()  # doesn't raise, and never touches self.client

    assert not tmp_path.exists()


async def test_delete_refuses_while_merge_in_progress(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")
    await _start_conflicting_merge(manager, service, data)

    with pytest.raises(CgContributionManagerError):
        await manager.delete()


async def test_delete_keep_server_leaves_server_untouched(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    await manager.delete(keep_server=True)

    assert service.delete_calls == []  # deleteContribution never called
    assert not tmp_path.exists()


async def test_delete_keep_server_refuses_if_never_pushed(tmp_path: Path) -> None:
    """Unlike plain delete(), keep_server=True is an explicit statement about server state
       ("leave the thing I'm tracking alone")--nonsensical to honor silently when nothing is
       actually tracked yet, so this raises instead."""
    client = object()
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.create(title="My Puzzle")

    with pytest.raises(FileNotFoundError):
        await manager.delete(keep_server=True)

    assert tmp_path.exists()  # refused before touching anything


async def test_delete_keep_local_refuses_if_never_pushed(tmp_path: Path) -> None:
    """Same reasoning as keep_server: keep_local means "detach from the thing I'm tracking,"
       which doesn't make sense when nothing is being tracked yet."""
    client = object()
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.create(title="My Puzzle")

    with pytest.raises(FileNotFoundError):
        await manager.delete(keep_local=True)

    assert tmp_path.exists()  # refused before touching anything

    assert tmp_path.exists()  # refused before touching anything


async def test_delete_keep_local_and_keep_server_are_mutually_exclusive(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(contribution)
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    with pytest.raises(CgContributionManagerError):
        await manager.delete(keep_local=True, keep_server=True)

    assert service.delete_calls == []
    assert tmp_path.exists()


# --- a push with nothing to push -----------------------------------------------------------------


async def test_push_with_no_local_changes_does_nothing(tmp_path: Path) -> None:
    """`updateContribution` has no empty update: it increments the version and re-runs moderation
       whether or not anything differs. So republishing identical content costs a review cycle and
       buries the history of real changes behind no-op versions--it has to be asked for."""
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(
            contribution, update_result=_make_contribution(data, version=4))
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    assert await manager.push() is None
    assert service.update_calls == []


async def test_push_force_publishes_identical_content_anyway(tmp_path: Path) -> None:
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(
            contribution, update_result=_make_contribution(data, version=4))
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    result = await manager.push(force=True)

    assert result is not None
    assert len(service.update_calls) == 1


async def test_push_detects_a_change_to_any_content_file(tmp_path: Path) -> None:
    """The check compares whole trees, so it covers every file under data/--not just the ones a
       hand-written list would have remembered."""
    data = _make_full_data()
    for name, content in (("statement.cgmd", "new statement\n"),
                          ("constraints.cgmd", "new constraints\n"),
                          ("cover.png", None)):
        root = tmp_path / name.replace(".", "_")
        contribution = _make_contribution(data)
        client, service, _, _ = _make_fake_client(
                contribution, update_result=_make_contribution(data, version=4))
        manager = CgContributionManager(root, client)  # type: ignore[arg-type]
        await manager.import_("handle-1")
        target = root / "data" / name
        if content is None:
            target.write_bytes(b"\x89PNG\r\n\x1a\n" + b"different cover bytes")
        else:
            target.write_text(content)

        assert await manager.push() is not None, f"editing {name} should be pushable"
        assert len(service.update_calls) == 1


async def test_push_after_a_no_op_push_still_sees_a_later_edit(tmp_path: Path) -> None:
    """The no-op must not leave anything behind that makes the *next* real push look unchanged--it
       returns before touching any ref."""
    data = _make_full_data()
    contribution = _make_contribution(data)
    client, service, _, _ = _make_fake_client(
            contribution, update_result=_make_contribution(data, version=4))
    manager = CgContributionManager(tmp_path, client)  # type: ignore[arg-type]
    await manager.import_("handle-1")

    assert await manager.push() is None

    (tmp_path / "data" / "statement.cgmd").write_text("a real edit\n")
    assert await manager.push() is not None
    assert len(service.update_calls) == 1
