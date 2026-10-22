"""Allow ``python -m skillforjob`` to run the pipeline."""

from __future__ import annotations

import sys

from .cli import run

if __name__ == "__main__":
    sys.exit(run())
