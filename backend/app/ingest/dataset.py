"""Загрузчик выгрузки СМВУ в базу сервиса. ADR 0017.

Выгрузка `raw_task/dataset/` весит 15,9 ГБ: восемь файлов журнала и три
справочника. Загрузчик читает её через DuckDB, отбирает строки, которые
читают признаки направлений, и пишет их в Postgres через `COPY`.

## Что попадает в базу

1. Объект выгрузки становится строкой `collector`, комплекс его подписью
   `district`.
2. Единица прогноза становится строкой `facility`:
   - `picket`: тройка «объект, галерея, пикет» каналов пожара, подтопления,
     температуры, газа и фазы;
   - `section`: участок доступа вокруг узла входа, правило
     `ml/access/data.py::build_sections`;
   - `object`: каналы объекта без пикета в названии.
3. Канал с известным типом становится строкой `sensor`. Канал «Состояние
   охраны» стоит на объекте целиком, поэтому его датчик и его события
   пишутся на каждый участок объекта.
4. Строка журнала становится событием `alarm_event` по правилу
   `app/ingest/mapping.py`.
5. Температура и метан становятся показанием `sensor_reading`: наибольшее
   значение канала за час, только за последние `READING_DAYS` суток истории.
   Правила пожара смотрят не дальше 30 суток назад.

Исключения повторяют витрины `ml/`: объект 5343 за 2020 и 2021 годы не
пишется совсем. Тревоги доступа не пишутся в первые 270 суток объекта и
первые 30 суток канала (ADR 0001).

## Время

Выгрузка кончается 2026-06-30. Поток заглушки идёт в текущем времени. Чтобы
история не обрывалась за месяцы до потока, загрузчик сдвигает все отметки
времени вперёд на целое число недель. Целые недели сохраняют день недели.
Праздники сдвигаются вместе с данными, и календарь признаков их не узнаёт.

Отрезок потока это неделя из отложенного полугодия 2026 года, где событий
трёх направлений больше всего. История кончается в точке этого отрезка,
которая после сдвига равна моменту загрузки. Заглушка продолжает её с той же
точки и потом крутит неделю по кругу. Сдвиг и отрезок лежат в `ingest_state`.

Журнал пишет местное время Москвы. Москва живёт в UTC+3 с 2014 года, поэтому
отметка получает сдвиг `+03` без таблицы часовых поясов.
"""

from __future__ import annotations

import gzip
import json
import logging
import math
import os
import random
import shutil
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine

from app.features.calendar_ru import is_day_off
from app.ingest import mapping, weather_loop
from app.ingest.channels import MAIN_LINE, PICKET_M, parse_place
from app.ingest.stream import accept, parse_row
from app.stub import scenarios
from app.synth.generate import wipe_synthetic
from app.synth.geo import OKRUGS, point_on
from app.tables import collector, facility, ingest_state, sensor, weather_hourly

log = logging.getLogger(__name__)

MOSCOW_OFFSET = timedelta(hours=3)
WEEK = timedelta(days=7)
DUMP_START = date(2019, 1, 31)
DUMP_END = datetime(2026, 7, 1)
HOLDOUT_START = datetime(2026, 1, 1)
ARTIFACT_OBJECT = 5343
ARTIFACT_YEARS = (2020, 2021)
OBJECT_COMMISSIONING_DAYS = 270
CHANNEL_COMMISSIONING_DAYS = 30
READING_DAYS = 40
SLICE_DAYS = 7

# Участки доступа. Числа из `ml/access/data.py`.
ENTRY_STYPES = ("КД АВ", "КД Люк", "9-секционный люк")
ENTRY_MERGE_PICKETS = 3
BIN_PICKETS = 15

CHANNELS_FILE = "справочник_каналов_датчиков.csv"
OBJECTS_FILE = "справочник_объектов_диспетчер.csv"
JOURNAL_GLOB = "ext-journal-*.csv"

STATE_KEY = "dataset"
WEATHER_AREA = "MOSCOW"

# Группы событий для выбора недели потока: неделя берётся там, где все три
# направления дают события, а их сумма по логарифму наибольшая.
SLICE_GROUPS: dict[str, tuple[str, ...]] = {
    "fire": ("SMOKE_DETECTED", "HEAT_OPEN", "TEMP_HIGH"),
    "flood": ("FLOODED", "FLOOD_SENSOR_OPEN"),
    "access": ("DOOR_OPEN", "MOTION"),
}


