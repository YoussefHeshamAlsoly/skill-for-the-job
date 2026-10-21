"""Maintenance pipeline for the *skill-for-the-job* dataset.

The pipeline has four stages:

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

``skills``
    Match a target job title against the dataset and show a histogram of the
    skills the matching jobs ask for.

Usage::

    python main.py                 # update + rework + stats
    python main.py update          # only refresh the raw dataset
    python main.py rework          # only rebuild the normalised dataset
    python main.py stats           # only print a summary
    python main.py skills "software engineer"
"""

from __future__ import annotations

import argparse
import calendar
import contextlib
import importlib
import importlib.util
import io
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

log = logging.getLogger("skill_for_the_job")

# --------------------------------------------------------------------------- #
# Constants                                                                   #
# --------------------------------------------------------------------------- #

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
DOTENV_PATH = BASE_DIR / ".env"
DATASETS_DIR = BASE_DIR / "datasets"
RAW_JOBS_PATH = DATASETS_DIR / "jobs.csv"
REWORKED_JOBS_PATH = DATASETS_DIR / "jobs_reworked.csv"

KAGGLE_DATASET = "chandranshbinjola/job-listings"
KAGGLE_FILE = "jobs.csv"
REFRESH_INTERVAL = timedelta(days=30)

LAST_UPDATED_KEY = "DB_last_updated"
SKILL_SEPARATOR = "|"

# Environment variables used to hand the script over to a better interpreter.
PYTHON_OVERRIDE_ENV = "SKILL_FOR_THE_JOB_PYTHON"
PYTHON_SWITCHED_ENV = "SKILL_FOR_THE_JOB_SWITCHED_FROM"
PYTHON_REEXEC_ENV = "SKILL_FOR_THE_JOB_REEXEC"

# ``python3.14t.exe`` and friends: the free threaded builds.
FREE_THREADED_RE = re.compile(r"^python[\d.]*t$", re.IGNORECASE)

# Columns that are actually needed downstream.  Reading only these keeps the
# 128 MB export from being fully materialised in memory.
RAW_COLUMNS = [
    "title",
    "company",
    "level",
    "category",
    "skills",
    "posted",
    "closed_at",
    "location",
    "remote",
    "url",
]

# Fallback seniority detection, used only for rows whose ``level`` column is
# empty.  Ordered from the most to the least senior so the first hit wins.
# Word boundaries avoid the substring false positives of a naive ``in`` check
# ("sr" no longer matches "resource", "lead" no longer matches "leads").
TITLE_LEVEL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("manager", re.compile(r"\b(?:manager|lead)\b", re.IGNORECASE)),
    ("senior", re.compile(r"\b(?:senior|sr|expert)\b", re.IGNORECASE)),
    ("junior", re.compile(r"\b(?:junior|jr)\b", re.IGNORECASE)),
)


