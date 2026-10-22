"""Crash safe file helpers.

The dataset is written with a temporary file that is moved into place in one
step, so an interrupted run never leaves a truncated file behind.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write(path: Path, text: str) -> None:
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


def atomic_write_frame(frame: Any, path: Path) -> None:
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


def format_size(num_bytes: float) -> str:
    """Render a number of bytes as a short human readable size."""
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024 or unit == "GB":
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} GB"
