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
    args = parser.parse_args(argv)

    if args.command == "migrate":
        run_migrations()
        return 0

    print(f"команда {args.command} ещё не реализована", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
