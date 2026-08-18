"""Tests for editing a contribution's scalar metadata and its topic tags.

All of it is local file editing -- nothing here touches the network -- so the risks are the quiet
ones: writing a field the caller did not mention, losing an unrelated field on the way through, or
tagging a contribution twice with the same topic.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from codingame_tools.client.common.protocol.contribution import CgContributionData, CgTopic
from codingame_tools.contribution_manager import (
    CgContributionManager,
    CgContributionManagerError,
)
from codingame_tools.contribution_manager.schema import CgContributionView


def _topic(handle: str, topic_id: int, label: str = "Label", puzzle_count: int = 1) -> CgTopic:
    return CgTopic(label_map={"1": label, "2": label}, id=topic_id, handle=handle,
                   category="FUNDAMENTALS", puzzle_count=puzzle_count)


@pytest.fixture
def manager(tmp_path: Path) -> CgContributionManager:
    """A working directory with a contribution-data.json on disk, and no client."""
    root = tmp_path / "contribution"
    mgr = CgContributionManager(root, None)  # type: ignore[arg-type]
    mgr.save(CgContributionView(
            puzzle_type="PUZZLE_INOUT", draft=True, ready_for_moderation=False,
            data=CgContributionData(title="Original", difficulty="easy",
                                    solution_language="Python3", statement="Keep me"),
        ))
    return mgr


class TestUpdateMetadata:
    def test_sets_each_field(self, manager: CgContributionManager) -> None:
        manager.update_metadata(title="Renamed", difficulty="hard", draft=False,
                                ready_for_moderation=True)
        view = manager.load()
        assert (view.data.title, view.data.difficulty) == ("Renamed", "hard")
        assert (view.draft, view.ready_for_moderation) == (False, True)

    def test_an_unmentioned_field_is_left_alone(self, manager: CgContributionManager) -> None:
        """The reason for the UNSET sentinel: `draft=False` must not reset the title, and must be
           distinguishable from "no opinion" -- both are falsey."""
        manager.update_metadata(draft=False)
        view = manager.load()
        assert view.data.title == "Original"
        assert view.data.difficulty == "easy"
        assert view.ready_for_moderation is False

    def test_setting_a_boolean_false_is_not_mistaken_for_unset(self,
                                                               manager: CgContributionManager) -> None:
        manager.update_metadata(draft=False)
        assert manager.load().draft is False
        manager.update_metadata(draft=True)
        assert manager.load().draft is True

    def test_unrelated_content_survives(self, manager: CgContributionManager) -> None:
        """A read-modify-write that dropped sidecar-backed content would be silent until push."""
        manager.update_metadata(title="Renamed")
        assert manager.load().data.statement == "Keep me"
        assert manager.load().data.solution_language == "Python3"

    @pytest.mark.parametrize("difficulty", ["easy", "medium", "hard"])
    def test_accepts_every_difficulty_codingame_offers(self, manager: CgContributionManager,
                                                       difficulty: str) -> None:
        manager.update_metadata(difficulty=difficulty)
        assert manager.load().data.difficulty == difficulty

    def test_rejects_an_unknown_difficulty(self, manager: CgContributionManager) -> None:
        with pytest.raises(CgContributionManagerError, match="easy, medium, hard"):
            manager.update_metadata(difficulty="impossible")
        assert manager.load().data.difficulty == "easy", "a rejected value must not be written"

    def test_rejects_an_unsupported_puzzle_type(self, manager: CgContributionManager) -> None:
        """CodinGame has other contribution types; none round-trip through this format yet, so
           accepting one would produce a working directory that cannot be pushed."""
        with pytest.raises(CgContributionManagerError, match="PUZZLE_INOUT"):
            manager.update_metadata(puzzle_type="CLASHOFCODE")
        assert manager.load().puzzle_type == "PUZZLE_INOUT"

    def test_rejects_an_empty_title(self, manager: CgContributionManager) -> None:
        with pytest.raises(CgContributionManagerError, match="cannot be empty"):
            manager.update_metadata(title="   ")


class TestTopics:
    def test_add_and_list(self, manager: CgContributionManager) -> None:
        added = manager.add_topics([_topic("graphs", 48), _topic("DFS", 55)])
        assert [t.handle for t in added] == ["graphs", "DFS"]
        assert [t.handle for t in manager.load().data.topics] == ["graphs", "DFS"]

    def test_adding_an_existing_topic_is_a_no_op(self, manager: CgContributionManager) -> None:
        manager.add_topics([_topic("graphs", 48)])
        added = manager.add_topics([_topic("graphs", 48)])
        assert added == []
        assert len(manager.load().data.topics) == 1

    def test_adding_matches_on_identity_not_field_equality(self,
                                                           manager: CgContributionManager) -> None:
        """The catalogue's puzzle_count drifts, so re-adding a topic whose count has since changed
           must still count as already present rather than appending a duplicate."""
        manager.add_topics([_topic("graphs", 48, puzzle_count=45)])
        added = manager.add_topics([_topic("graphs", 48, puzzle_count=46)])
        assert added == []
        assert len(manager.load().data.topics) == 1

    def test_add_preserves_order_and_earlier_topics(self, manager: CgContributionManager) -> None:
        manager.add_topics([_topic("graphs", 48)])
        manager.add_topics([_topic("sets", 52)])
        assert [t.handle for t in manager.load().data.topics] == ["graphs", "sets"]

    def test_remove(self, manager: CgContributionManager) -> None:
        manager.add_topics([_topic("graphs", 48), _topic("DFS", 55)])
        removed = manager.remove_topics([_topic("graphs", 48)])
        assert [t.handle for t in removed] == ["graphs"]
        assert [t.handle for t in manager.load().data.topics] == ["DFS"]

    def test_remove_matches_a_drifted_copy(self, manager: CgContributionManager) -> None:
        manager.add_topics([_topic("graphs", 48, puzzle_count=45)])
        assert manager.remove_topics([_topic("graphs", 48, puzzle_count=99)])
        assert manager.load().data.topics == []

    def test_removing_an_absent_topic_is_a_no_op(self, manager: CgContributionManager) -> None:
        manager.add_topics([_topic("graphs", 48)])
        assert manager.remove_topics([_topic("sets", 52)]) == []
        assert len(manager.load().data.topics) == 1

    def test_topic_edits_leave_other_content_alone(self, manager: CgContributionManager) -> None:
        manager.add_topics([_topic("graphs", 48)])
        assert manager.load().data.title == "Original"
        assert manager.load().data.statement == "Keep me"


def _cg(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run the real CLI in a subprocess, so the argument parsing and handler are both exercised."""
    return subprocess.run(
            [sys.executable, "-m", "codingame_tools.cli", *args],
            capture_output=True, text=True, cwd=cwd, check=False,
        )


