"""Allow ``python -m skillforjob.web`` to start the web interface."""

from __future__ import annotations

import sys

from .app import run_server

if __name__ == "__main__":
    sys.exit(run_server())
