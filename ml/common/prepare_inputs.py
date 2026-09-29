"""Собирает два входных файла, на которых стоят скрипты всех направлений.

1. `eda/out/events.parquet`: журнал целиком, одна строка на запись. DuckDB
   переводит восемь CSV на 15,9 ГБ в parquet одним проходом примерно за 23
   секунды, результат весит 1,82 ГБ. Готовый файл не пересобирается.
2. `eda/out/channels.parquet`: справочник каналов с пикетом, смещением и
   галереей, разобранными из названия датчика. Пикет равен 10 метрам и
   является контейнером оборудования. Ключ места это тройка «объект, галерея,
   пикет».

В справочник канал входит, если у него есть хотя бы одна запись вне артефакта
2021 года: объект 5343 за 2020 и 2021 годы исключён.

Запуск из корня репозитория:

    python ml/common/prepare_inputs.py [--dataset raw_task/dataset]
"""

from __future__ import annotations

import argparse
import pathlib
import re
import time

import duckdb
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATASET = ROOT / "raw_task" / "dataset"
OUT = ROOT / "eda" / "out"
EVENTS = OUT / "events.parquet"
CHANNELS = OUT / "channels.parquet"

# Всплеск 2020 и 2021 годов даёт один объект: две версии системы мониторинга
# работали параллельно, и люди не снимали объекты с охраны (QA-сессия).
SPIKE_OBJECT = 5343
SPIKE_YEARS = (2020, 2021)

# Формы названия: `ТД ПК87`, `ДД ПК1+0,5`, `ТД ПК290-292`, `Темп. ВШ ПК88,5`
# и ответвление `Темп. ПК399 Г0ПК5`: галерея 0 от пикета 399 со своей
# нумерацией с нуля. Правило повторяет `backend/app/ingest/channels.py`.
PK = re.compile(r"ПК\s?(\d+)(?:\s?[Гг]\s?(\d+)\s?ПК\s?(\d+))?(?:\s?\+\s?(\d+(?:[.,]\d+)?))?")


def parse_picket(name: str | None) -> tuple[float, float, float]:
    """Возвращает (пикет, смещение в метрах, галерея)."""
    m = PK.search(name or "")
    if not m:
        return (np.nan, np.nan, np.nan)
    pk, gal, gal_pk, off = m.groups()
    if gal is not None:
        return (int(gal_pk), 0.0, int(gal))
    offset = float(off.replace(",", ".")) if off else 0.0
    return (int(pk), offset, np.nan)


def build_events(con: duckdb.DuckDBPyConnection, dataset: pathlib.Path) -> None:
    """Часть значений журнала несёт запятые и переносы строк, а
    `ext-journal-2025.csv` повторяет заголовок посреди файла. Поэтому всё
    читается строками, а повторный заголовок отсекает TRY_CAST."""
    t0 = time.time()
    con.execute(f"""
        COPY (
          SELECT
            TRY_CAST(ид_события AS BIGINT)        AS event_id,
            TRY_CAST(ид_канала_данных AS BIGINT)  AS channel_id,
            TRY_CAST(дата AS DATE)                AS d,
            TRY_CAST(время AS TIME)               AS t,
            (тревожное IN ('t', 'true', 'True'))  AS alarm,
            значение                              AS value
          FROM read_csv_auto('{(dataset / "ext-journal-*.csv").as_posix()}',
                             header=true, union_by_name=true, all_varchar=true)
          WHERE TRY_CAST(ид_события AS BIGINT) IS NOT NULL
        ) TO '{EVENTS.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    print(f"{EVENTS.name}: собран за {time.time() - t0:.1f} с")


def build_channels(con: duckdb.DuckDBPyConnection, dataset: pathlib.Path) -> None:
    ref = (dataset / "справочник_каналов_датчиков.csv").as_posix()
    df = con.sql(f"""
        SELECT c.ид_канала_данных AS cid,
               c.тип_инж_системы  AS sys,
               c.тип_датчика      AS stype,
               c.название_датчика AS nm,
               c.ид_объект        AS oid
        FROM read_csv_auto('{ref}', header=true) c
        WHERE c.ид_канала_данных IN (
            SELECT DISTINCT e.channel_id
            FROM read_parquet('{EVENTS.as_posix()}') e
            JOIN read_csv_auto('{ref}', header=true) r
              ON r.ид_канала_данных = e.channel_id
            WHERE NOT (r.ид_объект = {SPIKE_OBJECT} AND year(e.d) IN {SPIKE_YEARS})
        )
        ORDER BY c.ид_канала_данных
    """).df()

    parsed = df.nm.map(parse_picket)
    df["picket"] = [p[0] for p in parsed]
    df["offset_m"] = [p[1] for p in parsed]
    df["gallery"] = [p[2] for p in parsed]
    df["distance_m"] = df.picket * 10 + df.offset_m
    # Галерея обязана входить в ключ: пикет 5 главной линии и пикет 5
    # галереи 1 это разные места. Главная линия получает галерею -1.
    df["gal_key"] = df.gallery.fillna(-1)

    df.to_parquet(CHANNELS, index=False)
    places = df.dropna(subset=["picket"]).groupby(["oid", "gal_key", "picket"]).ngroups
    print(f"{CHANNELS.name}: каналов {len(df)}, с пикетом {int(df.picket.notna().sum())}, "
          f"мест «объект, галерея, пикет» {places}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", type=pathlib.Path, default=DATASET,
                        help="каталог выгрузки заказчика")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    if EVENTS.exists():
        print(f"{EVENTS.name}: уже есть, не пересобираю")
    else:
        build_events(con, args.dataset)
    build_channels(con, args.dataset)


if __name__ == "__main__":
    main()