@pytest.fixture
def contribution_dir(tmp_path: Path) -> Path:
    """A real working directory, made by the CLI itself. `create` is purely local."""
    root = tmp_path / "c"
    result = _cg("contribution", "create", "-t", "PUZZLE_INOUT", "--language", "Python3",
                 str(root), "Fixture", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    return root


class TestSetCommandSurface:
    """Every row `cg contribution set` prints must be a field `set FIELD` can then act on.

       This is the contradiction being pinned: an earlier version listed solution-language and
       then answered "isn't a settable field" to both reading and writing it, so the listing
       advertised something the command refused to acknowledge."""

    def test_every_listed_field_can_be_read_back(self, contribution_dir: Path,
                                                 tmp_path: Path) -> None:
        listing = _cg("contribution", "--contribution-dir", str(contribution_dir), "set",
                      cwd=tmp_path)
        assert listing.returncode == 0, listing.stderr
        fields = [line.split()[0] for line in listing.stdout.splitlines() if line.strip()]
        assert "solution-language" in fields, "the listing should show the language"
        for field in fields:
            one = _cg("contribution", "--contribution-dir", str(contribution_dir), "set", field,
                      cwd=tmp_path)
            assert one.returncode == 0, f"`set {field}` is listed but not readable: {one.stderr}"
            assert one.stdout.strip(), f"`set {field}` printed nothing"

    def test_setting_the_language_does_the_real_switch(self, contribution_dir: Path,
                                                       tmp_path: Path) -> None:
        """`set solution-language` absorbed what `set-language` used to do, so it must rename the
           solution file too -- writing only the JSON field would leave the solution behind in the
           old language."""
        result = _cg("contribution", "--contribution-dir", str(contribution_dir), "set",
                     "solution-language", "C++", cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        after = _cg("contribution", "--contribution-dir", str(contribution_dir), "set",
                    "solution-language", cwd=tmp_path)
        assert after.stdout.strip() == "C++"
        assert (contribution_dir / "data" / "solution.cpp").exists()
        assert not (contribution_dir / "data" / "solution.py").exists()

    def test_the_destructive_guard_survived_the_move(self, contribution_dir: Path,
                                                     tmp_path: Path) -> None:
        """A contribution keeps one solution with no history, so switching away from real work is
           refused without --force. That guard moved with the command."""
        (contribution_dir / "data" / "solution.py").write_text("print('real work')\n")
        refused = _cg("contribution", "--contribution-dir", str(contribution_dir), "set",
                      "solution-language", "C++", cwd=tmp_path)
        assert refused.returncode != 0
        assert "--force" in refused.stderr
        assert (contribution_dir / "data" / "solution.py").exists(), "the refusal must not have switched"
        forced = _cg("contribution", "--contribution-dir", str(contribution_dir), "set",
                     "solution-language", "C++", "--force", cwd=tmp_path)
        assert forced.returncode == 0, forced.stderr

    def test_the_old_set_language_command_is_gone(self, tmp_path: Path) -> None:
        """It moved to `set solution-language`; leaving both would be two ways to do one thing."""
        assert _cg("contribution", "set-language", "C++", cwd=tmp_path).returncode != 0
        assert _cg("puzzle", "set-language", "C++", cwd=tmp_path).returncode != 0

    def test_a_real_field_still_writes(self, contribution_dir: Path, tmp_path: Path) -> None:
        assert _cg("contribution", "--contribution-dir", str(contribution_dir), "set",
                   "difficulty", "hard", cwd=tmp_path).returncode == 0
        after = _cg("contribution", "--contribution-dir", str(contribution_dir), "set",
                    "difficulty", cwd=tmp_path)
        assert after.stdout.strip() == "hard"


class TestSettableFieldRegistry:
    """Registry-level invariants behind the command surface above."""

    def test_every_listed_field_is_readable(self) -> None:
        from codingame_tools.cli.main import CONTRIBUTION_SET_FIELDS
        view = CgContributionView(
                puzzle_type="PUZZLE_INOUT", draft=True, ready_for_moderation=False,
                data=CgContributionData(title="T", difficulty="easy", solution_language="Python3"),
            )
        for reader in CONTRIBUTION_SET_FIELDS.values():
            reader(view)

    def test_boolean_fields_are_all_settable_fields(self) -> None:
        from codingame_tools.cli.main import (
            CONTRIBUTION_BOOLEAN_FIELDS,
            CONTRIBUTION_SET_FIELDS,
        )
        assert set(CONTRIBUTION_SET_FIELDS) >= CONTRIBUTION_BOOLEAN_FIELDS

    def test_metadata_fields_map_to_real_update_metadata_keywords(self) -> None:
        """Fields routed through update_metadata must name a keyword it accepts. solution-language
           is deliberately excluded -- it goes through set_language() instead."""
        import inspect

        from codingame_tools.cli.main import CONTRIBUTION_METADATA_FIELDS
        accepted = set(inspect.signature(CgContributionManager.update_metadata).parameters)
        for name in CONTRIBUTION_METADATA_FIELDS:
            assert name.replace("-", "_") in accepted, f"{name} has no update_metadata keyword"

    def test_solution_language_is_shown_but_not_a_plain_metadata_edit(self) -> None:
        from codingame_tools.cli.main import (
            CONTRIBUTION_METADATA_FIELDS,
            CONTRIBUTION_SET_FIELDS,
        )
        assert "solution-language" in CONTRIBUTION_SET_FIELDS
        assert "solution-language" not in CONTRIBUTION_METADATA_FIELDS


class TestModuleEntryPointExitCode:
    """`python -m codingame_tools.cli` must report failure the way the `cg` script does.

       `main()` returns an exit code rather than raising SystemExit. The console script generated
       from `[project.scripts]` wraps it in `sys.exit()`; `__main__.py` did not, so every failure
       under `python -m` exited 0 -- invisible to a shell, a script, or CI. Found by a test that
       drove the CLI this way to check something else entirely.
    """

    def test_a_failing_command_exits_non_zero(self, contribution_dir: Path,
                                              tmp_path: Path) -> None:
        result = _cg("contribution", "--contribution-dir", str(contribution_dir), "set",
                     "difficulty", "not-a-difficulty", cwd=tmp_path)
        assert result.returncode != 0, "a refused command must not report success"

    def test_a_successful_command_still_exits_zero(self, contribution_dir: Path,
                                                   tmp_path: Path) -> None:
        result = _cg("contribution", "--contribution-dir", str(contribution_dir), "set", "title",
                     cwd=tmp_path)
        assert result.returncode == 0, result.stderr

    def test_an_unparseable_command_line_exits_non_zero(self, tmp_path: Path) -> None:
        assert _cg("contribution", "set", "--no-such-flag", cwd=tmp_path).returncode != 0


class TestPerFieldSubcommands:
    """Each settable field is its own subcommand, so it can document and type its own value.

       The shape this replaced took an untyped FIELD VALUE pair, which meant `--help` could say
       nothing about what any individual field accepted, and every bad value had to be caught after
       parsing rather than by the parser."""

    @pytest.mark.parametrize("field", ["title", "difficulty", "draft", "ready-for-moderation",
                                       "puzzle-type", "solution-language"])
    def test_every_field_has_its_own_help(self, field: str, tmp_path: Path) -> None:
        result = _cg("contribution", "set", field, "--help", cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert field.split("-")[0] in result.stdout.casefold() or "print the current" in result.stdout

    def test_difficulty_choices_are_enforced_by_the_parser(self, contribution_dir: Path,
                                                           tmp_path: Path) -> None:
        """A rejected value should not reach the manager at all, and should name the valid ones."""
        result = _cg("contribution", "--contribution-dir", str(contribution_dir), "set",
                     "difficulty", "impossible", cwd=tmp_path)
        assert result.returncode != 0
        assert "easy" in result.stderr and "medium" in result.stderr and "hard" in result.stderr

    def test_puzzle_type_choices_are_enforced_by_the_parser(self, contribution_dir: Path,
                                                            tmp_path: Path) -> None:
        result = _cg("contribution", "--contribution-dir", str(contribution_dir), "set",
                     "puzzle-type", "CLASHOFCODE", cwd=tmp_path)
        assert result.returncode != 0
        assert "PUZZLE_INOUT" in result.stderr

    @pytest.mark.parametrize(("spelling", "expected"), [
        ("true", "true"), ("yes", "true"), ("on", "true"), ("1", "true"),
        ("false", "false"), ("no", "false"), ("off", "false"), ("0", "false"),
    ])
    def test_boolean_spellings(self, contribution_dir: Path, tmp_path: Path,
                               spelling: str, expected: str) -> None:
        assert _cg("contribution", "--contribution-dir", str(contribution_dir), "set", "draft",
                   spelling, cwd=tmp_path).returncode == 0
        shown = _cg("contribution", "--contribution-dir", str(contribution_dir), "set", "draft",
                    cwd=tmp_path)
        assert shown.stdout.strip() == expected

    def test_a_bad_boolean_is_rejected_by_the_parser(self, contribution_dir: Path,
                                                     tmp_path: Path) -> None:
        result = _cg("contribution", "--contribution-dir", str(contribution_dir), "set", "draft",
                     "maybe", cwd=tmp_path)
        assert result.returncode != 0
        assert "true or false" in result.stderr

    def test_an_unknown_field_is_rejected(self, contribution_dir: Path, tmp_path: Path) -> None:
        result = _cg("contribution", "--contribution-dir", str(contribution_dir), "set",
                     "no-such-field", "x", cwd=tmp_path)
        assert result.returncode != 0
