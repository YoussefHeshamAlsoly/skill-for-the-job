"""Downloading, normalising and summarising the dataset.

Three stages live here:

``update``
    Make sure ``datasets/jobs.csv`` is present and up to date.  The Kaggle
    download is skipped while the local copy is inside the refresh window and
    a failed/interrupted download never leaves a half written file behind.

``rework``
    Normalise the raw export into a compact, cacheable dataset
    (``datasets/jobs_reworked.csv``).  The heavy lifting is vectorised and the
    result is cached, so later runs never re-parse the raw file.

``stats``
    Summarise the reworked dataset (levels, skills, freshness).
"""

from __future__ import annotations

import os
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import (
    DATASETS_DIR,
    DOTENV_PATH,
    KAGGLE_DATASET,
    KAGGLE_FILE,
    LAST_UPDATED_KEY,
    RAW_COLUMNS,
    RAW_JOBS_PATH,
    REFRESH_INTERVAL,
    REWORKED_JOBS_PATH,
    SKILL_SEPARATOR,
    TITLE_LEVEL_PATTERNS,
    Config,
    dataset_is_stale,
)
from .deps import load_env, require
from .files import atomic_write_frame, format_size
from .logs import log


def download_dataset() -> Path:
    """Download the Kaggle dataset and swap it in atomically."""
    # Local imports keep the other stages light (~0.9 s of imports saved).
    load_env()

    username = os.getenv("KAGGLE_USERNAME")
    key = os.getenv("KAGGLE_KEY")
    token = os.getenv("KAGGLE_API_TOKEN")
    if not token and not (username and key):
        raise RuntimeError(
            "missing Kaggle credentials: set KAGGLE_API_TOKEN or "
            "KAGGLE_USERNAME/KAGGLE_KEY in the .env file next to main.py "
            f"({DOTENV_PATH}) or in the environment"
        )

    kaggle = require("kaggle", "downloading the dataset")

    if username and key:
        os.environ["KAGGLE_USERNAME"] = username
        os.environ["KAGGLE_KEY"] = key
    kaggle.api.authenticate()

    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=DATASETS_DIR, prefix=".download-") as tmp_dir:
        kaggle.api.dataset_download_file(
            KAGGLE_DATASET,
            file_name=KAGGLE_FILE,
            path=tmp_dir,
            force=True,
        )
        matches = list(Path(tmp_dir).rglob(KAGGLE_FILE))
        if not matches:
            raise RuntimeError(f"kaggle did not produce {KAGGLE_FILE}")
        # Replace in one step: an interrupted download can never truncate the
        # dataset that is already on disk.
        os.replace(matches[0], RAW_JOBS_PATH)

    log.info(
        "downloaded %s -> %s (%s)",
        KAGGLE_FILE,
        RAW_JOBS_PATH,
        format_size(RAW_JOBS_PATH.stat().st_size),
    )
    return RAW_JOBS_PATH


def update_dataset(config: Config, force: bool = False) -> bool:
    """Refresh the raw dataset when it is stale.  Returns ``True`` if downloaded."""
    if not force and not dataset_is_stale(config):
        log.info(
            "dataset was refreshed less than %d days ago, nothing to do",
            REFRESH_INTERVAL.days,
        )
        return False

    download_dataset()
    config.set(LAST_UPDATED_KEY, date.today().isoformat())
    log.info("recorded %s = %s", LAST_UPDATED_KEY, date.today().isoformat())
    return True


def normalise_skills(raw: str) -> str:
    """Normalise a ``;`` separated skill string.

    Skills are trimmed, lowercased, de-duplicated and sorted so that
    ``"Python;  aws;python"`` and ``"aws;python"`` collapse into one value.
    """
    skills = {part.strip().lower() for part in raw.split(";")}
    skills.discard("")
    return SKILL_SEPARATOR.join(sorted(skills))


def skill_mapping(unique_values: Any) -> dict[str, str]:
    """Build the ``raw skills -> normalised skills`` lookup table.

    Only the distinct skill strings are normalised (22 k instead of 470 k
    rows), which makes the mapping roughly 25x cheaper than a per-row loop.
    """
    return {value: normalise_skills(value) for value in unique_values}


def derive_levels(frame: Any) -> None:
    """Fill in missing ``level`` values from the job title, in place."""
    pd = require("pandas", "reading the dataset")

    missing = frame["level"].eq("")
    if not missing.any():
        return

    titles = frame.loc[missing, "title"].str.lower()
    derived = pd.Series("", index=titles.index, dtype=object)
    for label, pattern in TITLE_LEVEL_PATTERNS:
        derived = derived.mask(derived.eq("") & titles.str.contains(pattern), label)

    frame.loc[missing, "level"] = derived
    log.info("derived the level of %d rows from their title", int(missing.sum()))


def read_raw() -> Any:
    """Read the columns we need out of the raw export."""
    pd = require("pandas", "reading the dataset")

    return pd.read_csv(
        RAW_JOBS_PATH,
        usecols=RAW_COLUMNS,
        dtype=str,
        na_filter=False,
        engine="c",
    )


def source_fingerprint() -> str:
    """Fingerprint of the raw export (size + mtime).

    The mtime alone is not enough: Kaggle stamps the downloaded file with the
    timestamp of the remote file, so a fresh download can look older than the
    cache that was built from the previous snapshot.
    """
    stat = RAW_JOBS_PATH.stat()
    return f"{stat.st_size}-{stat.st_mtime_ns}"