# --------------------------------------------------------------------------- #
# Small helpers                                                               #
# --------------------------------------------------------------------------- #


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically (crash safe, never truncated)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f"{path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _atomic_write_frame(frame: Any, path: Path) -> None:
    """Persist a pandas frame to ``path`` atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f"{path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    os.close(handle)
    try:
        frame.to_csv(tmp_path, index=False)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _format_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024 or unit == "GB":
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} GB"


def _require(module: str, purpose: str) -> Any:
    """Import a dependency and explain how to install it when it is missing.

    Several Python installations can live on the same machine (a virtual
    environment, the Microsoft Store alias, the free threaded build, ...), so
    a package that is "installed" may still be invisible to the interpreter
    that runs the script.  This turns that into a readable message.
    """
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise RuntimeError(
            f"{module} is needed for {purpose} but is not installed for "
            f"{sys.executable}.\n"
            f"\n"
            f"Install the requirements with:\n"
            f'    "{sys.executable}" -m pip install -r requirements.txt\n'
            f"\n"
            f"If pandas is already installed somewhere else, this script is "
            f"running with a different interpreter. Check it with:\n"
            f'    "{sys.executable}" -c "import sys; print(sys.version)"'
        ) from exc


# --------------------------------------------------------------------------- #
# Interpreter selection                                                         #
# --------------------------------------------------------------------------- #


def _is_free_threaded(executable: Path) -> bool:
    """Whether the given executable is a free threaded build (``python3.14t``)."""
    return bool(FREE_THREADED_RE.match(executable.stem))


def _module_available(command: list[str], module: str) -> bool:
    """Whether ``command`` can import ``module`` (without importing it here)."""
    probe = (
        "import importlib.util,sys;"
        f"sys.exit(0 if importlib.util.find_spec({module!r}) else 1)"
    )
    try:
        result = subprocess.run(
            [*command, "-c", probe],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _candidate_interpreters(current: Path) -> list[list[str]]:
    """List the other Python interpreters that could run this script.

    The regular build usually lives next to the free threaded one, so the
    sibling executable is tried first; the ``py`` launcher is used afterwards
    to catch the interpreters that live somewhere else.
    """
    candidates: list[list[str]] = []
    seen: set[str] = set()

    def add(command: list[str]) -> None:
        key = " ".join(command).lower()
        if key not in seen:
            seen.add(key)
            candidates.append(command)

    for name in ("python.exe", "python3.exe"):
        sibling = current.with_name(name)
        if sibling.exists():
            add([str(sibling)])

    on_path = shutil.which("python")
    if on_path and "windowsapps" not in on_path.lower():
        add([on_path])

    launcher = shutil.which("py")
    if launcher:
        try:
            result = subprocess.run(
                [launcher, "-0p"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            result = None
        if result is not None and result.returncode == 0:
            for line in result.stdout.splitlines():
                found = re.search(r"([A-Za-z]:\\.*?\.exe)\s*$", line)
                if found:
                    add([found.group(1)])

    # Never point at the interpreter that is running right now.
    return [
        command
        for command in candidates
        if Path(command[0]).resolve() != current.resolve()
    ]


def _select_interpreter(current: Path) -> list[str] | None:
    """Find another interpreter that has the dependencies installed."""
    fallback: list[str] | None = None
    for command in _candidate_interpreters(current):
        if not _module_available(command, "pandas"):
            continue
        if fallback is None:
            fallback = command
        if not _is_free_threaded(Path(command[0])):
            return command
    return fallback


def _reexec(command: list[str]) -> int:
    """Run this script again with another interpreter and return its exit code."""
    script = Path(__file__).resolve()
    env = dict(os.environ)
    env[PYTHON_REEXEC_ENV] = "1"
    env[PYTHON_SWITCHED_ENV] = sys.executable
    log.info("re-running with %s", command[0])
    try:
        completed = subprocess.run(
            [*command, str(script), *sys.argv[1:]],
            env=env,
            check=False,
        )
    except OSError as exc:
        log.error("could not re-run with %s: %s", command[0], exc)
        return 1
    return completed.returncode


def _use_better_interpreter() -> int | None:
    """Hand the script over to an interpreter that has pandas installed.

    Returns the exit code of the new process, or ``None`` when the current
    interpreter is fine and the script should simply continue.
    """
    if os.getenv(PYTHON_REEXEC_ENV) == "1":
        return None

    current = Path(sys.executable)

    # An explicit override always wins.
    override = os.getenv(PYTHON_OVERRIDE_ENV)
    if override:
        candidate = Path(override)
        if candidate.exists() and candidate.resolve() != current.resolve():
            return _reexec([str(candidate)])
        return None

    try:
        if importlib.util.find_spec("pandas") is not None:
            return None
    except (ImportError, ValueError):
        pass

    command = _select_interpreter(current)
    if command is None:
        return None
    return _reexec(command)


def _import_quietly(module: str) -> Any:
    """Import a module without letting it write to the terminal.

    ``kaggle`` prints a multi-line "Authentication required" banner at import
    time when it cannot find credentials, which is pure noise in the
    diagnostics.
    """
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        return importlib.import_module(module)


def _load_env() -> bool:
    """Load the ``.env`` file when python-dotenv is available.

    Called before ``kaggle`` is imported so the package finds the credentials
    instead of complaining about them.
    """
    try:
        dotenv = importlib.import_module("dotenv")
    except ImportError:
        return False
    try:
        dotenv.load_dotenv(DOTENV_PATH, override=False)
    except OSError:
        return False
    return True


# --------------------------------------------------------------------------- #
# Configuration                                                               #
# --------------------------------------------------------------------------- #


class Config:
    """JSON backed configuration with an in-memory cache and atomic writes."""

    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self.path = path
        self._data: dict[str, Any] | None = None

    def load(self) -> dict[str, Any]:
        """Return the config, reading the file at most once per process."""
        if self._data is None:
            self._data = self._read()
        return self._data

    def _read(self) -> dict[str, Any]:
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            log.info("no config file at %s, starting from an empty config", self.path)
            return {}
        except (json.JSONDecodeError, OSError) as exc:
            log.warning(
                "could not read %s (%s), starting from an empty config", self.path, exc
            )
            return {}
        if not isinstance(data, dict):
            log.warning(
                "%s does not hold a JSON object, starting from an empty config",
                self.path,
            )
            return {}
        return data

    def get(self, key: str, default: Any = None) -> Any:
        return self.load().get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.load()[key] = value
        self.save()

    def save(self) -> None:
        _atomic_write(self.path, json.dumps(self.load(), indent=4, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# Stage 1: update the raw dataset                                             #
# --------------------------------------------------------------------------- #


def last_update_date(config: Config) -> date | None:
    """Return the date the dataset was last refreshed (``None`` if unknown)."""
    raw = config.get(LAST_UPDATED_KEY)
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        log.warning("invalid %s value %r in the config file", LAST_UPDATED_KEY, raw)
        return None


def dataset_is_stale(config: Config) -> bool:
    """``True`` when the local dataset is missing or older than the window."""
    if not RAW_JOBS_PATH.exists():
        return True
    last = last_update_date(config)
    if last is None:
        return True
    return date.today() - last >= REFRESH_INTERVAL


def download_dataset() -> Path:
    """Download the Kaggle dataset and swap it in atomically."""
    # Local imports keep the other stages light (~0.9 s of imports saved).
    _load_env()

    username = os.getenv("KAGGLE_USERNAME")
    key = os.getenv("KAGGLE_KEY")
    token = os.getenv("KAGGLE_API_TOKEN")
    if not token and not (username and key):
        raise RuntimeError(
            "missing Kaggle credentials: set KAGGLE_API_TOKEN or "
            "KAGGLE_USERNAME/KAGGLE_KEY in the .env file next to main.py "
            f"({DOTENV_PATH}) or in the environment"
        )

    kaggle = _require("kaggle", "downloading the dataset")

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
        _format_size(RAW_JOBS_PATH.stat().st_size),
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


# --------------------------------------------------------------------------- #
# Stage 2: rework the raw dataset                                             #
# --------------------------------------------------------------------------- #


def normalise_skills(raw: str) -> str:
    """Normalise a ``;`` separated skill string.

    Skills are trimmed, lowercased, de-duplicated and sorted so that
    ``"Python;  aws;python"`` and ``"aws;python"`` collapse into one value.
    """
    skills = {part.strip().lower() for part in raw.split(";")}
    skills.discard("")
    return SKILL_SEPARATOR.join(sorted(skills))


def _skill_mapping(unique_values: Any) -> dict[str, str]:
    """Build the ``raw skills -> normalised skills`` lookup table.

    Only the distinct skill strings are normalised (22 k instead of 470 k
    rows), which makes the mapping roughly 25x cheaper than a per-row loop.
    """
    return {value: normalise_skills(value) for value in unique_values}


def _derive_levels(frame: Any) -> None:
    """Fill in missing ``level`` values from the job title, in place."""
    pd = _require("pandas", "reading the dataset")

    missing = frame["level"].eq("")
    if not missing.any():
        return

    titles = frame.loc[missing, "title"].str.lower()
    derived = pd.Series("", index=titles.index, dtype=object)
    for label, pattern in TITLE_LEVEL_PATTERNS:
        derived = derived.mask(derived.eq("") & titles.str.contains(pattern), label)

    frame.loc[missing, "level"] = derived
    log.info("derived the level of %d rows from their title", int(missing.sum()))


def _read_raw() -> Any:
    """Read the columns we need out of the raw export."""
    pd = _require("pandas", "reading the dataset")

    return pd.read_csv(
        RAW_JOBS_PATH,
        usecols=RAW_COLUMNS,
        dtype=str,
        na_filter=False,
        engine="c",
    )


def _source_fingerprint() -> str:
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

    fingerprint = _source_fingerprint()
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
    frame = _read_raw()
    log.info(
        "read %s rows from %s in %.2fs",
        f"{len(frame):,}",
        RAW_JOBS_PATH.name,
        time.perf_counter() - started,
    )

    # --- normalise -------------------------------------------------------- #
    frame["skills"] = frame["skills"].map(_skill_mapping(frame["skills"].unique()))
    frame["level"] = frame["level"].str.strip().str.lower()
    _derive_levels(frame)

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
    _atomic_write_frame(frame, REWORKED_JOBS_PATH)
    elapsed = time.perf_counter() - started
    log.info(
        "wrote %s rows -> %s (%s) in %.2fs",
        f"{len(frame):,}",
        REWORKED_JOBS_PATH,
        _format_size(REWORKED_JOBS_PATH.stat().st_size),
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


def _load_dataset_frame() -> tuple[Any, str] | None:
    """Load the reworked dataset (falling back to the raw one).

    Returns the frame together with the separator used inside the skills
    column.
    """
    if not REWORKED_JOBS_PATH.exists() and not RAW_JOBS_PATH.exists():
        return None

    pd = _require("pandas", "reading the dataset")

    if REWORKED_JOBS_PATH.exists():
        path, separator = REWORKED_JOBS_PATH, SKILL_SEPARATOR
    else:
        path, separator = RAW_JOBS_PATH, ";"

    frame = pd.read_csv(path, dtype=str, na_filter=False, engine="c")
    return frame, separator


def preview_rows(limit: int = 20) -> None:
    """Print a sample of the reworked dataset (replaces the old debug prints)."""
    loaded = _load_dataset_frame()
    if loaded is None:
        log.warning("no dataset to preview yet")
        return

    frame, separator = loaded
    for row in frame.head(limit).itertuples(index=False):
        skills = row.skills.replace(separator, ", ") if row.skills else "-"
        print(f"{row.title[:60]:<60} | {row.level or '-':<8} | {skills}")


# --------------------------------------------------------------------------- #
# Stage 3: statistics                                                         #
# --------------------------------------------------------------------------- #


def dataset_stats() -> None:
    """Print a summary of the reworked dataset (or the raw one as a fallback)."""
    loaded = _load_dataset_frame()
    if loaded is None:
        log.warning("no dataset found, run 'python main.py update' first")
        return

    frame, separator = loaded
    path = REWORKED_JOBS_PATH if REWORKED_JOBS_PATH.exists() else RAW_JOBS_PATH

    started = time.perf_counter()
    print(f"\ndataset : {path.name} ({_format_size(path.stat().st_size)})")
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


# --------------------------------------------------------------------------- #
# Stage 4: target job title -> skill histogram                                 #
# --------------------------------------------------------------------------- #


def normalise_text(text: str) -> str:
    """Lowercase, strip punctuation and collapse whitespace."""
    cleaned = re.sub(r"[^0-9a-z]+", " ", str(text).lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def parse_date_bound(text: str) -> tuple[date, date] | None:
    """Parse a date token into the first and the last day it covers.

    ``2026`` covers the whole year, ``2026-03`` the whole month and
    ``2026-03-15`` a single day.
    """
    raw = text.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            parsed = datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
        if fmt == "%Y":
            last = date(parsed.year, 12, 31)
        elif fmt == "%Y-%m":
            last = parsed + timedelta(
                days=calendar.monthrange(parsed.year, parsed.month)[1] - 1
            )
        else:
            last = parsed
        return parsed, last
    return None


def parse_date_range(text: str) -> tuple[date | None, date | None]:
    """Parse a free form date range.

    The range is written with the ``old`` and ``new`` keywords:

    ``old 2026``        from the earliest date of the dataset until 2026
    ``2026-01-01 new``  from the 1st of January 2026 until the newest date
    ``old 2026 2024-01-01 new``  from the 1st of January 2024 until 2026

    Two plain dates (``2024-01-01 2026-12-31``) are read as a range and a
    single plain date as that whole period (day, month or year).

    Returns the ``(oldest, newest)`` bounds; ``None`` means "open".
    """
    tokens = text.split()
    if not tokens:
        raise ValueError("the date range is empty")

    lower: date | None = None
    upper: date | None = None
    consumed: set[int] = set()

    # "old <date>" -> the upper bound.
    for index, token in enumerate(tokens):
        if token.lower() != "old":
            continue
        if index + 1 >= len(tokens):
            raise ValueError('"old" needs a date, e.g. "old 2026"')
        bounds = parse_date_bound(tokens[index + 1])
        if bounds is None:
            raise ValueError(f"{tokens[index + 1]!r} is not a date I understand")
        upper = bounds[1]
        consumed.update({index, index + 1})

    # "<date> new" -> the lower bound.
    for index, token in enumerate(tokens):
        if token.lower() != "new":
            continue
        if index == 0:
            raise ValueError('"new" needs a date, e.g. "2026-01-01 new"')
        bounds = parse_date_bound(tokens[index - 1])
        if bounds is None:
            raise ValueError(f"{tokens[index - 1]!r} is not a date I understand")
        lower = bounds[0]
        consumed.update({index - 1, index})

    # Whatever is left has to be a plain date.
    plain: list[tuple[date, date]] = []
    for index, token in enumerate(tokens):
        if index in consumed:
            continue
        if token.lower() in ("old", "new"):
            continue
        bounds = parse_date_bound(token)
        if bounds is None:
            raise ValueError(f"{token!r} is not a date I understand")
        plain.append(bounds)

    if len(plain) >= 2:
        if lower is None:
            lower = plain[0][0]
        if upper is None:
            upper = plain[1][1]
    elif len(plain) == 1:
        if lower is None and upper is None:
            lower, upper = plain[0]
        elif lower is None:
            lower = plain[0][0]
        elif upper is None:
            upper = plain[0][1]

    if lower and upper and lower > upper:
        raise ValueError(
            f"the range starts on {lower} but ends on {upper}, that cannot work"
        )
    return lower, upper


def filter_date_range(frame: Any, lower: date | None, upper: date | None) -> Any:
    """Keep only the jobs that were posted inside the given range.

    The ``posted`` column is used.  Jobs without a posting date are dropped
    when a bound is given, because they cannot be placed inside the range.
    """
    if lower is None and upper is None:
        return frame

    posted = frame["posted"]
    mask = posted.ne("")
    if lower is not None:
        mask &= posted.ge(lower.isoformat())
    if upper is not None:
        mask &= posted.le(upper.isoformat())
    return frame[mask]


def _word_pattern(word: str) -> re.Pattern[str]:
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

    Four tiers are tried, from the strictest to the loosest, and the first
    one that matches anything wins:

    ``exact phrase``    the whole target appears as a phrase
    ``all keywords``    every keyword of the target appears (any order)
    ``keyword overlap`` at least ``coverage`` of the keywords appear
    ``fuzzy``           the title is close enough to the target (typos)

    ``broad_mask`` holds every row that matches either the exact phrase or
    the keyword overlap, which is what ``--loose`` uses.
    """
    pd = _require("pandas", "matching the titles")

    target_norm = normalise_text(target)
    if not target_norm:
        raise ValueError("the target title is empty")

    titles = titles.map(normalise_text)

    # Tier 1: the exact phrase.
    phrase = titles.str.contains(_word_pattern(target_norm))

    tokens = target_norm.split()

    # How many of the target keywords each title contains.
    hits = pd.Series(0, index=titles.index, dtype="int32")
    for token in tokens:
        hits = hits + titles.str.contains(_word_pattern(token)).astype("int32")

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
    import difflib

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


