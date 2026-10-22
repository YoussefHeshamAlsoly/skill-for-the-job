"""Entry point of the *skill-for-the-job* pipeline.

The work lives in the ``skillforjob`` package; this file only wires the
command line to it::

    python main.py                 # update + rework + stats
    python main.py update          # only refresh the raw dataset
    python main.py rework          # only rebuild the normalised dataset
    python main.py stats           # only print a summary
    python main.py doctor          # interpreter, dependencies and datasets
    python main.py skills "software engineer"
"""

from __future__ import annotations

import sys

from skillforjob.cli import run

if __name__ == "__main__":
    sys.exit(run())
