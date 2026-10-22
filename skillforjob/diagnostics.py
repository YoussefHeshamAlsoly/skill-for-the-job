"""The ``doctor`` command.

It reports the interpreter that runs the script, whether the dependencies can
be imported and the state of the datasets.  It is the first thing to run
when a package seems to be "installed" but the script cannot find it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .config import (
    CONFIG_PATH,
    LAST_UPDATED_KEY,
    PYTHON_SWITCHED_ENV,
    RAW_JOBS_PATH,
    REWORKED_JOBS_PATH,
    Config,
)
from .deps import import_quietly, load_env
from .files import format_size


def doctor() -> int:
    """Report the interpreter, the dependencies and the state of the datasets.

    Returns 1 when a dependency is missing, 0 otherwise.
    """
    print(f"Interpreter : {sys.executable}")
    print(f"Version     : {sys.version.split()[0]}")
    print(f"Script      : {Path(__file__).resolve().parent.parent / 'main.py'}")
    print(f"Working dir : {Path.cwd()}")

    switched_from = os.getenv(PYTHON_SWITCHED_ENV)
    if switched_from:
        print(
            f"Note        : the script was re-run with this interpreter because "
            f"{switched_from} could not find the dependencies."
        )

    # Load the credentials before importing kaggle, otherwise the package
    # greets us with its authentication banner.
    load_env()

    print("\nDependencies:")
    missing = 0
    for module, why in (
        ("pandas", "reading the dataset"),
        ("matplotlib", "writing the plot"),
        ("kaggle", "downloading the dataset"),
        ("dotenv", "reading the .env file"),
        ("flask", "running the web interface"),
    ):
        try:
            imported = import_quietly(module)
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
            print(f"  {label:<10} {path} ({format_size(path.stat().st_size)})")
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