def _closest_titles(target: str, titles: Any, limit: int = 8) -> list[str]:
    """Suggest similar titles when nothing matched."""
    import difflib

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
        matplotlib = _require("matplotlib", "writing the plot")
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


def describe_date_range(frame: Any, lower: date | None, upper: date | None) -> str:
    """Describe the range the jobs were taken from."""
    posted = frame["posted"]
    posted = posted[posted.ne("")]
    if posted.empty:
        return "no dated jobs"

    oldest = posted.min()
    newest = posted.max()
    start = lower.isoformat() if lower else oldest
    end = upper.isoformat() if upper else newest
    return f"{start} -> {end} (posted)"


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
    loaded = _load_dataset_frame()
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
        loaded = _load_dataset_frame()
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
        suggestions = _closest_titles(target, titles)
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


# --------------------------------------------------------------------------- #
# Diagnostics                                                                  #
# --------------------------------------------------------------------------- #


def doctor() -> int:
    """Report the interpreter, the dependencies and the state of the datasets.

    Useful when a package seems to be "installed" but the script cannot find
    it, which happens when several Python installations share a machine.
    """
    print(f"Interpreter : {sys.executable}")
    print(f"Version     : {sys.version.split()[0]}")
    print(f"Script      : {Path(__file__).resolve()}")
    print(f"Working dir : {Path.cwd()}")

    switched_from = os.getenv(PYTHON_SWITCHED_ENV)
    if switched_from:
        print(
            f"Note        : the script was re-run with this interpreter because "
            f"{switched_from} could not find the dependencies."
        )

    # Load the credentials before importing kaggle, otherwise the package
    # greets us with its authentication banner.
    _load_env()

    print("\nDependencies:")
    missing = 0
    for module, why in (
        ("pandas", "reading the dataset"),
        ("matplotlib", "writing the plot"),
        ("kaggle", "downloading the dataset"),
        ("dotenv", "reading the .env file"),
    ):
        try:
            imported = _import_quietly(module)
        except ImportError:
            missing += 1
            print(f"  {module:<12} MISSING   needed for {why}")
        else:
            version = getattr(imported, "__version__", "unknown")
            print(f"  {module:<12} {version:<10} ok")
    if missing:
        print(
            f"\nInstall what is missing with:\n"
            f'    "{sys.executable}" -m pip install -r requirements.txt'
        )

    print("\nDatasets:")
    for label, path in (
        ("raw", RAW_JOBS_PATH),
        ("reworked", REWORKED_JOBS_PATH),
    ):
        if path.exists():
            print(f"  {label:<10} {path} ({_format_size(path.stat().st_size)})")
        else:
            print(f"  {label:<10} {path} (missing)")

    print("\nConfiguration:")
    config = Config()
    print(f"  file       : {CONFIG_PATH}")
    print(f"  {LAST_UPDATED_KEY:<11}: {config.get(LAST_UPDATED_KEY, 'never')}")
    rework_state = config.get("rework")
    if isinstance(rework_state, dict):
        print(
            f"  reworked   : {rework_state.get('rows', '?')} rows, built {rework_state.get('built_at', '?')}"
        )
    return 1 if missing else 0


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    # ``SUPPRESS`` keeps the flag working both before and after the
    # sub-command without the sub-parser overwriting the global value.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="enable debug logging",
    )

    parser = argparse.ArgumentParser(
        prog="main.py",
        parents=[common],
        description="Maintenance pipeline for the skill-for-the-job dataset.",
    )
    sub = parser.add_subparsers(dest="command")

    update = sub.add_parser(
        "update", parents=[common], help="download the dataset when it is stale"
    )
    update.add_argument(
        "--force", action="store_true", help="download even if the dataset is fresh"
    )

    rework = sub.add_parser(
        "rework", parents=[common], help="rebuild the normalised dataset"
    )
    rework.add_argument(
        "--force", action="store_true", help="rebuild even if the cache is fresh"
    )
    rework.add_argument(
        "--keep-empty",
        action="store_true",
        help="keep the rows that have no skills listed",
    )
    rework.add_argument(
        "--preview", type=int, metavar="N", help="print N reworked rows"
    )

    sub.add_parser("stats", parents=[common], help="summarise the dataset")
    sub.add_parser(
        "doctor",
        parents=[common],
        help="check the interpreter, the dependencies and the datasets",
    )

    skills = sub.add_parser(
        "skills",
        parents=[common],
        help="show the skills associated with a target job title",
    )
    skills.add_argument(
        "title", nargs="?", help='target job title, e.g. "software engineer"'
    )
    skills.add_argument(
        "--level", help="only look at this level (junior, mid, senior, ...)"
    )
    skills.add_argument(
        "--top", type=int, default=20, help="how many skills to show (default: 20)"
    )
    skills.add_argument(
        "--width", type=int, default=36, help="width of the histogram bars"
    )
    skills.add_argument(
        "--coverage",
        type=float,
        default=0.6,
        help="keyword overlap needed for a loose match (default: 0.6)",
    )
    skills.add_argument(
        "--range",
        dest="date_range",
        metavar="RANGE",
        help=(
            'only include jobs posted inside this range, e.g. "old 2026", '
            '"2026-01-01 new" or "old 2026 2024-01-01 new"'
        ),
    )
    skills.add_argument(
        "--titles",
        type=int,
        metavar="N",
        help=(
            "list the job titles that were included and their share "
            "(default: 50 with --loose, off otherwise)"
        ),
    )
    skills.add_argument(
        "--plot", metavar="FILE", help="also write the histogram as a PNG"
    )
    skills.add_argument(
        "--loose",
        action="store_true",
        help="also match the titles that only share most of the keywords",
    )
    skills.add_argument("--json", action="store_true", help="print the result as JSON")
    return parser


