"""The web interface.

A small Flask application that exposes the analysis of ``skillforjob`` as a
JSON API and serves a single page that renders it.
"""

from __future__ import annotations

from .app import create_app, run_server

__all__ = ["create_app", "run_server"]
