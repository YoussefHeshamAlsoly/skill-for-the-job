"""The Flask application behind the web interface.

The API is small and stateless:

``GET  /api/meta``    what the dataset holds (rows, levels, dates, examples)
``GET  /api/titles``  title suggestions for the search box
``POST /api/skills``  the analysis of one job title
``POST /api/plot``    the same analysis as a PNG
"""

from __future__ import annotations

import re
import webbrowser
from typing import Any

from ..config import LAST_UPDATED_KEY, Config
from ..dataset import load_dataset_frame
from ..dates import parse_date_range
from ..deps import require
from ..logs import log
from ..skills import analyse_skills, build_histogram_plot

MAX_TOP = 100
MAX_TITLES = 500


class DatasetInfo:
    """Cached information about the dataset.

    Reading the frame takes a moment, so it is kept in memory between the
    requests.
    """

    def __init__(self) -> None:
        self._loaded: tuple[Any, str] | None = None
        self._titles: Any = None
        self._meta: dict[str, Any] | None = None

    def load(self) -> tuple[Any, str] | None:
        """The dataset frame together with the separator of the skills column."""
        if self._loaded is None:
            self._loaded = load_dataset_frame()
        return self._loaded

    def frame(self) -> Any:
        loaded = self.load()
        return loaded[0] if loaded else None

    def title_counts(self) -> Any:
        """How many jobs every job title has (a pandas Series)."""
        if self._titles is None:
            frame = self.frame()
            self._titles = frame["title"].value_counts() if frame is not None else None
        return self._titles

    def meta(self) -> dict[str, Any]:
        """Everything the page needs to know about the dataset."""
        if self._meta is None:
            self._meta = self._build_meta()
        return self._meta

    def _build_meta(self) -> dict[str, Any]:
        config = Config()
        loaded = self.load()
        info: dict[str, Any] = {
            "available": loaded is not None,
            "rows": 0,
            "updated": config.get(LAST_UPDATED_KEY, "never"),
            "levels": [],
            "date_min": None,
            "date_max": None,
            "popular_titles": [],
            "top_skills": [],
        }
        if loaded is None:
            return info

        frame, separator = loaded
        info["rows"] = len(frame)
        info["levels"] = sorted(
            {str(level) for level in frame["level"].unique() if level}
        )

        posted = frame["posted"].replace("", None).dropna()
        if not posted.empty:
            info["date_min"] = str(posted.min())
            info["date_max"] = str(posted.max())

        counts = self.title_counts()
        if counts is not None:
            info["popular_titles"] = [str(title) for title in counts.head(40).index]

        if not frame.empty:
            tokens = frame["skills"].str.split(separator).explode().str.strip()
            tokens = tokens[tokens.ne("")]
            info["top_skills"] = [
                str(skill) for skill in tokens.value_counts().head(12).index
            ]

        return info

    def search_titles(self, query: str, limit: int = 12) -> list[dict[str, Any]]:
        """Titles that contain ``query``, the most common ones first."""
        counts = self.title_counts()
        if counts is None:
            return []

        needle = query.strip()
        if not needle:
            hits = counts.head(limit)
        else:
            mask = counts.index.str.lower().str.contains(re.escape(needle.lower()))
            hits = counts[mask].head(limit)
        return [
            {"title": str(title), "jobs": int(count)} for title, count in hits.items()
        ]


def create_app() -> Any:
    """Build the Flask application."""
    flask = require("flask", "running the web interface")

    app = flask.Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    info = DatasetInfo()

    # ------------------------------------------------------------------ #
    # Helpers                                                           #
    # ------------------------------------------------------------------ #

    def read_request() -> dict[str, Any]:
        """Validate the body of a request and return the search parameters."""
        body = flask.request.get_json(silent=True) or {}

        target = str(body.get("target") or "").strip()
        if not target:
            flask.abort(400, description="please enter a job title")

        level = str(body.get("level") or "").strip().lower() or None
        if level:
            known = info.meta()["levels"]
            if level not in known:
                flask.abort(
                    400,
                    description=(
                        f"unknown level '{level}' "
                        f"(available: {', '.join(known) if known else 'none'})"
                    ),
                )

        date_range = str(body.get("date_range") or "").strip() or None
        if date_range:
            try:
                parse_date_range(date_range)
            except ValueError as exc:
                flask.abort(400, description=str(exc))

        try:
            coverage = float(body.get("coverage", 0.6))
            top = int(body.get("top", 20))
            titles_limit = int(body.get("titles_limit", 0))
        except (TypeError, ValueError):
            flask.abort(400, description="the options must be numbers")

        loose = body.get("loose", False)
        if isinstance(loose, str):
            loose = loose.strip().lower() in ("1", "true", "yes", "on")

        return {
            "target": target,
            "level": level,
            "coverage": min(max(coverage, 0.05), 1.0),
            "loose": bool(loose),
            "date_range": date_range,
            "top": min(max(top, 1), MAX_TOP),
            "titles_limit": min(max(titles_limit, 0), MAX_TITLES),
        }

    def run_analysis(params: dict[str, Any]) -> Any:
        report = analyse_skills(
            params["target"],
            level=params["level"],
            coverage=params["coverage"],
            loose=params["loose"],
            date_range=params["date_range"],
        )
        if report is None:
            flask.abort(
                400,
                description="the dataset is not available, run 'python main.py update'",
            )
        return report

    # ------------------------------------------------------------------ #
    # Routes                                                            #
    # ------------------------------------------------------------------ #

    @app.get("/")
    def index() -> Any:
        return flask.render_template("index.html")

    @app.get("/api/meta")
    def api_meta() -> Any:
        return flask.jsonify(info.meta())

    @app.get("/api/titles")
    def api_titles() -> Any:
        query = flask.request.args.get("q", "")
        try:
            limit = int(flask.request.args.get("limit", 12))
        except ValueError:
            limit = 12
        return flask.jsonify(
            {"titles": info.search_titles(query, min(max(limit, 1), 50))}
        )

    @app.post("/api/skills")
    def api_skills() -> Any:
        params = read_request()
        report = run_analysis(params)
        return flask.jsonify(
            report.payload(top=params["top"], titles_limit=params["titles_limit"])
        )

    @app.post("/api/plot")
    def api_plot() -> Any:
        params = read_request()
        report = run_analysis(params)
        if not report.matched:
            flask.abort(404, description="nothing to plot for this title")
        try:
            png = build_histogram_plot(
                report.counts, report.target, report.matched, top=params["top"]
            )
        except RuntimeError as exc:
            flask.abort(500, description=str(exc))
        return flask.Response(
            png,
            mimetype="image/png",
            headers={
                "Content-Disposition": 'attachment; filename="skills.png"',
            },
        )

    # ------------------------------------------------------------------ #
    # Errors                                                            #
    # ------------------------------------------------------------------ #

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(500)
    def handle_error(error: Any) -> Any:
        message = getattr(error, "description", None) or "something went wrong"
        return flask.jsonify({"error": message}), error.code

    return app


def run_server(
    host: str = "127.0.0.1",
    port: int = 5000,
    debug: bool = False,
    open_browser: bool = False,
) -> int:
    """Start the web interface."""
    app = create_app()
    shown = "127.0.0.1" if host in ("0.0.0.0", "") else host
    url = f"http://{shown}:{port}"
    log.info("web interface on %s", url)

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception as exc:  # noqa: BLE001 - the browser is optional
            log.warning("could not open the browser (%s)", exc)

    app.run(host=host, port=port, debug=debug)
    return 0