def _configure_streams() -> None:
    """Force UTF-8 output so non-ASCII job titles never blow up the console."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - exotic streams
            pass


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_streams()
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    switched = _use_better_interpreter()
    if switched is not None:
        return switched

    config = Config()
    command = args.command or "all"

    if command == "doctor":
        return doctor()

    if command in ("all", "update"):
        update_dataset(config, force=getattr(args, "force", False))

    if command in ("all", "rework"):
        rework_dataset(
            config=config,
            force=getattr(args, "force", False),
            keep_empty=getattr(args, "keep_empty", False),
        )
        if getattr(args, "preview", None):
            preview_rows(args.preview)

    if command in ("all", "stats"):
        dataset_stats()

    if command == "skills":
        target = args.title or input("target job title: ").strip()
        if not target:
            log.error("no target title given")
            return 1
        titles_limit = args.titles
        if titles_limit is None:
            titles_limit = 50 if args.loose else 0
        try:
            found = find_skills(
                target,
                level=args.level,
                top=args.top,
                coverage=args.coverage,
                width=args.width,
                loose=args.loose,
                date_range=args.date_range,
                titles_limit=titles_limit,
                plot_path=Path(args.plot) if args.plot else None,
                as_json=args.json,
            )
        except ValueError as exc:
            log.error("%s", exc)
            return 1
        return 0 if found else 1

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:  # pragma: no cover - interactive use
        log.warning("interrupted")
        sys.exit(130)
    except Exception as exc:  # top level guard: report instead of traceback
        log.error("%s: %s", type(exc).__name__, exc)
        log.debug("traceback", exc_info=True)
        sys.exit(1)
