"""Команды обслуживания: migrate, seed, run-pipeline, train, ingest."""

from __future__ import annotations

import argparse
import sys

from app import logging as app_logging
from app.config import config
from app.migrate import run as run_migrations

COMMANDS = ("migrate", "seed", "run-pipeline", "train", "ingest")


def main(argv: list[str] | None = None) -> int:
    app_logging.setup(config.log_level)
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument(
        "--facilities",
        type=int,
        default=None,
        help="число объектов для команды seed, по умолчанию число из app.synth.generate",
    )
    args = parser.parse_args(argv)

    if args.command == "migrate":
        run_migrations()
        return 0

    if args.command == "run-pipeline":
        from app.db import engine
        from app.pipeline.run import run as run_pipeline

        result = run_pipeline(engine())
        print(
            f"прогон {result.run_id}: {result.prediction_count} прогнозов, "
            f"{result.order_count} заявок, {result.duration_ms} мс"
        )
        return 0

    if args.command == "seed":
        from app.db import engine
        from app.synth.generate import DEFAULT_FACILITY_COUNT, generate

        count = args.facilities if args.facilities is not None else DEFAULT_FACILITY_COUNT
        with engine().begin() as conn:
            seeded = generate(conn, facility_count=count)
        print(
            f"посев: {seeded.collector_count} коллекторов, "
            f"{seeded.facility_count} объектов, "
            f"{seeded.alarm_event_count} событий доступа"
        )
        return 0

    print(f"команда {args.command} ещё не реализована", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
