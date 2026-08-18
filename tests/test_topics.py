"""Tests for the puzzle-topic catalogue: caching it, and resolving what a user typed into one topic.

Resolution is the part worth pinning. It accepts five different kinds of reference and has to pick
exactly one topic, so the interesting failures are silent: a broader match shadowing an exact one
would tag a contribution with the wrong topic and never say so.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from codingame_tools.client.common.protocol.contribution import CgTopic
from codingame_tools.topics import (
    AmbiguousTopicError,
    UnknownTopicError,
    read_cached_catalogue,
    resolve_topic,
    same_topic,
    search_topics,
    topic_label,
    topic_labels,
    write_cached_catalogue,
)


def _topic(handle: str, topic_id: int, *, en: str, fr: str | None = None,
           category: str = "FUNDAMENTALS", puzzle_count: int = 1) -> CgTopic:
    return CgTopic(
            label_map={"1": fr if fr is not None else en, "2": en},
            id=topic_id, handle=handle, category=category, puzzle_count=puzzle_count,
        )


# Modelled on the real catalogue: handles vary in case and shape, and 41 of the 135 real topics
# have a French label that differs from the English one.
CATALOGUE = [
    _topic("graphs", 48, en="Graphs", fr="Graphes", category="INTERMEDIATE", puzzle_count=45),
    _topic("graph-theory", 100, en="Graph theory", category="INTERMEDIATE", puzzle_count=5),
    _topic("dependency-graph", 171, en="Dependency Graph", category="INTERMEDIATE", puzzle_count=4),
    _topic("DFS", 55, en="DFS", fr="Parcours en profondeur", category="ADVANCED", puzzle_count=33),
    _topic("sets", 52, en="Sets", fr="Ensembles", puzzle_count=9),
    _topic("hash-tables", 49, en="Hash tables", fr="Tables de hachage", puzzle_count=6),
]


class TestResolutionOrder:
    """Order matters more than any individual case: each kind of reference must beat the broader
       kinds below it, or an exact input silently resolves to something else."""

    def test_exact_handle(self) -> None:
        assert resolve_topic("dependency-graph", CATALOGUE).id == 171

    def test_handle_ignoring_case(self) -> None:
        assert resolve_topic("dfs", CATALOGUE).handle == "DFS"

    def test_numeric_id(self) -> None:
        assert resolve_topic("171", CATALOGUE).handle == "dependency-graph"

    def test_exact_label(self) -> None:
        assert resolve_topic("Hash tables", CATALOGUE).handle == "hash-tables"

    def test_label_in_any_language_region(self) -> None:
        """A French author types the label they see; region 1 resolves as readily as region 2."""
        assert resolve_topic("Ensembles", CATALOGUE).handle == "sets"
        assert resolve_topic("Parcours en profondeur", CATALOGUE).handle == "DFS"

    def test_an_exact_handle_beats_a_substring_of_other_handles(self) -> None:
        """`graphs` is a substring of nothing here, but `graph-theory` and `dependency-graph` both
           contain `graph` -- so this only passes if the exact-handle pass runs first."""
        assert resolve_topic("graphs", CATALOGUE).id == 48

    def test_an_exact_label_beats_a_substring(self) -> None:
        assert resolve_topic("Graphs", CATALOGUE).id == 48

    def test_unique_substring_resolves(self) -> None:
        assert resolve_topic("depend", CATALOGUE).handle == "dependency-graph"
        assert resolve_topic("hachage", CATALOGUE).handle == "hash-tables"


class TestAmbiguity:
    def test_an_ambiguous_substring_is_refused_with_its_candidates(self) -> None:
        """The candidates are the point: an author who typed something too broad needs the handles
           to retype, not just a rejection."""
        with pytest.raises(AmbiguousTopicError) as exc:
            resolve_topic("graph", CATALOGUE)
        handles = {t.handle for t in exc.value.candidates}
        assert handles == {"graphs", "graph-theory", "dependency-graph"}
        assert "graph-theory" in str(exc.value)

    def test_ambiguity_never_silently_picks_one(self) -> None:
        """The failure this guards against is resolving to the first match instead of refusing."""
        with pytest.raises(AmbiguousTopicError):
            resolve_topic("graph", CATALOGUE)

    def test_unknown_reference_is_refused(self) -> None:
        with pytest.raises(UnknownTopicError):
            resolve_topic("no-such-topic", CATALOGUE)

    def test_empty_reference_is_refused(self) -> None:
        with pytest.raises(UnknownTopicError):
            resolve_topic("   ", CATALOGUE)

    def test_not_found_hint_is_caller_supplied(self) -> None:
        """`remove` resolves against the contribution's own topics, so pointing at the full
           catalogue would send the user to the wrong list."""
        with pytest.raises(UnknownTopicError, match="use `cg contribution topic list`"):
            resolve_topic("zzz", CATALOGUE, not_found_hint="use `cg contribution topic list`")


class TestIdentity:
    def test_same_topic_ignores_puzzle_count(self) -> None:
        """A stored topic and its catalogue entry differ in puzzle_count, which tracks the live
           puzzle population -- so field equality would fail to match a topic with itself."""
        stored = _topic("graphs", 48, en="Graphs", puzzle_count=45)
        fresh = _topic("graphs", 48, en="Graphs", puzzle_count=46)
        assert stored != fresh
        assert same_topic(stored, fresh)

    def test_same_topic_falls_back_to_handle_when_an_id_is_missing(self) -> None:
        assert same_topic(CgTopic(label_map={}, handle="graphs"), _topic("graphs", 48, en="Graphs"))

    def test_different_topics_are_not_the_same(self) -> None:
        assert not same_topic(CATALOGUE[0], CATALOGUE[1])


class TestLabels:
    def test_label_prefers_english(self) -> None:
        assert topic_label(CATALOGUE[3]) == "DFS"

    def test_label_falls_back_when_the_region_is_missing(self) -> None:
        assert topic_label(CgTopic(label_map={"1": "Seulement"}, handle="x")) == "Seulement"

    def test_label_falls_back_to_the_handle_when_there_are_none(self) -> None:
        assert topic_label(CgTopic(label_map={}, handle="bare")) == "bare"

    def test_labels_deduplicate_identical_regions(self) -> None:
        """Most topics spell both regions the same; listing the label twice would be noise."""
        assert topic_labels(CATALOGUE[1]) == ("Graph theory",)
        assert topic_labels(CATALOGUE[3]) == ("Parcours en profondeur", "DFS")


class TestSearch:
    def test_search_matches_handle_or_any_label(self) -> None:
        assert {t.handle for t in search_topics(CATALOGUE, "graph")} == {
            "graphs", "graph-theory", "dependency-graph"}
        assert {t.handle for t in search_topics(CATALOGUE, "hachage")} == {"hash-tables"}

    def test_search_ignores_case(self) -> None:
        assert search_topics(CATALOGUE, "GRAPHS")[0].handle == "graphs"

    def test_category_filter(self) -> None:
        assert {t.handle for t in search_topics(CATALOGUE, category="ADVANCED")} == {"DFS"}

    def test_no_filters_returns_everything_in_order(self) -> None:
        assert search_topics(CATALOGUE) == CATALOGUE


class TestCache:
    def test_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "topics.json"
        write_cached_catalogue(CATALOGUE, path)
        assert [t.handle for t in read_cached_catalogue(path) or []] == [t.handle for t in CATALOGUE]

    def test_missing_cache_is_not_an_error(self, tmp_path: Path) -> None:
        assert read_cached_catalogue(tmp_path / "absent.json") is None

    def test_corrupt_cache_is_treated_as_missing(self, tmp_path: Path) -> None:
        """Derived data: a truncated write should cost a refetch, never an error at the user."""
        path = tmp_path / "topics.json"
        path.write_text("{not json", encoding="utf-8")
        assert read_cached_catalogue(path) is None

    def test_cache_of_the_wrong_shape_is_treated_as_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "topics.json"
        path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
        assert read_cached_catalogue(path) is None

    def test_stale_cache_is_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "topics.json"
        write_cached_catalogue(CATALOGUE, path)
        old = time.time() - 60
        import os
        os.utime(path, (old, old))
        assert read_cached_catalogue(path, max_age=30) is None
        assert read_cached_catalogue(path, max_age=None) is not None
