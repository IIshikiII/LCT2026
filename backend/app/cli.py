"""Команды обслуживания: migrate, seed, run-pipeline, train, ingest."""

from __future__ import annotations

import argparse
import sys

from app import logging as app_logging
from app.config import config
from app.migrate import run as run_migrations

COMMANDS = (
    "migrate",
    "seed",
    "create-user",
    "reset-demo",
    "reset-keys",
    "run-pipeline",
    "publish-metrics",
    "load-weather",
    "set-commissioning",
    "train",
    "ingest",
)


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
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="для seed: снести прежнюю синтетику перед посевом",
    )
    parser.add_argument("--username", help="логин для команды create-user")
    parser.add_argument("--full-name", default="", help="имя пользователя для create-user")
    parser.add_argument("--role", help="код роли для create-user")
    parser.add_argument(
        "--scope",
        default=None,
        help="комплекс или район для create-user, по виду области видимости роли",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="пароль для create-user, по умолчанию берётся из переменной SEED_PASSWORD",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=14,
        help="для load-weather: сколько прошедших суток погоды загрузить, не больше 92",
    )
    parser.add_argument(
        "--collector",
        default=None,
        help="для set-commissioning: код объекта из таблицы collector",
    )
    parser.add_argument(
        "--until",
        default=None,
        help="для set-commissioning: дата конца пусконаладки ГГГГ-ММ-ДД, пусто снимает отметку",
    )
    parser.add_argument(
        "--set",
        type=int,
        default=None,
        help="номер набора тестовых учёток для reset-keys, по умолчанию все наборы",
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
        from app.auth.seed import seed_users
        from app.db import engine
        from app.synth.generate import DEFAULT_FACILITY_COUNT, generate, wipe_synthetic

        count = args.facilities if args.facilities is not None else DEFAULT_FACILITY_COUNT
        with engine().begin() as conn:
            if args.fresh:
                # Обычный посев не трогает готовые строки: он вставляет с
                # `on_conflict_do_nothing`. Когда меняется сама раскладка
                # объектов, как при переходе на настоящие трассы округов,
                # старые координаты так и остались бы в базе.
                removed = wipe_synthetic(conn)
                print(f"снято прежней синтетики: {removed} строк")
            seeded = generate(conn, facility_count=count)
            # Учётные записи заводятся после объектов: границы видимости узких
            # ролей берутся из первого коллектора посева.
            users = seed_users(conn)
        print(
            f"посев: {seeded.collector_count} коллекторов, "
            f"{seeded.facility_count} объектов, "
            f"{seeded.alarm_event_count} событий доступа, "
            f"учётные записи {', '.join(users)} с паролем из SEED_PASSWORD"
        )
        return 0

    if args.command == "reset-demo":
        from app.db import engine
        from app.domain.reset import reset_demo

        with engine().begin() as conn:
            done = reset_demo(conn)
        print(
            f"сброс: {done.predictions} прогнозов снова новые, "
            f"{done.orders} заявок вернулись в начало, "
            f"{done.log_entries} записей журнала удалено"
        )
        return 0

    if args.command == "reset-keys":
        from app.auth.seed import reset_keys
        from app.db import engine

        with engine().begin() as conn:
            count = reset_keys(conn, args.set)
        where = f"набора {args.set}" if args.set is not None else "всех наборов"
        print(f"ключи второго фактора сняты у {count} записей {where}")
        return 0

    if args.command == "create-user":
        from app.auth import directory
        from app.auth.roles import require
        from app.db import engine

        if not args.username or not args.role:
            print("нужны --username и --role", file=sys.stderr)
            return 2
        try:
            role = require(args.role)
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 2
        with engine().begin() as conn:
            directory.upsert(
                conn,
                username=args.username,
                full_name=args.full_name or args.username,
                role=role.code,
                password=args.password or config.seed_password,
                scope_value=args.scope,
            )
        print(
            f"учётная запись {args.username}: роль {role.label}, "
            f"область {role.scope_kind} {args.scope or ''}".strip()
        )
        return 0

    if args.command == "publish-metrics":
        from app.db import engine
        from app.meta import active as active_directions
        from app.ml.publish import publish_all

        codes = tuple(direction.code for direction in active_directions())
        with engine().begin() as conn:
            published = publish_all(conn, codes)
        if published:
            print("замеры опубликованы: " + ", ".join(published))
        else:
            print("замеров нет: ни одно направление не положило metrics.json в ARTIFACTS_DIR")
        return 0

    if args.command == "load-weather":
        from app.db import engine
        from app.ingest.weather import load

        with engine().begin() as conn:
            hours = load(conn, args.days)
        print(f"погода Москвы: {hours} часов за {args.days} суток")
        return 0

    if args.command == "set-commissioning":
        from datetime import date

        from app.db import engine
        from app.tables import collector

        if not args.collector:
            print("нужен ключ --collector", file=sys.stderr)
            return 2
        until = date.fromisoformat(args.until) if args.until else None
        with engine().begin() as conn:
            changed = conn.execute(
                collector.update()
                .where(collector.c.code == args.collector)
                .values(commissioning_until=until)
            ).rowcount
        if not changed:
            print(f"объекта {args.collector} нет", file=sys.stderr)
            return 1
        state = f"в пусконаладке до {until}" if until else "отметка пусконаладки снята"
        print(f"объект {args.collector}: {state}")
        return 0

    print(f"команда {args.command} ещё не реализована", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