@dataclass
class Report:
    """Итог загрузки для журнала и для `ingest_state`."""

    slice_start: str = ""
    shift_days: int = 0
    history_until: str = ""
    collectors: int = 0
    facilities: dict[str, int] = field(default_factory=dict)
    sensors: int = 0
    events: int = 0
    readings: int = 0
    weather_hours: int = 0
    slice_rows: int = 0
    scenario_sections: int = 0
    scenario_rows: int = 0
    seconds: dict[str, float] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _duckdb() -> Any:
    try:
        import duckdb
    except ImportError as error:  # pragma: no cover - окружение без набора ingest
        raise RuntimeError("загрузчику нужен duckdb: uv sync --group ingest") from error
    con = duckdb.connect()
    con.execute(f"PRAGMA threads={os.cpu_count() or 4}")
    return con


def _q(path: Path) -> str:
    return "'" + path.as_posix().replace("'", "''") + "'"


def _stamp(expr: str) -> str:
    """Отметка времени Москвы текстом со сдвигом `+03` для `COPY`."""
    return f"strftime({expr}, '%Y-%m-%d %H:%M:%S') || '+03'"


# --- справочники ------------------------------------------------------------


def _read_directories(con: Any, dataset: Path) -> None:
    con.execute(
        f"""
        CREATE TABLE raw_ch AS
        SELECT CAST("ид_канала_данных" AS BIGINT) AS cid,
               "тип_инж_системы" AS sys, "тип_датчика" AS stype,
               "название_датчика" AS nm, CAST("ид_объект" AS INTEGER) AS oid
        FROM read_csv({_q(dataset / CHANNELS_FILE)}, header=true, all_varchar=true)
        """
    )
    con.execute(
        f"""
        CREATE TABLE obj AS
        SELECT CAST("ид_объект" AS INTEGER) AS oid,
               CAST("иерархия_уровень" AS INTEGER) AS level,
               CAST("родитель" AS INTEGER) AS parent,
               "диспетчерское_название_объекта" AS name
        FROM read_csv({_q(dataset / OBJECTS_FILE)}, header=true, all_varchar=true)
        """
    )
    rows = con.execute("SELECT cid, stype, nm FROM raw_ch").fetchall()
    places = []
    for cid, stype, name in rows:
        place = parse_place(name)
        places.append(
            (
                cid,
                mapping.SENSOR_TYPES.get(stype or ""),
                place.picket if place else None,
                place.offset_m if place else None,
                place.gallery if place else None,
            )
        )
    con.execute(
        "CREATE TABLE place (cid BIGINT, sensor_type TEXT, picket INTEGER, "
        "offset_m DOUBLE, gal INTEGER)"
    )
    con.executemany("INSERT INTO place VALUES (?, ?, ?, ?, ?)", places)
    con.execute("CREATE TABLE ch AS SELECT * FROM raw_ch JOIN place USING (cid)")


def _journal(con: Any, dataset: Path) -> None:
    """Представление журнала. Повторный заголовок 2025 года отбрасывается."""
    con.execute(
        f"""
        CREATE VIEW ev AS
        SELECT CAST("ид_события" AS BIGINT) AS event_id,
               CAST("ид_канала_данных" AS BIGINT) AS channel_id,
               CAST("дата" AS DATE) AS d,
               CAST("дата" AS DATE) + CAST("время" AS TIME) AS ts,
               ("тревожное" IN ('t', 'true')) AS alarm,
               "значение_датчика" AS value
        FROM read_csv({_q(dataset / JOURNAL_GLOB)}, header=true, all_varchar=true)
        WHERE TRY_CAST("ид_события" AS BIGINT) IS NOT NULL
        """
    )


# --- выборка событий ----------------------------------------------------------


def _select_events(con: Any) -> None:
    """Первые сутки каналов и события по правилу `mapping`. Два прохода журнала."""
    con.execute(
        "CREATE TABLE ch_first AS SELECT channel_id, min(d) AS first_day FROM ev GROUP BY 1"
    )
    code = mapping.event_code_sql("c.stype", "e.value", "e.alarm")
    con.execute(
        f"""
        CREATE TABLE evt AS
        SELECT e.event_id, e.channel_id, e.ts, {code} AS code
        FROM ev e JOIN ch c ON c.cid = e.channel_id
        WHERE c.sensor_type IS NOT NULL
          AND {code} IS NOT NULL
          AND NOT (c.oid = {ARTIFACT_OBJECT} AND year(e.d) IN {ARTIFACT_YEARS})
        """
    )


