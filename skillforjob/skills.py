"""The skill histogram of a matched job title.

``analyse_skills`` does the work and returns a :class:`SkillReport`; the
command line (``find_skills``) and the web interface both build on it.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import SKILL_SEPARATOR, Config
from .dataset import load_dataset_frame, rework_dataset
from .dates import describe_date_range, filter_date_range, parse_date_range
from .deps import require
from .logs import log
from .matching import closest_titles, match_titles, normalise_text


@dataclass
class SkillReport:
    """Everything that was found out about one job title."""

    target: str
    tier: str
    level: str | None
    range_text: str
    matched: int
    total: int
    broad_matched: int = 0
    counts: Any = None  # pandas Series: skill -> number of jobs
    titles: Any = None  # pandas Series: job title -> number of jobs
    level_counts: dict[str, int] = field(default_factory=dict)
    suggestions: list[str] = field(default_factory=list)

    @property
    def skills_found(self) -> int:
        """How many different skills the matched jobs ask for."""
        return int(self.counts.size) if self.counts is not None else 0

    def payload(self, top: int = 20, titles_limit: int = 0) -> dict[str, Any]:
        """The report as a JSON friendly dictionary."""
        skill_items = (
            list(self.counts.head(top).items()) if self.counts is not None else []
        )
        title_items = (
            list(self.titles.head(titles_limit).items())
            if self.titles is not None
            else []
        )
        return {
            "target": self.target,
            "match": self.tier,
            "level": self.level or "any",
            "date_range": self.range_text or "all dates",
            "matched_jobs": self.matched,
            "jobs_in_scope": self.total,
            "skills_found": self.skills_found,
            "levels": self.level_counts,
            "skills": [
                {
                    "skill": str(skill),
                    "jobs": int(count),
                    "share": _share(int(count), self.matched),
                }
                for skill, count in skill_items
            ],
            "titles": [
                {
                    "title": str(title),
                    "jobs": int(count),
                    "share": _share(int(count), self.matched),
                }
                for title, count in title_items
            ]
            if titles_limit
            else [],
            "suggestions": self.suggestions,
        }


def _share(count: int, total: int) -> float:
    """The share of ``count`` in ``total``, as a percentage."""
    return round(count / total * 100, 2) if total else 0.0


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


def render_title_breakdown(breakdown: Any, total: int, limit: int = 50) -> None:
    """Print every job title that was included and how much it contributed."""
    if breakdown is None or breakdown.empty:
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


def build_histogram_plot(counts: Any, target: str, total: int, top: int = 20) -> bytes:
    """Render the histogram as PNG bytes (matplotlib is optional)."""
    matplotlib = require("matplotlib", "writing the plot")
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

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
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=150)
    plt.close(figure)
    return buffer.getvalue()


def save_histogram_plot(
    counts: Any, path: Path, target: str, total: int, top: int = 20
) -> None:
    """Write the histogram to a PNG file (matplotlib is optional)."""
    try:
        data = build_histogram_plot(counts, target, total, top)
    except RuntimeError as exc:
        log.warning("%s", exc)
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    log.info("plot written to %s", path)


def analyse_skills(
    target: str,
    level: str | None = None,
    coverage: float = 0.6,
    loose: bool = False,
    date_range: str | None = None,
) -> SkillReport | None:
    """Match ``target`` and collect the skills of the matching jobs.

    Returns ``None`` when there is no dataset to work with.
    """
    loaded = load_dataset_frame()
    if loaded is None:
        log.warning("no dataset found, run 'python main.py update' first")
        return None

    frame, separator = loaded
    if separator != SKILL_SEPARATOR:
        # The raw export is not normalised yet: build the cache and retry.
        log.info("building the reworked dataset first")
        try:
            rework_dataset(config=Config())
        except (FileNotFoundError, RuntimeError) as exc:
            log.error("could not build the reworked dataset (%s)", exc)
            return None
        loaded = load_dataset_frame()
        if loaded is None:
            return None
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
            return None
        titles = titles[level_mask]
        frame = frame[level_mask]

    match = match_titles(titles, target, coverage=coverage)
    if loose:
        mask, tier = match.broad_mask, match.broad_tier
    else:
        mask, tier = match.mask, match.tier

    matched = int(mask.sum())
    total = len(frame)
    broad_matched = int(match.broad_mask.sum())

    if not matched:
        log.error("no job title matches %r", target)
        return SkillReport(
            target=target,
            tier=tier,
            level=level,
            range_text=range_text,
            matched=0,
            total=total,
            broad_matched=broad_matched,
            suggestions=closest_titles(target, titles),
        )

    return SkillReport(
        target=target,
        tier=tier,
        level=level,
        range_text=range_text,
        matched=matched,
        total=total,
        broad_matched=broad_matched,
        counts=skill_histogram(frame, mask),
        titles=frame.loc[mask, "title"].value_counts(),
        level_counts={
            str(name): int(count)
            for name, count in frame.loc[mask, "level"].value_counts().items()
        },
    )


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
    report = analyse_skills(
        target,
        level=level,
        coverage=coverage,
        loose=loose,
        date_range=date_range,
    )
    if report is None:
        return 0

    matched = report.matched
    if not matched:
        if report.suggestions:
            print("\nDid you mean:")
            for suggestion in report.suggestions:
                print(f"  - {suggestion}")
        return 0

    counts = report.counts
    total = report.total

    if as_json:
        print(json.dumps(report.payload(top=top, titles_limit=titles_limit), indent=2))
        return matched

    print(f"\nTarget      : {report.target}")
    print(f"Match       : {report.tier}")
    if report.range_text:
        print(f"Date range  : {report.range_text}")
    print(
        f"Jobs        : {matched:,} of {total:,} ({matched / total:.1%} of the dataset)"
    )
    if level is None:
        print(
            "Levels      : "
            + ", ".join(
                f"{name or 'unknown'} {count:,}"
                for name, count in report.level_counts.items()
            )
        )
    print(f"Skills found: {report.skills_found:,}\n")
    print(
        f"Top {min(top, len(counts))} skills (share of the {matched:,} matched jobs):\n"
    )
    render_histogram(counts, matched, top=top, width=width)

    if titles_limit:
        render_title_breakdown(report.titles, matched, limit=titles_limit)

    if not loose:
        broad = report.broad_matched
        if broad > matched * 1.1:
            print(
                f"\n(hint: a looser match would cover {broad:,} jobs, "
                f"run it again with --loose)"
            )

    if plot_path is not None:
        save_histogram_plot(counts, plot_path, report.target, matched, top=top)

    print()
    return matched
