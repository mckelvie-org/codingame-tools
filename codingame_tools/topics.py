"""The CodinGame puzzle-topic catalogue: fetching it, caching it, and resolving what a user typed.

Topics are the tags a puzzle or contribution carries ("Graphs", "BFS", "Parsing"). The catalogue is
global CodinGame data, identical for every user and every working directory, so it is cached once
per machine rather than per contribution.

Each topic carries a `label_map` of display labels keyed by CodinGame's UI language region -- `"1"`
is French, `"2"` is English -- and 41 of the 135 topics differ between the two. Resolution accepts
a label in either region, so an author working in French can type the label they see.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .client.common.protocol.contribution import CgTopic

if TYPE_CHECKING:
    from .client import CgClient

__all__ = [
    "TOPIC_CATALOGUE_FILE_NAME",
    "CATALOGUE_MAX_AGE_SECONDS",
    "ENGLISH_LABEL_REGION",
    "FRENCH_LABEL_REGION",
    "TopicResolutionError",
    "UnknownTopicError",
    "AmbiguousTopicError",
    "topic_catalogue_path",
    "read_cached_catalogue",
    "write_cached_catalogue",
    "get_topic_catalogue",
    "topic_labels",
    "topic_label",
    "same_topic",
    "resolve_topic",
    "search_topics",
]

TOPIC_CATALOGUE_FILE_NAME = "topics.json"
"""Name of the cached catalogue, in the per-user cache directory."""

CATALOGUE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
"""How long a cached catalogue is used before being refetched. Topics are added rarely, and a
   stale entry is visible rather than silent -- a topic you cannot find is a topic you refresh
   for."""

ENGLISH_LABEL_REGION = "2"
"""`label_map` key for the English display label."""

FRENCH_LABEL_REGION = "1"
"""`label_map` key for the French display label."""


class TopicResolutionError(Exception):
    """A topic reference could not be turned into exactly one topic."""


class UnknownTopicError(TopicResolutionError):
    """Nothing in the catalogue matches the reference."""


@dataclass
class AmbiguousTopicError(TopicResolutionError):
    """The reference matches more than one topic, so it cannot be acted on.

       `candidates` carries every match, so the caller can show the handles to disambiguate with.
    """

    token: str
    candidates: tuple[CgTopic, ...]

    def __str__(self) -> str:
        handles = ", ".join(sorted(t.handle or str(t.id) for t in self.candidates))
        return (f"{self.token!r} matches {len(self.candidates)} topics: {handles}. "
                "Use the handle or the numeric id to pick one.")


def topic_catalogue_path() -> Path:
    """Where the cached catalogue lives: the per-user cache directory, not any working directory.

       Deleting it is always safe -- the next command that needs the catalogue refetches it."""
    from .config.resolver import default_global_cache_dir

    return default_global_cache_dir() / TOPIC_CATALOGUE_FILE_NAME


def read_cached_catalogue(path: Path | None = None,
                          max_age: float | None = CATALOGUE_MAX_AGE_SECONDS) -> list[CgTopic] | None:
    """The cached catalogue, or None if there is no usable one.

    Args:
        path:    Cache file to read. Defaults to `topic_catalogue_path()`.
        max_age: Ignore a cache older than this many seconds. None accepts any age.

    Returns:
        The cached topics, or None if the file is missing, too old, or unreadable. An unreadable
        cache is treated as missing rather than as an error -- it is derived data.
    """
    path = path if path is not None else topic_catalogue_path()
    try:
        if max_age is not None and time.time() - path.stat().st_mtime > max_age:
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, list):
        return None
    try:
        return CgTopic.from_list(raw)
    except Exception:  # noqa: BLE001 -- a corrupt cache is refetched, never raised at the user
        return None


def write_cached_catalogue(topics: list[CgTopic], path: Path | None = None) -> None:
    """Replace the cached catalogue. Creates the cache directory if needed."""
    path = path if path is not None else topic_catalogue_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([t.to_dict() for t in topics], indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


async def get_topic_catalogue(client: CgClient, *, refresh: bool = False,
                              max_age: float | None = CATALOGUE_MAX_AGE_SECONDS,
                              path: Path | None = None) -> list[CgTopic]:
    """The topic catalogue, from cache when it is fresh enough and from the server otherwise.

    Args:
        client:  Client to fetch with, if a fetch is needed.
        refresh: Fetch even if the cache is fresh.
        max_age: Age above which the cache is refetched. None accepts any cached copy.
        path:    Cache file to use. Defaults to `topic_catalogue_path()`.

    Returns:
        Every selectable topic.
    """
    if not refresh:
        cached = read_cached_catalogue(path, max_age)
        if cached is not None:
            return cached
    topics = await client.services.topic.get_all_children_topics_with_puzzle_count()
    write_cached_catalogue(topics, path)
    return topics


def topic_labels(topic: CgTopic) -> tuple[str, ...]:
    """Every distinct display label for a topic, across all UI language regions."""
    seen: dict[str, None] = {}
    for label in (topic.label_map or {}).values():
        if label:
            seen.setdefault(label, None)
    return tuple(seen)


def topic_label(topic: CgTopic, region: str = ENGLISH_LABEL_REGION) -> str:
    """A topic's display label in `region`, falling back to any other region, then its handle."""
    label_map = topic.label_map or {}
    if label_map.get(region):
        return label_map[region]
    for label in label_map.values():
        if label:
            return label
    return topic.handle or str(topic.id or "")


