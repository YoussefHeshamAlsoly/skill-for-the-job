"""The skill histogram of a matched job title.

``find_skills`` is the entry point of this module: it matches the target
title, counts the skills of the matching jobs and renders the result as an
ASCII histogram, as JSON or as a PNG.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import SKILL_SEPARATOR, Config
from .dataset import load_dataset_frame, rework_dataset
from .dates import describe_date_range, filter_date_range, parse_date_range
from .deps import require
from .logs import log
from .matching import closest_titles, match_titles, normalise_text


def skill_histogram(frame: Any, mask: Any) -> Any:
    """Count how many of the matched jobs list each skill."""
    tokens = frame.loc[mask, "skills"].str.split(SKILL_SEPARATOR).explode().str.strip()
    tokens = tokens[tokens.ne("")]
    return tokens.value_counts()


def render_histogram(counts: Any, total: int, top: int = 20, width: int = 36) -> None:
    """Print an ASCII histogram of the skill counts."""
    if counts.empty:
        print("no skills found for this title")
        return

    selected = counts.head(top)
    largest = int(selected.max())
    for skill, count in selected.items():
        bar = "█" * max(1, round(int(count) / largest * width))
        share = int(count) / total * 100 if total else 0.0
        print(f"  {skill:<24} {bar} {int(count):>7,}  {share:5.1f}%")


def render_title_breakdown(frame: Any, mask: Any, total: int, limit: int = 50) -> None:
    """Print every job title that was included and how much it contributed."""
    breakdown = frame.loc[mask, "title"].value_counts()
    if breakdown.empty:
        return

    print(
        f"\nJob titles that were included ({len(breakdown):,} distinct, "
        f"showing the top {min(limit, len(breakdown))}):\n"
    )
    for title, count in breakdown.head(limit).items():
        share = int(count) / total * 100 if total else 0.0
        print(f"  {str(title)[:58]:<58} {int(count):>6,}  {share:5.1f}%")
    if len(breakdown) > limit:
        print(f"  ... and {len(breakdown) - limit:,} more titles")


def save_histogram_plot(
    counts: Any, path: Path, target: str, total: int, top: int = 20
) -> None:
    """Write the histogram to a PNG file (matplotlib is optional)."""
    try:
        matplotlib = require("matplotlib", "writing the plot")
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except RuntimeError as exc:
        log.warning("%s", exc)
        return

    selected = counts.head(top).sort_values()
    figure, axes = plt.subplots(figsize=(9, max(3, 0.32 * len(selected) + 1.4)))
    axes.barh(selected.index, selected.values, color="#4c78a8")
    axes.set_xlabel("jobs listing the skill")
    axes.set_title(f"Skills for '{target}'  ({total:,} jobs)")
    axes.grid(axis="x", linestyle=":", alpha=0.4)
    for index, (skill, count) in enumerate(selected.items()):
        axes.text(
            int(count) + max(selected.values) * 0.01,
            index,
            f"{int(count) / total * 100:.1f}%",
            va="center",
            fontsize=8,
        )
    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=150)
    plt.close(figure)
    log.info("plot written to %s", path)


def find_skills(
    target: str,
    level: str | None = None,
    top: int = 20,
    coverage: float = 0.6,
    width: int = 36,
    loose: bool = False,
    date_range: str | None = None,
    titles_limit: int = 0,
    plot_path: Path | None = None,
    as_json: bool = False,
) -> int:
    """Show the skills that are associated with ``target``.

    Returns the number of matched jobs.
    """
    loaded = load_dataset_frame()
    if loaded is None:
        log.warning("no dataset found, run 'python main.py update' first")
        return 0

    frame, separator = loaded
    if separator != SKILL_SEPARATOR:
        # The raw export is not normalised yet: build the cache and retry.
        log.info("building the reworked dataset first")
        try:
            rework_dataset(config=Config())
        except (FileNotFoundError, RuntimeError) as exc:
            log.error("could not build the reworked dataset (%s)", exc)
            return 0
        loaded = load_dataset_frame()
        if loaded is None:
            return 0
        frame, separator = loaded

    titles = frame["title"]

    # --- date range ------------------------------------------------------- #
    range_text = ""
    if date_range:
        lower, upper = parse_date_range(date_range)
        before = len(frame)
        frame = filter_date_range(frame, lower, upper)
        titles = frame["title"]
        dropped = before - len(frame)
        range_text = describe_date_range(frame, lower, upper)
        log.info("date range %s dropped %s jobs", range_text, f"{dropped:,}")

    if level:
        wanted = normalise_text(level)
        level_mask = frame["level"].str.lower().eq(wanted)
        if not level_mask.any():
            available = ", ".join(sorted(frame["level"].unique()))
            log.error("unknown level %r (available: %s)", level, available)
            return 0
        titles = titles[level_mask]
        frame = frame[level_mask]

    match = match_titles(titles, target, coverage=coverage)
    if loose:
        mask, tier = match.broad_mask, match.broad_tier
    else:
        mask, tier = match.mask, match.tier

    matched = int(mask.sum())
    if not matched:
        log.error("no job title matches %r", target)
        suggestions = closest_titles(target, titles)
        if suggestions:
            print("\nDid you mean:")
            for suggestion in suggestions:
                print(f"  - {suggestion}")
        return 0

    counts = skill_histogram(frame, mask)
    total = len(frame)

    if as_json:
        payload = {
            "target": target,
            "match": tier,
            "level": level or "any",
            "date_range": range_text or "all dates",
            "matched_jobs": matched,
            "jobs_in_scope": total,
            "levels": {
                str(name): int(count)
                for name, count in frame.loc[mask, "level"].value_counts().items()
            },
            "skills": [
                {
                    "skill": str(skill),
                    "jobs": int(count),
                    "share": round(int(count) / matched * 100, 2),
                }
                for skill, count in counts.head(top).items()
            ],
            "titles": [
                {
                    "title": str(title),
                    "jobs": int(count),
                    "share": round(int(count) / matched * 100, 2),
                }
                for title, count in (
                    frame.loc[mask, "title"]
                    .value_counts()
                    .head(titles_limit if titles_limit else None)
                    .items()
                    if titles_limit
                    else []
                )
            ],
        }
        print(json.dumps(payload, indent=2))
        return matched

    print(f"\nTarget      : {target}")
    print(f"Match       : {tier}")
    if range_text:
        print(f"Date range  : {range_text}")
    print(
        f"Jobs        : {matched:,} of {total:,} ({matched / total:.1%} of the dataset)"
    )
    if level is None:
        mix = frame.loc[mask, "level"].value_counts()
        print(
            "Levels      : "
            + ", ".join(f"{name or 'unknown'} {count:,}" for name, count in mix.items())
        )
    print(f"Skills found: {counts.size:,}\n")
    print(
        f"Top {min(top, len(counts))} skills (share of the {matched:,} matched jobs):\n"
    )
    render_histogram(counts, matched, top=top, width=width)

    if titles_limit:
        render_title_breakdown(frame, mask, matched, limit=titles_limit)

    if not loose:
        broad = int(match.broad_mask.sum())
        if broad > matched * 1.1:
            print(
                f"\n(hint: a looser match would cover {broad:,} jobs, "
                f"run it again with --loose)"
            )

    if plot_path is not None:
        save_histogram_plot(counts, plot_path, target, matched, top=top)

    print()
    return matched
