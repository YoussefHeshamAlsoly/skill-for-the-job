"""Optional dependency handling.

Several Python installations can live on the same machine (a virtual
environment, the Microsoft Store alias, the free threaded build, ...), so a
package that is "installed" may still be invisible to the interpreter that
runs the script.  These helpers turn that into a readable message.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import sys
from typing import Any

from .config import DOTENV_PATH


def require(module: str, purpose: str) -> Any:
    """Import a dependency and explain how to install it when it is missing."""
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


def import_quietly(module: str) -> Any:
    """Import a module without letting it write to the terminal.

    ``kaggle`` prints a multi-line "Authentication required" banner at import
    time when it cannot find credentials, which is pure noise in the
    diagnostics.
    """
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        return importlib.import_module(module)


def load_env() -> bool:
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
