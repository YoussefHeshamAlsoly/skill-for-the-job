"""Matching a target job title against the dataset.

Four tiers are tried, from the strictest to the loosest, and the first one
that matches anything wins:

``exact phrase``    the whole target appears as a phrase
``all keywords``    every keyword of the target appears (any order)
``keyword overlap`` at least ``coverage`` of the keywords appear
``fuzzy``           the title is close enough to the target (typos)
"""

from __future__ import annotations

import difflib
import math
import re
from typing import Any, NamedTuple

from .deps import require


def normalise_text(text: str) -> str:
    """Lowercase, strip punctuation and collapse whitespace."""
    cleaned = re.sub(r"[^0-9a-z]+", " ", str(text).lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def word_pattern(word: str) -> re.Pattern[str]:
    """Regex matching ``word`` as a whole word (never inside another word)."""
    return re.compile(rf"(?<!\w){re.escape(word)}(?!\w)")


class TitleMatch(NamedTuple):
    """The result of matching a target title against the dataset."""

    mask: Any  # the rows that match the strictest tier
    tier: str  # which tier produced that mask
    broad_mask: Any  # the rows matching the strict *and* the loose tiers
    broad_tier: str


def match_titles(
    titles: Any,
    target: str,
    coverage: float = 0.6,
) -> TitleMatch:
    """Find the rows whose job title matches ``target``.

    The titles are normalised first (lowercase, punctuation removed), so
    "Senior Software-Engineer" and "senior software engineer" are the same.

    ``broad_mask`` holds every row that matches either the exact phrase or
    the keyword overlap, which is what ``--loose`` uses.
    """
    pd = require("pandas", "matching the titles")

    target_norm = normalise_text(target)
    if not target_norm:
        raise ValueError("the target title is empty")

    titles = titles.map(normalise_text)

    # Tier 1: the exact phrase.
    phrase = titles.str.contains(word_pattern(target_norm))

    tokens = target_norm.split()

    # How many of the target keywords each title contains.
    hits = pd.Series(0, index=titles.index, dtype="int32")
    for token in tokens:
        hits = hits + titles.str.contains(word_pattern(token)).astype("int32")

    # Tier 2: every keyword is present.
    all_keywords = hits == len(tokens)
    # Tier 3: most of the keywords are present.
    needed = max(1, math.ceil(coverage * len(tokens)))
    overlap = hits >= needed

    broad_mask = phrase | overlap
    broad_tier = "exact phrase + keyword overlap"

    if phrase.any():
        return TitleMatch(phrase, "exact phrase", broad_mask, broad_tier)
    if all_keywords.any():
        return TitleMatch(all_keywords, "all keywords", broad_mask, broad_tier)
    if overlap.any():
        label = f"keyword overlap (>= {needed}/{len(tokens)})"
        return TitleMatch(overlap, label, broad_mask, broad_tier)

    # Tier 4: fuzzy match on the distinct titles (typos, small variations).
    close = difflib.get_close_matches(
        target_norm, titles.unique().tolist(), n=25, cutoff=0.7
    )
    # Only keep the candidates that share a keyword with the target, so a
    # completely unrelated title does not match by accident.
    close = [
        candidate for candidate in close if any(token in candidate for token in tokens)
    ]
    if close:
        mask = titles.isin(close)
        return TitleMatch(mask, "fuzzy match", broad_mask, broad_tier)

    empty = pd.Series(False, index=titles.index)
    return TitleMatch(empty, "no match", empty, broad_tier)


def closest_titles(target: str, titles: Any, limit: int = 8) -> list[str]:
    """Suggest similar titles when nothing matched."""
    target_norm = normalise_text(target)
    candidates = titles.map(normalise_text).unique().tolist()
    close = difflib.get_close_matches(target_norm, candidates, n=limit, cutoff=0.4)
    if close:
        return close
    # Nothing close enough: fall back to the titles sharing the most keywords.
    tokens = target_norm.split()
    if not tokens:
        return []
    scored: list[tuple[int, str]] = []
    for candidate in candidates:
        score = sum(1 for token in tokens if token in candidate)
        if score:
            scored.append((score, candidate))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [
        title for score, title in scored[:limit] if score >= max(1, len(tokens) - 1)
    ]
