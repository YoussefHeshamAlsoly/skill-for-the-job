"""Finding the Python interpreter that has the dependencies installed.

The free threaded builds (``python3.14t.exe``) have their own
``site-packages`` and are the default of the ``py`` launcher on Windows, so
``py main.py ...`` used to fail with ``ModuleNotFoundError: No module named
'pandas'`` even though pandas was installed for the regular 3.14.

This module finds an interpreter that has the dependencies and hands the
script over to it.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .config import (
    FREE_THREADED_RE,
    PYTHON_OVERRIDE_ENV,
    PYTHON_REEXEC_ENV,
    PYTHON_SWITCHED_ENV,
)
from .logs import log


def script_path() -> Path:
    """The entry point script that should be re-run."""
    candidate = Path(sys.argv[0]).resolve()
    if candidate.exists():
        return candidate
    return Path(__file__).resolve().parent.parent / "main.py"


def is_free_threaded(executable: Path) -> bool:
    """Whether the given executable is a free threaded build (``python3.14t``)."""
    return bool(FREE_THREADED_RE.match(executable.stem))


def module_available(command: list[str], module: str) -> bool:
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


def candidate_interpreters(current: Path) -> list[list[str]]:
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


def select_interpreter(current: Path) -> list[str] | None:
    """Find another interpreter that has the dependencies installed."""
    fallback: list[str] | None = None
    for command in candidate_interpreters(current):
        if not module_available(command, "pandas"):
            continue
        if fallback is None:
            fallback = command
        if not is_free_threaded(Path(command[0])):
            return command
    return fallback


def reexec(command: list[str]) -> int:
    """Run the script again with another interpreter and return its exit code."""
    script = script_path()
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


def use_better_interpreter() -> int | None:
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
            return reexec([str(candidate)])
        return None

    try:
        if importlib.util.find_spec("pandas") is not None:
            return None
    except (ImportError, ValueError):
        pass

    command = select_interpreter(current)
    if command is None:
        return None
    return reexec(command)
