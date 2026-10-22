"""The command line interface.

``python main.py`` (or ``python -m skillforjob``) dispatches to one of the
stages:

``update``   refresh the raw dataset from Kaggle
``rework``   rebuild the normalised dataset
``stats``    summarise the dataset
``doctor``   report the interpreter, the dependencies and the datasets
``skills``   show the skills associated with a target job title
``serve``    start the web interface
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import Config
from .dataset import dataset_stats, preview_rows, rework_dataset, update_dataset
from .diagnostics import doctor
from .interpreter import use_better_interpreter
from .logs import log
from .skills import find_skills


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser of every sub-command."""
    # ``SUPPRESS`` keeps the flag working both before and after the
    # sub-command without the sub-parser overwriting the global value.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="enable debug logging",
    )

    parser = argparse.ArgumentParser(
        prog="main.py",
        parents=[common],
        description="Maintenance pipeline for the skill-for-the-job dataset.",
    )
    sub = parser.add_subparsers(dest="command")

    update = sub.add_parser(
        "update", parents=[common], help="download the dataset when it is stale"
    )
    update.add_argument(
        "--force", action="store_true", help="download even if the dataset is fresh"
    )

    rework = sub.add_parser(
        "rework", parents=[common], help="rebuild the normalised dataset"
    )
    rework.add_argument(
        "--force", action="store_true", help="rebuild even if the cache is fresh"
    )
    rework.add_argument(
        "--keep-empty",
        action="store_true",
        help="keep the rows that have no skills listed",
    )
    rework.add_argument(
        "--preview", type=int, metavar="N", help="print N reworked rows"
    )

    sub.add_parser("stats", parents=[common], help="summarise the dataset")
    sub.add_parser(
        "doctor",
        parents=[common],
        help="check the interpreter, the dependencies and the datasets",
    )

    skills = sub.add_parser(
        "skills",
        parents=[common],
        help="show the skills associated with a target job title",
    )
    skills.add_argument(
        "title", nargs="?", help='target job title, e.g. "software engineer"'
    )
    skills.add_argument(
        "--level", help="only look at this level (junior, mid, senior, ...)"
    )
    skills.add_argument(
        "--top", type=int, default=20, help="how many skills to show (default: 20)"
    )
    skills.add_argument(
        "--width", type=int, default=36, help="width of the histogram bars"
    )
    skills.add_argument(
        "--coverage",
        type=float,
        default=0.6,
        help="keyword overlap needed for a loose match (default: 0.6)",
    )
    skills.add_argument(
        "--range",
        dest="date_range",
        metavar="RANGE",
        help=(
            'only include jobs posted inside this range, e.g. "old 2026", '
            '"2026-01-01 new" or "old 2026 2024-01-01 new"'
        ),
    )
    skills.add_argument(
        "--titles",
        type=int,
        metavar="N",
        help=(
            "list the job titles that were included and their share "
            "(default: 50 with --loose, off otherwise)"
        ),
    )
    skills.add_argument(
        "--plot", metavar="FILE", help="also write the histogram as a PNG"
    )
    skills.add_argument(
        "--loose",
        action="store_true",
        help="also match the titles that only share most of the keywords",
    )
    skills.add_argument("--json", action="store_true", help="print the result as JSON")

    serve = sub.add_parser(
        "serve",
        parents=[common],
        help="start the web interface in the browser",
    )
    serve.add_argument(
        "--host",
        default="127.0.0.1",
        help="the address to listen on (default: 127.0.0.1, use 0.0.0.0 for the network)",
    )
    serve.add_argument(
        "--port", type=int, default=5000, help="the port to listen on (default: 5000)"
    )
    serve.add_argument(
        "--debug",
        action="store_true",
        help="run the development server with the debugger and the reloader",
    )
    serve.add_argument(
        "--open",
        dest="open_browser",
        action="store_true",
        help="open the page in the default browser",
    )
    return parser


def configure_streams() -> None:
    """Force UTF-8 output so non-ASCII job titles never blow up the console."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - exotic streams
            pass


def main(argv: list[str] | None = None) -> int:
    """Parse the arguments and run the requested stage."""
    args = build_parser().parse_args(argv)
    configure_streams()
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    switched = use_better_interpreter()
    if switched is not None:
        return switched

    config = Config()
    command = args.command or "all"

    if command == "doctor":
        return doctor()

    if command in ("all", "update"):
        update_dataset(config, force=getattr(args, "force", False))

    if command in ("all", "rework"):
        rework_dataset(
            config=config,
            force=getattr(args, "force", False),
            keep_empty=getattr(args, "keep_empty", False),
        )
        if getattr(args, "preview", None):
            preview_rows(args.preview)

    if command in ("all", "stats"):
        dataset_stats()

    if command == "skills":
        target = args.title or input("target job title: ").strip()
        if not target:
            log.error("no target title given")
            return 1
        titles_limit = args.titles
        if titles_limit is None:
            titles_limit = 50 if args.loose else 0
        try:
            found = find_skills(
                target,
                level=args.level,
                top=args.top,
                coverage=args.coverage,
                width=args.width,
                loose=args.loose,
                date_range=args.date_range,
                titles_limit=titles_limit,
                plot_path=Path(args.plot) if args.plot else None,
                as_json=args.json,
            )
        except ValueError as exc:
            log.error("%s", exc)
            return 1
        return 0 if found else 1

    if command == "serve":
        # Imported here so that the other commands do not need Flask.
        from .web.app import run_server

        return run_server(
            host=args.host,
            port=args.port,
            debug=args.debug,
            open_browser=args.open_browser,
        )

    return 0


def run(argv: list[str] | None = None) -> int:
    """Entry point with the top level error guard."""
    try:
        return main(argv)
    except KeyboardInterrupt:  # pragma: no cover - interactive use
        log.warning("interrupted")
        return 130
    except Exception as exc:  # noqa: BLE001 - the top level guard catches everything
        log.error("%s: %s", type(exc).__name__, exc)
        log.debug("traceback", exc_info=True)
        return 1