def _choose_slice(con: Any) -> datetime:
    """Неделя с понедельника в отложенном полугодии, где событий трёх
    направлений больше всего. Неделя обязана целиком лежать в выгрузке."""
    groups = " ".join(
        f"count(*) FILTER (WHERE code IN ({', '.join(repr(c) for c in codes)})) AS {name},"
        for name, codes in SLICE_GROUPS.items()
    )
    rows = con.execute(
        f"""
        SELECT date_trunc('week', ts) AS week, {groups} 0 AS pad
        FROM evt
        WHERE ts >= TIMESTAMP '{HOLDOUT_START:%Y-%m-%d}'
          AND ts < TIMESTAMP '{DUMP_END - WEEK:%Y-%m-%d}'
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    best: tuple[float, datetime] | None = None
    for week, *counts, _ in rows:
        if any(n == 0 for n in counts):
            continue
        score = sum(math.log1p(n) for n in counts)
        if best is None or score > best[0]:
            best = (score, week)
    if best is None:
        raise RuntimeError("в отложенном полугодии нет недели с событиями трёх направлений")
    return best[1]


# --- единицы ----------------------------------------------------------------


def _sections(con: Any) -> None:
    """Участки доступа. Правило `ml/access/data.py::build_sections` без правок."""
    entry = ", ".join(f"'{s}'" for s in ENTRY_STYPES)
    con.execute(
        f"""
        CREATE TABLE section_map AS
        WITH c AS (
            SELECT DISTINCT oid AS object_id, gal AS gallery, picket, stype, nm
            FROM ch WHERE picket IS NOT NULL
        ),
        entry AS (
            SELECT DISTINCT object_id, gallery, picket FROM c
            WHERE stype IN ({entry}) OR nm ILIKE '%ВШ%'
               OR (stype = 'КД Дверь' AND NOT (nm ILIKE '%отс%' OR nm ILIKE '%шкаф%'
                                               OR nm ILIKE '%щ%'))
        ),
        brk AS (
            SELECT *, CASE WHEN picket - lag(picket) OVER w > {ENTRY_MERGE_PICKETS}
                                OR lag(picket) OVER w IS NULL THEN 1 ELSE 0 END AS b
            FROM entry WINDOW w AS (PARTITION BY object_id, gallery ORDER BY picket)
        ),
        node AS (
            SELECT object_id, gallery, node, min(picket) AS pk_from, max(picket) AS pk_to
            FROM (SELECT *, sum(b) OVER (PARTITION BY object_id, gallery ORDER BY picket
                                         ROWS UNBOUNDED PRECEDING) AS node FROM brk)
            GROUP BY ALL
        ),
        pk AS (SELECT DISTINCT object_id, gallery, picket FROM c),
        near AS (
            SELECT p.object_id, p.gallery, p.picket, n.node,
                   CASE WHEN p.picket < n.pk_from THEN n.pk_from - p.picket
                        WHEN p.picket > n.pk_to THEN p.picket - n.pk_to ELSE 0 END AS dist
            FROM pk p JOIN node n USING (object_id, gallery)
        )
        SELECT object_id, gallery, picket,
               CAST(arg_min(node, dist * 1000 + node) AS INTEGER) AS section
        FROM near GROUP BY ALL
        UNION ALL
        SELECT p.object_id, p.gallery, p.picket,
               CAST(1000 + floor(p.picket / {BIN_PICKETS}) AS INTEGER)
        FROM pk p
        WHERE NOT EXISTS (SELECT 1 FROM node n
                          WHERE n.object_id = p.object_id AND n.gallery = p.gallery)
        """
    )


def _units(con: Any) -> None:
    """Раскладка каналов по единицам: таблица `unit_of` «канал → единица»."""
    access = ", ".join(f"'{s}'" for s in mapping.ACCESS_STYPES)
    guard = mapping.GUARD_STYPE
    con.execute(
        f"""
        CREATE TABLE unit_of AS
        -- доступ: участок пикета канала
        SELECT c.cid, 'section' AS kind, c.oid, sm.gallery, NULL::INTEGER AS picket,
               sm.section, c.stype, c.sensor_type, c.nm
        FROM ch c JOIN section_map sm
          ON sm.object_id = c.oid AND sm.gallery = c.gal AND sm.picket = c.picket
        WHERE c.stype IN ({access})
        UNION ALL
        -- пожар, подтопление, температура, газ, фаза: пикет
        SELECT c.cid, 'picket', c.oid, c.gal, c.picket, NULL, c.stype, c.sensor_type, c.nm
        FROM ch c
        WHERE c.sensor_type IS NOT NULL AND c.stype NOT IN ({access}) AND c.stype <> '{guard}'
          AND c.picket IS NOT NULL
        UNION ALL
        -- те же каналы без пикета в названии: объект целиком
        SELECT c.cid, 'object', c.oid, NULL, NULL, NULL, c.stype, c.sensor_type, c.nm
        FROM ch c
        WHERE c.sensor_type IS NOT NULL AND c.stype NOT IN ({access}) AND c.stype <> '{guard}'
          AND c.picket IS NULL
        """
    )
    # Режим охраны объекта пишется на каждый участок объекта.
    con.execute(
        f"""
        INSERT INTO unit_of
        SELECT c.cid, 'section', c.oid, s.gallery, NULL, s.section, c.stype, c.sensor_type, c.nm
        FROM ch c
        JOIN (SELECT DISTINCT oid, gallery, section FROM unit_of WHERE kind = 'section') s
          ON s.oid = c.oid
        WHERE c.stype = '{guard}'
        """
    )
    con.execute(
        f"""
        CREATE TABLE unit_id AS
        SELECT *, CASE kind
            WHEN 'section' THEN 'S-' || oid || '-' || branch || section
            WHEN 'picket' THEN 'P-' || oid || '-' || branch || picket
            ELSE 'O-' || oid END AS facility_id
        FROM (
            -- Основная линия идёт без метки галереи: P-5963-1050. Галерея
            -- несёт свою: P-5963-G0-5.
            SELECT *, CASE WHEN gallery IS NULL OR gallery = {MAIN_LINE} THEN ''
                           ELSE 'G' || gallery || '-' END AS branch
            FROM unit_of
        )
        """
    )
    # Участок каждого пикета. Пожар считается на участке (ADR 0018), поэтому
    # участок нужен и там, где каналов доступа нет.
    con.execute(
        f"""
        CREATE TABLE parent_of AS
        SELECT DISTINCT u.facility_id AS child, u.oid, u.gallery, sm.section,
               'S-' || u.oid || '-' || CASE WHEN u.gallery IS NULL OR u.gallery = {MAIN_LINE}
                   THEN '' ELSE 'G' || u.gallery || '-' END || sm.section AS parent
        FROM unit_id u JOIN section_map sm
          ON sm.object_id = u.oid AND sm.gallery = u.gallery AND sm.picket = u.picket
        WHERE u.kind = 'picket'
        """
    )
    con.execute(
        """
        CREATE TABLE section_only AS
        SELECT DISTINCT p.parent AS facility_id, p.oid, p.gallery, p.section
        FROM parent_of p
        WHERE p.parent NOT IN (SELECT facility_id FROM unit_id)
        """
    )
    con.execute(
        """
        CREATE TABLE sensor_of AS
        SELECT cid, facility_id, kind, stype, sensor_type, nm,
               CASE WHEN count(*) OVER (PARTITION BY cid) > 1
                    THEN 'C' || cid || '-' || facility_id ELSE 'C' || cid END AS sensor_id
        FROM unit_id
        """
    )


def _commissioning(con: Any) -> None:
    """День, с которого тревоги доступа канала входят в историю (ADR 0001)."""
    con.execute(
        f"""
        CREATE TABLE ready AS
        WITH f AS (
            SELECT c.cid, c.oid, cf.first_day FROM ch c JOIN ch_first cf ON cf.channel_id = c.cid
        ),
        o AS (SELECT oid, min(first_day) AS first_day FROM f GROUP BY 1)
        SELECT f.cid, f.first_day,
               greatest(
                   CASE WHEN o.first_day <= DATE '{DUMP_START}' THEN DATE '1900-01-01'
                        ELSE o.first_day + INTERVAL {OBJECT_COMMISSIONING_DAYS} DAY END,
                   CASE WHEN f.first_day <= DATE '{DUMP_START}' THEN DATE '1900-01-01'
                        ELSE f.first_day + INTERVAL {CHANNEL_COMMISSIONING_DAYS} DAY END
               )::DATE AS ready_day
        FROM f JOIN o USING (oid)
        """
    )


# --- география -----------------------------------------------------------------


def _geography(con: Any) -> tuple[list[dict[str, Any]], dict[str, tuple[float, float]]]:
    """Условная трасса объекта и точка каждой единицы.

    Координат в выгрузке нет (`INSIGHTS.md` §1.11). Комплексы ложатся на трассы
    округов по кругу, объекты комплекса делят трассу подряд. Единица стоит на
    трассе объекта по своей координате вдоль линии, галерея чуть в стороне.
    Длина и порядок пикетов настоящие, положение на карте условное.
    """
    objects = {
        oid: (name, parent)
        for oid, name, parent in con.execute("SELECT oid, name, parent FROM obj").fetchall()
    }
    units = con.execute(
        """
        SELECT DISTINCT u.facility_id, u.kind, u.oid, u.gallery,
               coalesce(u.picket, avg(c.picket) OVER (PARTITION BY u.facility_id)) AS pk
        FROM unit_id u JOIN ch c ON c.cid = u.cid
        UNION ALL
        SELECT s.facility_id, 'section', s.oid, s.gallery, avg(sm.picket)
        FROM section_only s JOIN section_map sm
          ON sm.object_id = s.oid AND sm.gallery = s.gallery AND sm.section = s.section
        GROUP BY ALL
        """
    ).fetchall()
    by_object: dict[int, list[tuple[str, str, int | None, float | None]]] = defaultdict(list)
    for fid, kind, oid, gallery, pk in units:
        by_object[oid].append((fid, kind, gallery, pk))

    complexes: dict[int, list[int]] = defaultdict(list)
    for oid in sorted(by_object):
        complexes[objects.get(oid, ("", 0))[1] or 0].append(oid)

    collectors: list[dict[str, Any]] = []
    points: dict[str, tuple[float, float]] = {}
    for index, (_complex_id, oids) in enumerate(sorted(complexes.items())):
        okrug = OKRUGS[index % len(OKRUGS)]
        for k, oid in enumerate(oids):
            lo, hi = k / len(oids), (k + 1) / len(oids)
            line = [point_on(okrug.line, lo + (hi - lo) * i / 7) for i in range(8)]
            collectors.append(
                {
                    "code": str(oid),
                    "label": objects.get(oid, (f"объект {oid}", 0))[0],
                    # Район это округ, на трассу которого лёг комплекс: его
                    # знает справочник районов и роль диспетчера района.
                    "district": okrug.code,
                    "line": [list(p) for p in line],
                }
            )
            longest = max((pk or 0.0 for _, _, _, pk in by_object[oid]), default=0.0) + 1.0
            for fid, _kind, gallery, pk in by_object[oid]:
                share = (pk or 0.0) / longest
                lon, lat = point_on(tuple(line), share)
                if gallery is not None and gallery != MAIN_LINE:
                    lat += 0.0006 * (gallery + 1)
                points[fid] = (lon, lat)
    return collectors, points


# --- сценарии пожара ----------------------------------------------------------


def _scenario_candidates(con: Any, cutoff: datetime) -> list[dict[str, Any]]:
    """Участки, где заглушка может разыграть пожар. ADR 0018.

    Средний сценарий берёт любой датчик дыма участка. Высокому нужна пара
    датчиков дыма одной галереи на разных пикетах не дальше 50 м. Критическому
    нужна пара и датчик температуры на объекте. Объект моложе 180 суток
    пропускается: правила понизили бы ему уровень охлаждением.
    """
    young = {
        oid
        for (oid,) in con.execute(
            f"""
            SELECT c.oid FROM ch c JOIN ch_first f ON f.channel_id = c.cid
            WHERE c.stype IN ('{mapping.SMOKE_STYPE}', '{mapping.HEAT_STYPE}',
                              '{mapping.TEMP_STYPE}')
            GROUP BY 1
            HAVING min(f.first_day) > DATE '{(cutoff - timedelta(days=200)):%Y-%m-%d}'
            """
        ).fetchall()
    }
    pairs = con.execute(
        f"""
        WITH s AS (
            SELECT u.cid, u.oid, u.gallery, p.parent AS section,
                   c.picket * {PICKET_M} + coalesce(c.offset_m, 0) AS chain, c.picket
            FROM unit_id u JOIN ch c ON c.cid = u.cid
            JOIN parent_of p ON p.child = u.facility_id
            WHERE u.stype = '{mapping.SMOKE_STYPE}'
        )
        SELECT a.section, a.oid, list(DISTINCT a.cid),
               list(DISTINCT [a.cid, b.cid]) FILTER (WHERE b.cid IS NOT NULL)
        FROM s a LEFT JOIN s b
          ON a.section = b.section AND a.gallery = b.gallery AND a.cid < b.cid
         AND a.picket <> b.picket AND abs(a.chain - b.chain) <= 50
        GROUP BY 1, 2
        """
    ).fetchall()
    by_object: dict[str, dict[int, list[int]]] = {"heat": {}, "temp": {}}
    for kind, stype in (("heat", mapping.HEAT_STYPE), ("temp", mapping.TEMP_STYPE)):
        for oid, cids in con.execute(
            f"SELECT oid, list(DISTINCT cid) FROM unit_id WHERE stype = '{stype}' GROUP BY 1"
        ).fetchall():
            by_object[kind][oid] = [int(c) for c in cids]
    result = []
    for section, oid, smoke, found in pairs:
        if oid in young:
            continue
        result.append(
            {
                "section": section,
                "object": int(oid),
                "smoke": sorted(int(c) for c in smoke),
                "pairs": [[int(a), int(b)] for a, b in (found or [])],
                "heat": by_object["heat"].get(oid, []),
                "temp": by_object["temp"].get(oid, []),
            }
        )
    return sorted(result, key=lambda c: c["section"])


def _seed_scenarios(engine: Engine, candidates: list[dict[str, Any]], now_msk: datetime) -> int:
    """Сценарии пожара в последние сутки истории. Иначе уровни появились бы
    только через сутки работы заглушки."""
    rng = random.Random(2026)
    rows = scenarios.plan(candidates, now_msk - timedelta(hours=24), now_msk, rng, is_day_off)
    stream_rows = [
        parse_row(
            scenarios.EVENT_ID_BASE + i,
            r.channel_id,
            r.at.strftime("%Y-%m-%d"),
            r.at.strftime("%H:%M:%S"),
            r.alarm,
            r.value,
        )
        for i, r in enumerate(rows)
    ]
    with engine.begin() as conn:
        accept(conn, stream_rows, None)
    return len(rows)


# --- запись -------------------------------------------------------------------


def _copy(engine: Engine, table: str, columns: str, source: Path) -> int:
    """`COPY FROM STDIN` файла CSV. Отдаёт число строк."""
    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        statement = f"COPY {table} ({columns}) FROM STDIN WITH (FORMAT csv)"
        with cursor.copy(statement) as copy, source.open("rb") as handle:  # type: ignore[attr-defined]
            while chunk := handle.read(1 << 20):
                copy.write(chunk)
        count = int(cursor.rowcount)
        raw.commit()
        return count
    finally:
        raw.close()


def _facility_rows(
    con: Any, points: dict[str, tuple[float, float]], shift: timedelta
) -> list[dict[str, Any]]:
    names = {
        oid: (name, parent)
        for oid, name, parent in con.execute("SELECT oid, name, parent FROM obj").fetchall()
    }
    rows = con.execute(
        """
        WITH life AS (
            SELECT u.facility_id, min(greatest(r.first_day, r.ready_day)) AS born
            FROM unit_id u JOIN ready r ON r.cid = u.cid
            WHERE u.kind = 'section' AND u.stype <> 'Состояние охраны'
            GROUP BY 1
        )
        SELECT DISTINCT u.facility_id, u.kind, u.oid, u.gallery, u.picket, u.section,
               min(c.offset_m) OVER (PARTITION BY u.facility_id) AS offset_m,
               l.born, p.parent
        FROM unit_id u JOIN ch c ON c.cid = u.cid
        LEFT JOIN life l USING (facility_id)
        LEFT JOIN parent_of p ON p.child = u.facility_id
        UNION ALL
        SELECT facility_id, 'section', oid, gallery, NULL, section, NULL, NULL, NULL
        FROM section_only
        """
    ).fetchall()
    result = []
    for fid, kind, oid, gallery, picket, section, offset, born, parent_id in rows:
        name, parent = names.get(oid, (f"объект {oid}", 0))
        complex_name = names.get(parent, (f"комплекс {parent}", 0))[0]
        branch = f"галерея {gallery}, " if gallery is not None and gallery != MAIN_LINE else ""
        if kind == "picket":
            address = f"{name}, {branch}ПК{picket}"
        elif kind == "section":
            address = f"{name}, {branch}участок {section}"
        else:
            address = f"{name}, без пикета"
        lon, lat = points.get(fid, (37.62, 55.75))
        result.append(
            {
                "id": fid,
                "collector": str(oid),
                "section": f"участок {section}" if kind == "section" else None,
                "chamber": None,
                "device": None,
                "district": complex_name,
                "address": address,
                "lat": lat,
                "lon": lon,
                "facility_type": kind,
                "commissioned_at": (born + shift) if born is not None else None,
                "is_active": True,
                "kind": kind,
                "object_id": oid,
                "gallery": gallery,
                "picket": float(picket) if picket is not None else None,
                "section_no": section,
                "chainage_m": (picket * PICKET_M + (offset or 0.0)) if picket is not None else None,
                "parent_id": parent_id,
            }
        )
    return result


def _weather(conn: Connection, source: Path, shift: timedelta, until: datetime) -> int:
    """Часовая погода Москвы из архива Open-Meteo, сдвинутая вместе с журналом."""
    payload = json.loads(source.read_text(encoding="utf-8"))
    hourly = payload["hourly"]
    offset = timedelta(seconds=int(payload.get("utc_offset_seconds", 10800)))
    items = []
    for index, stamp in enumerate(hourly["time"]):
        local = datetime.fromisoformat(stamp)
        observed = (local - offset).replace(tzinfo=UTC) + shift
        if observed >= until:
            continue
        items.append(
            {
                "observed_at": observed,
                "district": WEATHER_AREA,
                "temperature_c": hourly["temperature_2m"][index],
                "humidity": (hourly.get("relative_humidity_2m") or [None] * len(hourly["time"]))[
                    index
                ],
                "precip_mm": hourly["precipitation"][index],
                "rain_mm": hourly["rain"][index],
                "snowfall_cm": hourly["snowfall"][index],
                "snow_depth_m": hourly["snow_depth"][index],
            }
        )
    conn.execute(weather_hourly.delete().where(weather_hourly.c.district == WEATHER_AREA))
    for start in range(0, len(items), 5000):
        conn.execute(pg_insert(weather_hourly).values(items[start : start + 5000]))
    return len(items)


# --- вход ---------------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    """Отрезок потока и сдвиг времени."""

    slice_start: datetime
    shift: timedelta
    cutoff: datetime

    @staticmethod
    def for_now(slice_start: datetime, now_msk: datetime) -> Plan:
        """Сдвиг на целые недели: отрезок потока, сдвинутый на него, начинается
        не позже `now_msk`. История кончается там, где сейчас идёт поток."""
        weeks = math.floor((now_msk - slice_start) / WEEK)
        shift = WEEK * weeks
        return Plan(slice_start, shift, now_msk - shift)


def load(
    engine: Engine,
    dataset: Path,
    stream_dir: Path,
    now: datetime | None = None,
    slice_start: date | None = None,
    weather: Path | None = None,
) -> Report:
    """Загружает выгрузку и готовит отрезок потока. Прежние данные стираются."""
    report = Report()
    now_utc = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    now_msk = (now_utc + MOSCOW_OFFSET).replace(tzinfo=None)
    tick = time.monotonic()

    con = _duckdb()
    _read_directories(con, dataset)
    _journal(con, dataset)
    _select_events(con)
    report.seconds["journal"] = round(time.monotonic() - tick, 1)

    start = (
        datetime.combine(slice_start, datetime.min.time()) if slice_start else _choose_slice(con)
    )
    plan = Plan.for_now(start, now_msk)
    report.slice_start = start.isoformat()
    report.shift_days = plan.shift.days
    report.history_until = (plan.cutoff + plan.shift).isoformat() + "+03:00"
    shift_sql = f"INTERVAL {plan.shift.days} DAY"
    cutoff_sql = f"TIMESTAMP '{plan.cutoff:%Y-%m-%d %H:%M:%S}'"

    _sections(con)
    _units(con)
    _commissioning(con)
    collectors, points = _geography(con)
    facilities = _facility_rows(con, points, plan.shift)
    # Район объекта это округ его коллектора, а не имя комплекса: фильтр по
    # районам и роль диспетчера района знают коды округов.
    okrug_of = {row["code"]: row["district"] for row in collectors}
    for row in facilities:
        row["district"] = okrug_of.get(row["collector"], row["district"])

    tmp = Path(tempfile.mkdtemp(prefix="arm-ingest-"))
    try:
        access_codes = ", ".join(f"'{c}'" for c in ("DOOR_OPEN", "MOTION"))
        events_csv = tmp / "events.csv"
        con.execute(
            f"""
            COPY (
                SELECT s.sensor_id, s.facility_id, {_stamp("e.ts + " + shift_sql)},
                       e.code, e.event_id
                FROM evt e
                JOIN sensor_of s ON s.cid = e.channel_id
                JOIN ready r ON r.cid = e.channel_id
                WHERE e.ts < {cutoff_sql}
                  AND (e.code NOT IN ({access_codes}) OR CAST(e.ts AS DATE) >= r.ready_day)
                ORDER BY e.ts
            ) TO {_q(events_csv)} (FORMAT csv, HEADER false)
            """
        )
        metric, number = mapping.reading_sql("c.stype", "e.value")
        readings_csv = tmp / "readings.csv"
        units = " ".join(f"WHEN '{k}' THEN '{v.unit}'" for k, v in mapping.NUMERIC.items())
        numeric = ", ".join(f"'{k}'" for k in mapping.NUMERIC)
        tick = time.monotonic()
        con.execute(
            f"""
            COPY (
                SELECT s.sensor_id, s.facility_id, metric,
                       {_stamp("h + " + shift_sql)}, max(v), unit
                FROM (
                    SELECT e.channel_id, date_trunc('hour', e.ts) AS h,
                           {metric} AS metric, {number} AS v,
                           CASE c.stype {units} END AS unit
                    FROM ev e JOIN ch c ON c.cid = e.channel_id
                    WHERE c.stype IN ({numeric})
                      AND e.ts >= {cutoff_sql} - INTERVAL {READING_DAYS} DAY
                      AND e.ts < {cutoff_sql}
                ) x
                JOIN sensor_of s ON s.cid = x.channel_id
                WHERE v IS NOT NULL
                GROUP BY ALL
            ) TO {_q(readings_csv)} (FORMAT csv, HEADER false)
            """
        )
        report.seconds["readings"] = round(time.monotonic() - tick, 1)

        slice_csv = stream_dir / "slice.csv.gz"
        stream_dir.mkdir(parents=True, exist_ok=True)
        tick = time.monotonic()
        plain = tmp / "slice.csv"
        slice_end = f"TIMESTAMP '{start + WEEK:%Y-%m-%d %H:%M:%S}'"
        con.execute(
            f"""
            COPY (
                SELECT e.event_id AS "ид_события", e.channel_id AS "ид_канала_данных",
                       strftime(e.ts, '%Y-%m-%d') AS "дата", strftime(e.ts, '%H:%M:%S') AS "время",
                       CASE WHEN e.alarm THEN 'true' ELSE 'false' END AS "тревожное",
                       e.value AS "значение_датчика"
                FROM ev e JOIN ch c ON c.cid = e.channel_id
                WHERE e.ts >= TIMESTAMP '{start:%Y-%m-%d %H:%M:%S}' AND e.ts < {slice_end}
                  AND c.cid IN (SELECT cid FROM sensor_of)
                  AND ({mapping.event_code_sql("c.stype", "e.value", "e.alarm")} IS NOT NULL
                       OR c.stype IN ({numeric}))
                ORDER BY e.ts, e.event_id
            ) TO {_q(plain)} (FORMAT csv, HEADER true)
            """
        )
        with plain.open("rb") as src, gzip.open(slice_csv, "wb") as dst:
            shutil.copyfileobj(src, dst)
        report.slice_rows = int(
            con.execute(f"SELECT count(*) FROM read_csv({_q(plain)}, header=true)").fetchone()[0]
        )
        report.seconds["slice"] = round(time.monotonic() - tick, 1)
        meta = {
            "slice_start": start.isoformat(),
            "slice_days": SLICE_DAYS,
            "shift_days": plan.shift.days,
            "history_until": report.history_until,
            "rows": report.slice_rows,
        }
        (stream_dir / "slice.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        sensor_rows = [
            {
                "id": sid,
                "facility_id": fid,
                "sensor_type": stype_code,
                "installed_at": (first + plan.shift) if first is not None else None,
                "is_active": True,
                "channel_id": cid,
                "channel_type": stype,
                "channel_name": name,
            }
            for sid, fid, stype_code, stype, name, cid, first in con.execute(
                """
                SELECT s.sensor_id, s.facility_id, s.sensor_type, s.stype, s.nm, s.cid,
                       cf.first_day
                FROM sensor_of s LEFT JOIN ch_first cf ON cf.channel_id = s.cid
                """
            ).fetchall()
        ]

        tick = time.monotonic()
        with engine.begin() as conn:
            wipe_synthetic(conn)
            conn.execute(collector.insert(), collectors)
            for i in range(0, len(facilities), 2000):
                conn.execute(facility.insert(), facilities[i : i + 2000])
            for i in range(0, len(sensor_rows), 2000):
                conn.execute(sensor.insert(), sensor_rows[i : i + 2000])
            if weather is not None and weather.exists():
                report.weather_hours = _weather(
                    conn,
                    weather,
                    plan.shift,
                    (plan.cutoff + plan.shift - MOSCOW_OFFSET).replace(tzinfo=UTC),
                )
                payload = json.loads(weather.read_text(encoding="utf-8"))
                week = weather_loop.slice_hours(payload["hourly"], start)
                statement = pg_insert(ingest_state).values(
                    key=weather_loop.STATE_KEY,
                    value={
                        "slice_start": start.isoformat(),
                        "shift_days": plan.shift.days,
                        "hours": week,
                    },
                    updated_at=datetime.now(UTC),
                )
                conn.execute(
                    statement.on_conflict_do_update(
                        index_elements=["key"],
                        set_={"value": statement.excluded.value},
                    )
                )
        report.events = _copy(
            engine,
            "alarm_event",
            "sensor_id, facility_id, occurred_at, alarm_type, source_event_id",
            events_csv,
        )
        report.readings = _copy(
            engine,
            "sensor_reading",
            "sensor_id, facility_id, metric, observed_at, value, unit",
            readings_csv,
        )
        report.seconds["postgres"] = round(time.monotonic() - tick, 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    candidates = _scenario_candidates(con, plan.cutoff)
    (stream_dir / "fire_scenarios.json").write_text(
        json.dumps(candidates, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    report.scenario_sections = len(candidates)
    report.scenario_rows = _seed_scenarios(engine, candidates, now_msk)

    report.collectors = len(collectors)
    report.sensors = len(sensor_rows)
    counts: dict[str, int] = defaultdict(int)
    for row in facilities:
        counts[row["kind"]] += 1
    report.facilities = dict(counts)
    with engine.begin() as conn:
        statement = pg_insert(ingest_state).values(
            key=STATE_KEY, value=report.as_json(), updated_at=datetime.now(UTC)
        )
        conn.execute(
            statement.on_conflict_do_update(
                index_elements=["key"],
                set_={
                    "value": statement.excluded.value,
                    "updated_at": statement.excluded.updated_at,
                },
            )
        )
    return report