def rework_dataset(
    config: Config | None = None,
    force: bool = False,
    keep_empty: bool = False,
) -> Path:
    """Build the normalised dataset from the raw export.

    The result is cached and only rebuilt when the raw export changed, so
    repeated runs cost a couple of milliseconds instead of a full re-parse.
    """
    config = config or Config()
    if not RAW_JOBS_PATH.exists():
        raise FileNotFoundError(f"raw dataset not found at {RAW_JOBS_PATH}")

    fingerprint = source_fingerprint()
    cached = config.get("rework") or {}
    if (
        not force
        and REWORKED_JOBS_PATH.exists()
        and isinstance(cached, dict)
        and cached.get("source") == fingerprint
    ):
        log.info("reworked dataset is up to date, skipping (use --force to rebuild)")
        return REWORKED_JOBS_PATH

    started = time.perf_counter()
    frame = read_raw()
    log.info(
        "read %s rows from %s in %.2fs",
        f"{len(frame):,}",
        RAW_JOBS_PATH.name,
        time.perf_counter() - started,
    )

    # --- normalise -------------------------------------------------------- #
    frame["skills"] = frame["skills"].map(skill_mapping(frame["skills"].unique()))
    frame["level"] = frame["level"].str.strip().str.lower()
    derive_levels(frame)

    remote = frame["remote"].str.strip().str.lower()
    frame["remote"] = (
        remote.map({"true": True, "false": False}).fillna(False).astype(bool)
    )

    # --- prune ------------------------------------------------------------ #
    if not keep_empty:
        before = len(frame)
        frame = frame[frame["skills"].ne("")]
        log.info("dropped %s rows without any skill", f"{before - len(frame):,}")

    frame = frame.sort_values("posted", ascending=False, kind="stable")
    frame = frame.reset_index(drop=True)

    # --- persist ---------------------------------------------------------- #
    atomic_write_frame(frame, REWORKED_JOBS_PATH)
    elapsed = time.perf_counter() - started
    log.info(
        "wrote %s rows -> %s (%s) in %.2fs",
        f"{len(frame):,}",
        REWORKED_JOBS_PATH,
        format_size(REWORKED_JOBS_PATH.stat().st_size),
        elapsed,
    )
    config.set(
        "rework",
        {
            "source": fingerprint,
            "rows": len(frame),
            "built_at": datetime.now().isoformat(timespec="seconds"),
        },
    )
    return REWORKED_JOBS_PATH


def load_dataset_frame() -> tuple[Any, str] | None:
    """Load the reworked dataset (falling back to the raw one).

    Returns the frame together with the separator used inside the skills
    column.
    """
    if not REWORKED_JOBS_PATH.exists() and not RAW_JOBS_PATH.exists():
        return None

    pd = require("pandas", "reading the dataset")

    if REWORKED_JOBS_PATH.exists():
        path, separator = REWORKED_JOBS_PATH, SKILL_SEPARATOR
    else:
        path, separator = RAW_JOBS_PATH, ";"

    frame = pd.read_csv(path, dtype=str, na_filter=False, engine="c")
    return frame, separator


def preview_rows(limit: int = 20) -> None:
    """Print a sample of the reworked dataset (replaces the old debug prints)."""
    loaded = load_dataset_frame()
    if loaded is None:
        log.warning("no dataset to preview yet")
        return

    frame, separator = loaded
    for row in frame.head(limit).itertuples(index=False):
        skills = row.skills.replace(separator, ", ") if row.skills else "-"
        print(f"{row.title[:60]:<60} | {row.level or '-':<8} | {skills}")


def dataset_stats() -> None:
    """Print a summary of the reworked dataset (or the raw one as a fallback)."""
    loaded = load_dataset_frame()
    if loaded is None:
        log.warning("no dataset found, run 'python main.py update' first")
        return

    frame, separator = loaded
    path = REWORKED_JOBS_PATH if REWORKED_JOBS_PATH.exists() else RAW_JOBS_PATH

    started = time.perf_counter()
    print(f"\ndataset : {path.name} ({format_size(path.stat().st_size)})")
    print(f"rows    : {len(frame):,}")
    print(f"columns : {', '.join(frame.columns)}")

    if "level" in frame.columns and not frame.empty:
        print("\nlevels:")
        for level, count in frame["level"].value_counts().items():
            label = level or "(unknown)"
            print(f"  {label:<12} {count:>9,}  ({count / len(frame):.1%})")

    if "skills" in frame.columns and not frame.empty:
        tokens = frame["skills"].str.split(separator).explode().str.strip()
        tokens = tokens[tokens.ne("")]
        print(f"\nunique skills: {tokens.nunique():,}")
        print("top skills:")
        for skill, count in tokens.value_counts().head(15).items():
            print(f"  {skill:<28} {count:>9,}")

    if "posted" in frame.columns and not frame.empty:
        posted = frame["posted"].replace("", None).dropna()
        if not posted.empty:
            print(f"\nposted between {posted.min()} and {posted.max()}")
            recent = posted.ge((date.today() - timedelta(days=30)).isoformat()).sum()
            print(f"posted in the last 30 days: {recent:,}")

    print(f"\n(summary computed in {time.perf_counter() - started:.2f}s)\n")
