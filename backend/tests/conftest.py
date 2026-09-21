"""Фикстура данных для тестов эндпоинтов.

Это не имитация реальных выгрузок. Реальных данных нет, и угадать их нельзя.
Фикстура закрывает ветви кода: каждый уровень риска, каждый статус обеих
сущностей, объект с двумя прогнозами, направление без прогнозов и направление
с битыми блоками.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app import migrate
from app.db import engine
from app.tables import (
    action_log,
    collector,
    facility,
    model_metric,
    pipeline_run,
    prediction,
    work_order,
)

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)

FACILITIES = [
    # id, коллектор, район, адрес, lon, lat
    ("F-1", "K-CAO-1", "CAO", "Тверская улица, 1", 37.61, 55.75),
    ("F-2", "K-CAO-1", "CAO", "Арбат, 12", 37.59, 55.75),
    ("F-3", "K-SAO-1", "SAO", "Ленинградское шоссе, 40", 37.50, 55.85),
]

# probability подобрана под пороги уровней: 0.3, 0.55, 0.78.
PREDICTIONS = [
    # id, направление, объект, вероятность, уровень, статус, сдвиг в часах
    ("P-1", "SENSOR_FAILURE", "F-1", 0.91, "CRITICAL", "NEW", 0),
    ("P-2", "SENSOR_FAILURE", "F-1", 0.40, "MEDIUM", "IN_REVIEW", -1),
    ("P-3", "FIRE_RISK", "F-2", 0.60, "HIGH", "ORDER_CONFIRMED", -30),
    ("P-4", "UNAUTHORIZED_ACCESS", "F-3", 0.10, "LOW", "REJECTED", -60),
    ("P-5", "FIRE_RISK", "F-3", 0.80, "CRITICAL", "CLOSED", -80),
]

GOOD_BLOCKS = [
    {"type": "factors", "title": "Факторы", "data": {"items": [{"label": "Шум", "weight": 0.6}]}},
    {"type": "timeseries", "title": "Динамика", "data": {"series": []}},
]

# Направление UNAUTHORIZED_ACCESS отдаёт мусор намеренно. Спецификация §13.
BROKEN_BLOCKS = [
    {"type": "factors"},
    None,
    "строка вместо блока",
    {"type": "experimental_heatmap", "title": "Неизвестный тип", "data": {"cells": []}},
]

# Прогон конвейера. Второй идёт позже первого и не закончился: здоровье
# считается по последнему успешному, а не по последнему начатому.
RUNS = [
    # id, статус, сдвиг начала в часах, длительность
    (1, "DONE", -1, 42_000),
    (2, "RUNNING", 0, None),
]

# Оценки моделей. «Несанкционированный доступ» оценки не имеет, и это норма:
# направление отдаёт правило, а не обученную модель.
METRICS = [
    # направление, precision, recall, сдвиг оценки в часах
    ("SENSOR_FAILURE", 0.62, 0.44, -48),
    ("SENSOR_FAILURE", 0.81, 0.63, -2),
    ("FIRE_RISK", 0.76, 0.58, -2),
]

ORDERS = [
    # id, номер, прогноз, объект, статус, сдвиг срока в часах, итог
    ("O-1", "2026-0001", "P-1", "F-1", "AUTO_CREATED", 12, None),
    ("O-2", "2026-0002", "P-3", "F-2", "CONFIRMED", 24, None),
    ("O-3", "2026-0003", "P-4", "F-3", "IN_PROGRESS", 48, None),
    (
        "O-4",
        "2026-0004",
        "P-5",
        "F-3",
        "DONE",
        -6,
        {
            "actualCause": "HOT_WORKS",
            "predictionConfirmed": True,
            "comment": "подтвердилось при осмотре",
            "closedAt": "2026-09-09T10:00:00Z",
        },
    ),
]


# Порядок чистки: сначала ссылающиеся таблицы.
TABLES = (action_log, work_order, prediction, model_metric, pipeline_run, facility, collector)

# Список таблиц для чистки читается из базы, а не пишется руками. Рукописный
# список уже разошёлся со схемой: в нём не было `alarm_event`, и строки
# конвейера оставались в базе. Дальше `DELETE FROM facility` проверял внешние
# ключи по секционированной таблице, и чистка занимала 130 секунд на тест.
_MUTABLE_TABLES_SQL = text("""
    SELECT c.relname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p')
      AND NOT c.relispartition
      AND c.relname <> 'schema_migration'
    ORDER BY c.relname