def same_topic(a: CgTopic, b: CgTopic) -> bool:
    """Whether two topics are the same one.

       Compared by `id`, falling back to `handle`, rather than field-by-field: a topic stored on a
       contribution and the same topic in the catalogue differ in `puzzle_count`, which counts the
       live puzzle population and drifts on its own."""
    if a.id is not None and b.id is not None:
        return a.id == b.id
    return a.handle is not None and a.handle == b.handle


def resolve_topic(token: str, catalogue: list[CgTopic], *,
                  not_found_hint: str = "Run `cg topics` to list them, or `cg topics --refresh` "
                                        "if you expect a newly added one.") -> CgTopic:
    """Turn what a user typed into exactly one topic.

       Tried in order, most specific first, so an exact match always wins over a broader one:

       1. exact handle (`dependency-graph`)
       2. handle ignoring case (`DFS`, `dfs`)
       3. numeric id (`171`)
       4. exact display label in any language region, ignoring case (`Hash tables`, `Ensembles`)
       5. substring of a handle or a label, if exactly one topic matches

       Only the last can be ambiguous, and that is what makes it safe: a search-style reference is
       accepted when it identifies one topic and refused when it does not.

    Args:
        token:           What the user typed.
        catalogue:       Topics to resolve against, from `get_topic_catalogue()`.
        not_found_hint:  Appended to the "no match" message. Resolving against a contribution's
                         own topics should point at those rather than at the whole catalogue.

    Returns:
        The single matching topic.

    Raises:
        UnknownTopicError:   Nothing matches.
        AmbiguousTopicError: The reference is a substring of several topics; `candidates` lists
                             them so the caller can print the handles to choose between.
    """
    stripped = token.strip()
    if not stripped:
        raise UnknownTopicError("an empty string is not a topic")
    folded = stripped.casefold()

    for topic in catalogue:
        if topic.handle == stripped:
            return topic
    for topic in catalogue:
        if topic.handle is not None and topic.handle.casefold() == folded:
            return topic
    if stripped.isdigit():
        wanted = int(stripped)
        for topic in catalogue:
            if topic.id == wanted:
                return topic
    for topic in catalogue:
        if any(label.casefold() == folded for label in topic_labels(topic)):
            return topic

    partial = [
        topic for topic in catalogue
        if folded in (topic.handle or "").casefold()
        or any(folded in label.casefold() for label in topic_labels(topic))
    ]
    if len(partial) == 1:
        return partial[0]
    if partial:
        raise AmbiguousTopicError(stripped, tuple(partial))
    raise UnknownTopicError(f"{stripped!r} matches no topic. {not_found_hint}")


def search_topics(catalogue: list[CgTopic], query: str | None = None, *,
                  category: str | None = None) -> list[CgTopic]:
    """Topics whose handle or any display label contains `query`, optionally within one category.

       Both filters ignore case. With neither, the whole catalogue is returned unchanged, in the
       server's own order."""
    results = list(catalogue)
    if category is not None:
        folded_category = category.casefold()
        results = [t for t in results if (t.category or "").casefold() == folded_category]
    if query:
        folded = query.casefold()
        results = [
            t for t in results
            if folded in (t.handle or "").casefold()
            or any(folded in label.casefold() for label in topic_labels(t))
        ]
    return results
