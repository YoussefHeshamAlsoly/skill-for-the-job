# skill-for-the-job

Job listings from Kaggle ([`chandranshbinjola/job-listings`](https://www.kaggle.com/datasets/chandranshbinjola/job-listings)),
normalised into a compact dataset that can be matched against a list of skills.

## Setup

```bash
pip install -r requirements.txt
```

Kaggle credentials go into a `.env` file next to `main.py`:

```
KAGGLE_USERNAME=...
KAGGLE_KEY=...
```

(`KAGGLE_API_TOKEN` is supported as well.)

### Which Python am I running?

Several Python installations can live on the same machine (a virtual
environment, the Microsoft Store alias, the free threaded build, ...).  A
package that is "installed" may still be invisible to the interpreter that
runs the script:

```bash
python main.py doctor
```

```
Interpreter : C:\Program Files\Python314\python.exe
Version     : 3.14.7

Dependencies:
  pandas       3.0.6      ok
  matplotlib   3.11.2     ok
  kaggle       2.2.4      ok
  dotenv       unknown    ok
```

The command prints the interpreter that runs the script, whether the
dependencies can be imported and the state of the datasets.  If a dependency
is missing it also prints the exact `pip install` command for that
interpreter.

#### The script picks the right interpreter by itself

The free threaded build (`python3.14t.exe`) has its own `site-packages` and
is the default of the `py` launcher on Windows, so `py main.py ...` used to
fail with `ModuleNotFoundError: No module named 'pandas'` even though pandas
was installed for the regular 3.14.

The script now notices that and hands over to an interpreter that has the
dependencies:

```bash
py main.py skills "chef"
```

```
INFO re-running with C:\Program Files\Python314\python.exe
```

The same happens for `py main.py doctor`, a double click on `main.py` or an
editor that picked the free threaded build.  The interpreter can be forced
with an environment variable:

```bash
set SKILL_FOR_THE_JOB_PYTHON=C:\Program Files\Python314\python.exe
```

To see what is installed:

```bash
py -0p                   # list every installed interpreter
python -c "import pandas; print(pandas.__file__)"
```

If a dependency is genuinely missing everywhere, install it for the
interpreter that reports it:

```bash
"path\to\python.exe" -m pip install -r requirements.txt
```

A virtual environment is the safest option for a project:

```bash
python -m venv .venv
.venv\Scripts\activate          # . .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
python main.py skills "software engineer"
```

## Usage

```bash
python main.py                # update + rework + stats
python main.py update         # download the dataset when it is older than 30 days
python main.py update --force # download regardless of the refresh window
python main.py rework         # rebuild the normalised dataset
python main.py rework --preview 10
python main.py stats          # summary of the dataset
python main.py doctor         # interpreter, dependencies and dataset status
python main.py skills "software engineer"
python main.py skills "software engineer" --range "2026-01-01 new" --loose
```

## Finding the skills of a job title

The `skills` command takes a target job title and prints a histogram of the
skills that the matching jobs ask for:

```bash
python main.py skills "software engineer"
```

```
Target      : software engineer
Match       : exact phrase
Jobs        : 11,244 of 247,311 (4.5% of the dataset)
Levels      : senior 4,679, mid 3,362, staff+ 2,222, junior 383, ...
Skills found: 103

Top 20 skills (share of the 11,244 matched jobs):

  python                   ████████████████████████████████████   5,008   44.5%
  java                     ███████████████████████   3,153   28.0%
  aws                      █████████████████████   2,973   26.4%
  ...
```

Options:

| Option | What it does |
| --- | --- |
| `--level senior` | only look at one seniority (junior, mid, senior, lead, ...) |
| `--top N` | how many skills to show (default 20) |
| `--range "old 2026"` | only include jobs posted inside a date range |
| `--loose` | also match the titles that only share most of the keywords |
| `--coverage 0.6` | keyword overlap needed for a loose match |
| `--titles N` | list the job titles that were included and their share |
| `--plot skills.png` | also write the histogram as a PNG (needs matplotlib) |
| `--json` | machine readable output |

### Date ranges

The range is written with the `old` and `new` keywords and filters on the
`posted` date of the job:

| Range | Meaning |
| --- | --- |
| `--range "old 2026"` | from the earliest date of the dataset until the end of 2026 |
| `--range "2026-01-01 new"` | from the 1st of January 2026 until the newest date |
| `--range "old 2026 2024-01-01 new"` | from the 1st of January 2024 until the end of 2026 |
| `--range "2024-01-01 2026-12-31"` | two plain dates, read as a range |
| `--range "2026"` | everything posted in 2026 (a year, a month or a day) |

Jobs without a posting date are dropped as soon as a bound is given, because
they cannot be placed inside the range.  Anything that cannot be read as a
date is reported instead of being silently ignored.

### Which titles were included

With `--loose` the script lists every job title that was matched together
with the number of entries it contributed and its share of the matched jobs:

```
Job titles that were included (634 distinct, showing the top 12):

  Data Analyst                                                   72    8.2%
  Senior Data Analyst                                            51    5.8%
  Data Analyst II                                                11    1.3%
  Staff Data Analyst                                              9    1.0%
  ...
  ... and 622 more titles
```

`--titles N` sets how many are shown (50 by default with `--loose`, off
otherwise).

The title is matched in four tiers, the strictest one that matches anything
wins: `exact phrase` → `all keywords` → `keyword overlap` → `fuzzy` (for
typos).  The tier that was used is always shown, and when a looser match
would cover noticeably more jobs the script says so.

## How it works

| Stage | What it does |
| --- | --- |
| `update` | Downloads `datasets/jobs.csv` from Kaggle into a temporary file and swaps it in atomically, so an interrupted download never truncates the local copy. The download is skipped while the dataset is inside the 30 day refresh window. |
| `rework` | Reads only the columns that are needed, normalises the skills (`;` separated → trimmed, lowercased, de-duplicated, sorted, joined with `\|`), fills in missing levels from the job title and writes `datasets/jobs_reworked.csv`. The result is cached and only rebuilt when the raw export changed. |
| `stats` | Prints the level distribution, the most common skills and the posting dates of the dataset. |
| `skills` | Matches a target title against the reworked dataset and histograms the skills of the matching jobs. |

The reworked dataset is roughly a fifth of the raw export and is read back in a
fraction of the time, which matters because the raw file is a few hundred
megabytes.

## Configuration

`config.json` holds the state of the pipeline:

```json
{
    "DB_last_updated": "2026-10-21",
    "rework": {
        "source": "266996508-1792558917000000000",
        "rows": 247311,
        "built_at": "2026-10-21T16:52:50"
    }
}
```

`rework.source` is the fingerprint (size + mtime) of the raw export the cache
was built from, so the cache is rebuilt automatically after a new download.