""")


# Хосты, на которых тестам разрешено опустошать базу. Петля это машина
# разработчика, `db` это служба compose. Всё остальное может оказаться чужой
# или рабочей базой, а `TRUNCATE ... CASCADE` снимает вообще все таблицы.
LOCAL_HOSTS = frozenset({None, "", "localhost", "127.0.0.1", "::1", "db"})

# Суффикс имени тестовой базы. Адрес выводит корневой `conftest.py`, см. ADR 0005.
TEST_SUFFIX = "_test"


def check_database(host: str | None, name: str | None) -> str | None:
    """Отдаёт причину отказа или `None`, если чистить базу разрешено.

    Два условия. Хост обязан быть локальным, иначе база может оказаться чужой.
    Имя обязано кончаться на `_test`, иначе база может оказаться рабочей.

    Одного хоста мало. Рабочая база `arm` лежит на том же `127.0.0.1`, что и
    тестовая `arm_test`, поэтому хост её не отличает. Ворота снимали рабочую
    базу целиком именно так.
    """
    if host not in LOCAL_HOSTS:
        return (
            f"база не локальная: хост {host!r}. "
            f"Разрешены {', '.join(sorted(h for h in LOCAL_HOSTS if h))}."
        )
    if not (name or "").endswith(TEST_SUFFIX):
        return f"имя базы {name!r} не кончается на {TEST_SUFFIX!r}. Это похоже на рабочую базу."
    return None


def _require_test_database() -> None:
    """Запрещает чистку, если база не тестовая.

    Раньше чистка перечисляла семь таблиц руками и могла испортить меньше.
    Теперь она снимает всё, поэтому ошибка в адресе стоила бы рабочих данных.
    Проверка стоит наносекунды и закрывает этот случай навсегда.
    """
    url = engine().url
    reason = check_database(url.host, url.database)
    if reason is not None:
        pytest.exit(
            f"Тесты чистят базу целиком и работают только с тестовой базой. {reason} "
            f"Назовите адрес переменной TEST_DATABASE_URL.",
            returncode=2,
        )


def reset_database(conn: object) -> None:
    """Опустошает все таблицы данных, кроме журнала миграций.

    `TRUNCATE` не проверяет внешние ключи построчно и снимает сразу все
    секции, поэтому его время не зависит от числа строк. `CASCADE` снимает
    ссылающиеся таблицы, поэтому порядок перечисления не важен.

    Схему это не трогает: `schema_migration` остаётся на месте, и повторный
    `migrate.run()` ничего не переприменяет.
    """
    _require_test_database()
    names = conn.execute(_MUTABLE_TABLES_SQL).scalars().all()  # type: ignore[attr-defined]
    if not names:
        return
    joined = ", ".join(f'"{name}"' for name in names)
    conn.execute(text(f"TRUNCATE {joined} RESTART IDENTITY CASCADE"))  # type: ignore[attr-defined]


def _insert(conn: object) -> None:
    conn.execute(  # type: ignore[attr-defined]
        collector.insert(),
        [
            {
                "code": "K-CAO-1",
                "label": "Коллектор Центральный 1",
                "district": "CAO",
                "line": [[37.58, 55.74], [37.61, 55.75]],
            }
        ],
    )
    conn.execute(  # type: ignore[attr-defined]
        facility.insert(),
        [
            {
                "id": code,
                "collector": collector_code,
                "section": "У-1",
                "chamber": "К-2",
                "device": None,
                "district": district,
                "address": address,
                "lat": lat,
                "lon": lon,
                "facility_type": "chamber",
                "is_active": True,
            }
            for code, collector_code, district, address, lon, lat in FACILITIES
        ],
    )
    conn.execute(  # type: ignore[attr-defined]
        prediction.insert(),
        [
            {
                "id": code,
                "direction": direction,
                "facility_id": facility_id,
                "probability": probability,
                "level": level,
                "horizon_hours": 48,
                "computed_at": NOW + timedelta(hours=shift),
                "computed_at_bucket": NOW + timedelta(hours=shift),
                "compute_ms": 1200,
                "status": status,
                "summary": f"прогноз {code}",
                "blocks": (BROKEN_BLOCKS if direction == "UNAUTHORIZED_ACCESS" else GOOD_BLOCKS),
                "model_version": "test",
                "run_id": RUNS[0][0],
            }
            for code, direction, facility_id, probability, level, status, shift in PREDICTIONS
        ],
    )
    conn.execute(  # type: ignore[attr-defined]
        pipeline_run.insert(),
        [
            {
                "id": run_id,
                "started_at": NOW + timedelta(hours=shift),
                "finished_at": (
                    NOW + timedelta(hours=shift, milliseconds=duration) if duration else None
                ),
                "duration_ms": duration,
                "prediction_count": len(PREDICTIONS),
                "status": status,
            }
            for run_id, status, shift, duration in RUNS
        ],
    )
    conn.execute(  # type: ignore[attr-defined]
        model_metric.insert(),
        [
            {
                "direction": direction,
                "precision_value": precision,
                "recall_value": recall,
                "evaluated_at": NOW + timedelta(hours=shift),
                "method": "offline_holdout",
            }
            for direction, precision, recall, shift in METRICS
        ],
    )
    conn.execute(  # type: ignore[attr-defined]
        work_order.insert(),
        [
            {
                "id": code,
                "number": number,
                "prediction_id": prediction_id,
                "facility_id": facility_id,
                "work_type": "Диагностика датчика",
                "due_at": NOW + timedelta(hours=due_shift),
                "status": status,
                "created_at": NOW - timedelta(hours=2),
                "outcome": outcome,
            }
            for code, number, prediction_id, facility_id, status, due_shift, outcome in ORDERS
        ],
    )


@pytest.fixture(autouse=True)
def forget_access_model() -> Iterator[None]:
    """Чистит кэш модели направления доступа до и после каждого теста.

    Плагин держит модель и объяснитель в кэше на весь процесс: чтение модели
    стоит 47 мс, а конвейер зовёт его 7 894 раза за прогон. Внутри одного
    процесса pytest кэш переживает тест и отдаёт следующему чужую заглушку.
    """
    from app.ml.plugins import unauthorized_access

    unauthorized_access.reset_cache()
    yield
    unauthorized_access.reset_cache()


@pytest.fixture
def seeded() -> Iterator[None]:
    """Наполняет тестовую базу и чистит её после теста."""
    try:
        with engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as error:
        pytest.skip(f"тестовая база недоступна: {error}")

    migrate.run()
    with engine().begin() as conn:
        reset_database(conn)
        _insert(conn)

    yield

    with engine().begin() as conn:
        reset_database(conn)
