"""Paths, constants and the JSON backed configuration.

``config.json`` holds the state of the pipeline (when the dataset was
refreshed, which snapshot the normalised cache was built from).
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .files import atomic_write
from .logs import log

# The project root: the parent of this package.
BASE_DIR = Path(__file__).resolve().parent.parent
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
# raw export from being fully materialised in memory.
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
        atomic_write(self.path, json.dumps(self.load(), indent=4, ensure_ascii=False))


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
