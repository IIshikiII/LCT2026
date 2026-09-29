"""Готовит данные направления «несанкционированный доступ». Это единственный
скрипт направления, который читает выгрузку. Остальные скрипты читают только
его результат в `out/`.

Входы: `eda/out/events.parquet` (журнал) и `notebooks/out/channel_features.parquet`
(справочник каналов с объектом, галереей и пикетом).

Единица это участок хода вокруг узла входа. Узел входа это аварийный выход,
люк, вентшахта или входная дверь. Входы ближе 3 пикетов друг к другу образуют
один узел. Пикет относится к ближайшему узлу на своей линии, то есть на том же
объекте и той же галерее. Медиана шага между узлами 15 пикетов, 150 м. Линия
без входов режется на куски по 15 пикетов.

Результат в `out/`:

- `sections.parquet` — пикет и его участок;
- `access_hourly.parquet` — часы с тревогой доступа на участке, все тревоги;
- `access_hourly_armed.parquet` — часы с событием доступа вне окон «Снято с
  охраны». По ним строится метка (ADR 0001);
- `access_units.parquet` — срок жизни каждого участка;
- `section_info.parquet` — паспорт участка: входы и датчики;
- `faults_daily.parquet` — записи «Неисправен» на каналах участка по суткам;
- `disarm_windows.parquet` — окна «Снято с охраны» по объектам;
- `guard_unknown.parquet` — периоды, когда режим охраны объекта неизвестен;
- `access_chain.parquet` — инциденты, где за контактом входа пошло движение;
- `calendar.parquet` — календарь России по дням;
- `data_stats.json` — сводные числа.

Отбор тревог:

1. «Не замкнут» на контакте аварийного выхода, двери, люка или на датчике
   стекла. «Обнаружено движение» на датчике движения. Остальные типы датчиков
   в доступ не входят: пожарные, затопления, переговорные устройства.
2. Только каналы с пикетом.
3. Без объекта 5343 за 2020 и 2021 годы: это артефакт миграции.
4. Без первых 270 суток жизни объекта и без первых 30 суток жизни канала:
   это шум пусконаладки. Объект или канал, который пишет с начала выгрузки,
   правило не трогает.

Событие для метки это инцидент: тревоги доступа на участке вне окон «Снято с
охраны» с паузами не больше 30 минут. Инцидент считается событием, когда в нём
есть контакт входа или стекла, два и больше датчиков движения, или датчик
движения не дальше 6 пикетов от входа. Одиночное движение вдали от входов
заказчик считает ложным (QA-сессия). Правило действует, только когда участок
держат два и больше контактных датчика. Одному датчику верить нельзя: его могли
обойти или выключить, поэтому на участке с одним контактом или без контактов
одиночное движение тоже считается событием.

Запуск из корня репозитория:

    .venv/bin/python ml/access/data.py            (Ubuntu, macOS)
    .venv\\Scripts\\python.exe ml/access/data.py  (Windows)
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import time

import duckdb

ROOT = pathlib.Path(__file__).resolve().parents[2]
EVENTS = ROOT / "eda" / "out" / "events.parquet"
CHANNELS = ROOT / "notebooks" / "out" / "channel_features.parquet"
OUT = pathlib.Path(__file__).resolve().parent / "out"

HOURLY = OUT / "access_hourly.parquet"
ARMED = OUT / "access_hourly_armed.parquet"
UNITS = OUT / "access_units.parquet"
SECTIONS = OUT / "sections.parquet"
SECTION_INFO = OUT / "section_info.parquet"
FAULTS = OUT / "faults_daily.parquet"
DISARM = OUT / "disarm_windows.parquet"
GUARD_UNKNOWN = OUT / "guard_unknown.parquet"
CHAIN = OUT / "access_chain.parquet"
CALENDAR = OUT / "calendar.parquet"
STATS = OUT / "data_stats.json"

CONTACT_VALUE = "Не замкнут"
CONTACT_STYPES = ("КД АВ", "КД Дверь", "КД Люк", "9-секционный люк", "Стекло")
MOTION_VALUE = "Обнаружено движение"
MOTION_STYPE = "Датчик движения"
ARTIFACT_OID = 5343
ARTIFACT_YEARS = (2020, 2021)

# Ввод объекта в работу. Доля тревог доступа в первый месяц объекта в 104 раза
# выше фона и опускается к фону на 9–10 месяце (ADR 0001).
COMMISSIONING_DAYS = 270
# Канал, добавленный на работающий объект, шумит только первый месяц: доля
# суток с тревогой 21,3 % против 13,1 % после года. Замер 24 сентября.
CHANNEL_COMMISSIONING_DAYS = 30
# Объект с первой записью до этой даты виден с левого края выгрузки.
DUMP_START = "2019-01-31"

GUARD_TYPE = "Состояние охраны"
GUARD_OFF = "Снято с охраны"
GUARD_ON = "На охране"
# Режим неизвестен, когда канал режима не меняет его дольше этого срока. 99 %
# обычных пауз между сменами режима короче 5,7 суток. В 2026 году 12 объектов
# перестали вести режим, и все их тревоги попадали в метку. Замер 24 сентября.
GUARD_SILENCE_DAYS = 7
DUMP_END = "2026-07-01"

# Вход в коллектор. Дверь входом считается, когда это не отсечная дверь внутри
# хода и не дверь шкафа или щита.
ENTRY_STYPES = ("КД АВ", "КД Люк", "9-секционный люк")
ENTRY_MERGE_PICKETS = 3
# Кусок линии без входов, пикетов. Равен медиане шага между узлами входа.
BIN_PICKETS = 15
# Датчик движения ближе этого числа пикетов ко входу стоит «у входа».
NEAR_ENTRY_PICKETS = 6
INCIDENT_GAP_MINUTES = 30

# Постоянные нерабочие дни. Переносы выходных по постановлениям сюда не входят.
FIXED_HOLIDAYS: tuple[tuple[int, int], ...] = (
    (1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6), (1, 7), (1, 8),
    (2, 23), (3, 8), (5, 1), (5, 9), (6, 12), (11, 4),
)
# Школьные каникулы по типовому расписанию Москвы: начало и конец включительно.
SCHOOL_VACATIONS: tuple[tuple[int, int, int, int], ...] = (
    (10, 26, 11, 3),
    (12, 28, 1, 8),
    (3, 23, 3, 31),
    (6, 1, 8, 31),
)


def _src() -> tuple[str, str]:
    return EVENTS.as_posix(), CHANNELS.as_posix()


def build_commissioning(con: duckdb.DuckDBPyConnection) -> None:
    """Таблица `commissioning`: день, с которого канал входит в выборку. Это
    поздний из двух дней: готовность объекта и готовность самого канала."""
    events, channels = _src()
    con.execute(
        f"""
        CREATE OR REPLACE TABLE commissioning AS
        WITH ch_first AS (
            SELECT ev.channel_id, any_value(ch.oid) AS object_id, min(ev.d) AS first_day
            FROM read_parquet('{events}') ev
            JOIN read_parquet('{channels}') ch ON ch.cid = ev.channel_id
            GROUP BY 1
        ),
        obj AS (
            SELECT object_id, min(first_day) AS first_day FROM ch_first GROUP BY 1
        )
        SELECT
            c.channel_id,
            greatest(
                CASE WHEN o.first_day <= DATE '{DUMP_START}' THEN DATE '1900-01-01'
                     ELSE o.first_day + INTERVAL {COMMISSIONING_DAYS} DAY END,
                CASE WHEN c.first_day <= DATE '{DUMP_START}' THEN DATE '1900-01-01'
                     ELSE c.first_day + INTERVAL {CHANNEL_COMMISSIONING_DAYS} DAY END
            )::DATE AS ready_day
        FROM ch_first c JOIN obj o USING (object_id)
        """
    )


def build_sections(con: duckdb.DuckDBPyConnection) -> None:
    """Таблица `section_map`: пикет, его участок и расстояние до узла входа."""
    _, channels = _src()
    entry_types = ", ".join(f"'{s}'" for s in ENTRY_STYPES)
    con.execute(
        f"""
        CREATE OR REPLACE TABLE section_map AS
        WITH ch AS (
            SELECT DISTINCT oid AS object_id, gal_key AS gallery, picket, stype, nm
            FROM read_parquet('{channels}') WHERE picket IS NOT NULL
        ),
        entry AS (
            SELECT DISTINCT object_id, gallery, picket FROM ch
            WHERE stype IN ({entry_types}) OR nm ILIKE '%ВШ%'
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
        pk AS (SELECT DISTINCT object_id, gallery, picket FROM ch),
        near AS (
            SELECT p.object_id, p.gallery, p.picket, n.node,
                   CASE WHEN p.picket < n.pk_from THEN n.pk_from - p.picket
                        WHEN p.picket > n.pk_to THEN p.picket - n.pk_to ELSE 0 END AS dist
            FROM pk p JOIN node n USING (object_id, gallery)
        )
        SELECT object_id, gallery, picket,
               CAST(arg_min(node, dist * 1000 + node) AS INTEGER) AS section,
               min(dist) AS entry_dist
        FROM near GROUP BY ALL
        UNION ALL
        SELECT p.object_id, p.gallery, p.picket,
               CAST(1000 + floor(p.picket / {BIN_PICKETS}) AS INTEGER), NULL
        FROM pk p
        WHERE NOT EXISTS (SELECT 1 FROM node n
                          WHERE n.object_id = p.object_id AND n.gallery = p.gallery)
        """
    )
    contacts = ", ".join(f"'{s}'" for s in CONTACT_STYPES)
    con.execute(
        f"""
        CREATE OR REPLACE TABLE section_sensors AS
        SELECT sm.object_id, sm.gallery, sm.section,
               count(DISTINCT ch.cid) FILTER (ch.stype IN ({contacts})) AS n_contact
        FROM section_map sm
        JOIN read_parquet('{channels}') ch
          ON ch.oid = sm.object_id AND ch.gal_key = sm.gallery AND ch.picket = sm.picket
        GROUP BY ALL
        """
    )
    con.execute(
        f"COPY (SELECT * FROM section_map ORDER BY 1, 2, 3) "
        f"TO '{SECTIONS.as_posix()}' (FORMAT PARQUET)"
    )
    con.execute(
        f"""
        COPY (
            SELECT sm.object_id, sm.gallery, sm.section,
                   CAST(sm.gallery <> -1 AS INTEGER) AS is_branch,
                   CAST(sm.section >= 1000 AS INTEGER) AS no_entry,
                   count(DISTINCT sm.picket) AS n_pickets,
                   count(DISTINCT ch.cid) FILTER (ch.stype IN ({contacts})) AS n_contact,
                   count(DISTINCT ch.cid) FILTER (ch.stype = '{MOTION_STYPE}') AS n_motion,
                   count(DISTINCT ch.cid) FILTER (ch.stype = 'КД АВ') AS n_emergency_exit,
                   count(DISTINCT ch.cid) FILTER (ch.stype IN ('КД Люк', '9-секционный люк'))
                       AS n_hatch,
                   count(DISTINCT ch.cid) FILTER (ch.nm ILIKE '%ВШ%') AS n_vent_shaft
            FROM section_map sm
            JOIN read_parquet('{channels}') ch
              ON ch.oid = sm.object_id AND ch.gal_key = sm.gallery AND ch.picket = sm.picket
            GROUP BY ALL ORDER BY 1, 2, 3
        ) TO '{SECTION_INFO.as_posix()}' (FORMAT PARQUET)
        """
    )


def build_moments(con: duckdb.DuckDBPyConnection) -> None:
    """Таблица `moment`: момент тревоги доступа это «канал, отметка времени».
    Пачка записей в одну секунду весит как один момент."""
    events, channels = _src()
    contacts = ", ".join(f"'{s}'" for s in CONTACT_STYPES)
    con.execute(
        f"""
        CREATE OR REPLACE TABLE moment AS
        SELECT
            ev.channel_id AS channel_id,
            ch.oid AS object_id,
            ch.gal_key AS gallery,
            sm.section AS section,
            sm.entry_dist AS entry_dist,
            CASE WHEN ch.stype = '{MOTION_STYPE}' THEN 'motion' ELSE 'contact' END AS kind,
            ev.d + ev.t AS ts,
            count(*) AS n_rows
        FROM read_parquet('{events}') ev
        JOIN read_parquet('{channels}') ch ON ch.cid = ev.channel_id
        JOIN section_map sm
          ON sm.object_id = ch.oid AND sm.gallery = ch.gal_key AND sm.picket = ch.picket
        JOIN commissioning cm ON cm.channel_id = ev.channel_id
        WHERE ev.alarm
          AND ((ev.value = '{CONTACT_VALUE}' AND ch.stype IN ({contacts}))
               OR (ev.value = '{MOTION_VALUE}' AND ch.stype = '{MOTION_STYPE}'))
          AND ev.d >= cm.ready_day
          AND NOT (ch.oid = {ARTIFACT_OID} AND year(ev.d) IN {ARTIFACT_YEARS})
        GROUP BY ALL
        """
    )


def build_units(con: duckdb.DuckDBPyConnection) -> None:
    """Срок жизни единицы считается по всем записям её каналов доступа, а не по
    одним тревогам: канал молчит годами и всё это время наблюдается."""
    events, channels = _src()
    con.execute(
        f"""
        COPY (
            WITH access AS (
                SELECT DISTINCT channel_id, object_id, gallery, section FROM moment
            ),
            life AS (
                SELECT ev.channel_id,
                       greatest(min(ev.d), max(cm.ready_day)) AS d_from,
                       max(ev.d) AS d_to
                FROM read_parquet('{events}') ev
                JOIN read_parquet('{channels}') ch ON ch.cid = ev.channel_id
                JOIN commissioning cm ON cm.channel_id = ev.channel_id
                WHERE ev.channel_id IN (SELECT channel_id FROM access)
                GROUP BY 1
            )
            SELECT
                a.object_id, a.gallery, a.section,
                date_trunc('hour', min(life.d_from)::TIMESTAMP) AS hour_from,
                date_trunc('hour', max(life.d_to)::TIMESTAMP + INTERVAL 23 HOUR) AS hour_to,
                count(DISTINCT a.channel_id) AS n_channels
            FROM access a
            JOIN life ON life.channel_id = a.channel_id
            GROUP BY 1, 2, 3
            ORDER BY 1, 2, 3
        ) TO '{UNITS.as_posix()}' (FORMAT PARQUET)
        """
    )


def build_disarm_windows(con: duckdb.DuckDBPyConnection) -> None:
    """Окно открывает смена режима на «снято», закрывает следующая смена на «на
    охране». Окно без закрытия длится до последней записи режима на объекте."""
    events, channels = _src()
    con.execute(
        f"""
        CREATE OR REPLACE TABLE guard AS
        SELECT ch.oid AS object_id, ev.d + ev.t AS ts, ev.value AS value
        FROM read_parquet('{events}') ev
        JOIN read_parquet('{channels}') ch ON ch.cid = ev.channel_id
        WHERE ch.stype = '{GUARD_TYPE}'
          AND ev.value IN ('{GUARD_ON}', '{GUARD_OFF}')
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE disarm_window AS
        WITH step AS (
            SELECT object_id, ts, value FROM (
                SELECT object_id, ts, value,
                       lag(value) OVER (PARTITION BY object_id ORDER BY ts, value) AS prev
                FROM guard
            )
            WHERE prev IS NULL OR prev <> value
        ),
        pair AS (
            SELECT object_id, ts AS t_from, value,
                   lead(ts) OVER (PARTITION BY object_id ORDER BY ts) AS t_to
            FROM step
        ),
        seen AS (SELECT object_id, max(ts) AS seen_to FROM guard GROUP BY 1)
        SELECT p.object_id, p.t_from, coalesce(p.t_to, s.seen_to) AS t_to,
               p.t_to IS NULL AS open_end
        FROM pair p JOIN seen s USING (object_id)
        WHERE p.value = '{GUARD_OFF}'
        ORDER BY 1, 2
        """
    )
    con.execute(f"COPY disarm_window TO '{DISARM.as_posix()}' (FORMAT PARQUET)")

    # Пауза дольше срока внутри истории и хвост после последней смены режима.
    silence = GUARD_SILENCE_DAYS * 24
    con.execute(
        f"""
        COPY (
            SELECT object_id, t_from, t_to FROM (
                SELECT object_id, lag(ts) OVER (PARTITION BY object_id ORDER BY ts) AS t_from,
                       ts AS t_to
                FROM guard
            )
            WHERE date_diff('hour', t_from, t_to) > {silence}
            UNION ALL
            SELECT object_id, max(ts), TIMESTAMP '{DUMP_END}'
            FROM guard GROUP BY 1
            HAVING date_diff('hour', max(ts), TIMESTAMP '{DUMP_END}') > {silence}
            ORDER BY 1, 2
        ) TO '{GUARD_UNKNOWN.as_posix()}' (FORMAT PARQUET)
        """
    )


def build_hourly(con: duckdb.DuckDBPyConnection) -> None:
    """Часовые своды. Свод всех тревог держит каждый момент. Свод метки держит
    только моменты инцидентов, которые правило признало событием. Тревога на
    объекте без канала режима остаётся в обоих: режим там неизвестен."""
    con.execute(
        f"""
        CREATE OR REPLACE TABLE moment_flag AS
        WITH f AS (
            SELECT m.*,
                exists(SELECT 1 FROM disarm_window w
                       WHERE w.object_id = m.object_id
                         AND m.ts >= w.t_from AND m.ts < w.t_to) AS off_guard
            FROM moment m
        ),
        armed AS (
            SELECT *, sum(is_new) OVER (PARTITION BY object_id, gallery, section ORDER BY ts
                                        ROWS UNBOUNDED PRECEDING) AS incident
            FROM (
                SELECT *, CASE WHEN date_diff('minute', lag(ts) OVER w, ts)
                                    <= {INCIDENT_GAP_MINUTES} THEN 0 ELSE 1 END AS is_new
                FROM f LEFT JOIN section_sensors USING (object_id, gallery, section)
                WHERE NOT off_guard
                WINDOW w AS (PARTITION BY object_id, gallery, section ORDER BY ts)
            )
        ),
        valid AS (
            SELECT object_id, gallery, section, incident
            FROM armed GROUP BY ALL
            HAVING bool_or(kind = 'contact')
                OR count(DISTINCT channel_id) FILTER (kind = 'motion') >= 2
                OR min(entry_dist) <= {NEAR_ENTRY_PICKETS}
                OR coalesce(max(n_contact), 0) < 2
        )
        SELECT f.*, v.incident IS NOT NULL AS event
        FROM f
        LEFT JOIN armed a USING (channel_id, ts)
        LEFT JOIN valid v
          ON v.object_id = a.object_id AND v.gallery = a.gallery
         AND v.section = a.section AND v.incident = a.incident
        """
    )
    for path, where in ((HOURLY, "TRUE"), (ARMED, "event")):
        con.execute(
            f"""
            COPY (
                SELECT object_id, gallery, section,
                       date_trunc('hour', ts) AS hour,
                       sum(n_rows) AS n_alarms,
                       count(*) AS n_moments,
                       count(DISTINCT channel_id) AS n_channels
                FROM moment_flag
                WHERE {where}
                GROUP BY 1, 2, 3, 4
                ORDER BY 1, 2, 3, 4
            ) TO '{path.as_posix()}' (FORMAT PARQUET)
            """
        )


def build_faults(con: duckdb.DuckDBPyConnection) -> None:
    """Записи «Неисправен» на любых каналах участка по суткам. Отказ датчика
    на участке предшествует тревогам доступа: первую неделю после него доля
    суток с тревогой 29–33 % против 19 % позже."""
    events, channels = _src()
    con.execute(
        f"""
        COPY (
            SELECT sm.object_id, sm.gallery, sm.section, ev.d AS day,
                   count(*) AS n_fault, count(DISTINCT ev.channel_id) AS n_fault_channels
            FROM read_parquet('{events}') ev
            JOIN read_parquet('{channels}') ch ON ch.cid = ev.channel_id
            JOIN section_map sm
              ON sm.object_id = ch.oid AND sm.gallery = ch.gal_key AND sm.picket = ch.picket
            WHERE ev.value = 'Неисправен'
            GROUP BY ALL ORDER BY 1, 2, 3, 4
        ) TO '{FAULTS.as_posix()}' (FORMAT PARQUET)
        """
    )


def build_chain(con: duckdb.DuckDBPyConnection) -> None:
    """Инцидент, в котором за контактом входа пошло движение на том же участке
    в пределах `INCIDENT_GAP_MINUTES`. Это сценарий заказчика из ответа 2.8."""
    con.execute(
        f"""
        COPY (
            SELECT c.object_id, c.gallery, c.section, min(m.ts) AS completed_ts
            FROM moment_flag c
            JOIN moment_flag m
              ON m.object_id = c.object_id AND m.gallery = c.gallery
             AND m.section = c.section AND m.kind = 'motion'
             AND m.ts > c.ts AND m.ts <= c.ts + INTERVAL {INCIDENT_GAP_MINUTES} MINUTE
            WHERE c.kind = 'contact'
            GROUP BY c.object_id, c.gallery, c.section, c.ts
            ORDER BY 1, 2, 3, 4
        ) TO '{CHAIN.as_posix()}' (FORMAT PARQUET)
        """
    )


def _is_school_vacation(day: dt.date) -> bool:
    now = (day.month, day.day)
    for m_from, d_from, m_to, d_to in SCHOOL_VACATIONS:
        start, end = (m_from, d_from), (m_to, d_to)
        if start <= end:
            if start <= now <= end:
                return True
        elif now >= start or now <= end:
            return True
    return False


def build_calendar(
    con: duckdb.DuckDBPyConnection,
    first: dt.date = dt.date(2018, 1, 1),
    last: dt.date = dt.date(2027, 12, 31),
) -> None:
    """Представление `calendar`: одна строка на дату. Отрезок шире выгрузки,
    потому что длина цепочки выходных смотрит за край периода."""
    rows = []
    day = first
    while day <= last:
        rows.append((
            day,
            int((day.month, day.day) in FIXED_HOLIDAYS),
            int(day.weekday() >= 5),
            int(_is_school_vacation(day)),
            day.weekday(),
            day.month,
            (day.month - 1) // 3 + 1,
            (day.month % 12) // 3,  # зима 0, весна 1, лето 2, осень 3
            day.day,
            day.isocalendar()[1],
            day.timetuple().tm_yday,
        ))
        day += dt.timedelta(days=1)

    con.execute(
        """
        CREATE OR REPLACE TABLE calendar_day (
            day DATE PRIMARY KEY, is_holiday INTEGER, is_weekend INTEGER,
            is_school_vacation INTEGER, day_of_week INTEGER, month INTEGER,
            quarter INTEGER, season INTEGER, day_of_month INTEGER,
            week_of_year INTEGER, day_of_year INTEGER
        )
        """
    )
    con.executemany(
        "INSERT INTO calendar_day VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
    )
    con.execute(
        """
        CREATE OR REPLACE VIEW calendar AS
        WITH base AS (
            SELECT *, CAST(is_holiday = 1 OR is_weekend = 1 AS INTEGER) AS is_day_off
            FROM calendar_day
        ),
        runs AS (
            SELECT *, sum(CASE WHEN is_day_off = 0 THEN 1 ELSE 0 END)
                          OVER (ORDER BY day) AS work_run
            FROM base
        ),
        chains AS (
            SELECT *, CASE WHEN is_day_off = 1
                           THEN count(*) OVER (PARTITION BY work_run, is_day_off)
                           ELSE 0 END AS day_off_chain
            FROM runs
        )
        SELECT day, is_holiday, is_weekend, is_school_vacation, is_day_off,
               day_off_chain, CAST(day_off_chain >= 3 AS INTEGER) AS is_long_weekend,
               day_of_week, month, quarter, season, day_of_month, week_of_year,
               day_of_year
        FROM chains
        """
    )


def stats(con: duckdb.DuckDBPyConnection) -> dict[str, object]:
    def one(sql: str) -> object:
        return con.execute(sql).fetchone()[0]

    def parquet(path: pathlib.Path) -> str:
        return f"read_parquet('{path.as_posix()}')"

    time_off = con.execute(
        """
        SELECT sum(date_diff('second', w.t_from, w.t_to)) / (
            SELECT sum(date_diff('second', a, b))
            FROM (SELECT min(ts) AS a, max(ts) AS b FROM guard GROUP BY object_id))
        FROM disarm_window w
        """
    ).fetchone()[0]
    return {
        "sections": one(f"SELECT count(DISTINCT (object_id, gallery, section)) FROM {parquet(SECTIONS)}"),
        "units": one(f"SELECT count(*) FROM {parquet(UNITS)}"),
        "channels": int(one(f"SELECT sum(n_channels) FROM {parquet(UNITS)}")),
        "alarm_hours_all": one(f"SELECT count(*) FROM {parquet(HOURLY)}"),
        "alarm_hours_armed": one(f"SELECT count(*) FROM {parquet(ARMED)}"),
        "moments": one("SELECT count(*) FROM moment_flag"),
        "moments_armed_not_event": one(
            "SELECT count(*) FROM moment_flag WHERE NOT off_guard AND NOT event"
        ),
        "moments_off_guard_pct": round(
            100.0 * one("SELECT avg(off_guard::INTEGER) FROM moment_flag"), 3
        ),
        "disarm_windows": one("SELECT count(*) FROM disarm_window"),
        "guard_unknown_periods": one(f"SELECT count(*) FROM {parquet(GUARD_UNKNOWN)}"),
        "time_off_guard_pct": round(100.0 * float(time_off), 3),
        "chains": one(f"SELECT count(*) FROM {parquet(CHAIN)}"),
        "period": [
            str(one(f"SELECT min(hour) FROM {parquet(HOURLY)}")),
            str(one(f"SELECT max(hour) FROM {parquet(HOURLY)}")),
        ],
    }


def main() -> None:
    OUT.mkdir(exist_ok=True)
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    con.execute("PRAGMA memory_limit='6GB'")

    steps = (
        ("ввод объектов в работу", build_commissioning),
        ("участки", build_sections),
        ("моменты тревоги", build_moments),
        ("окна «Снято с охраны»", build_disarm_windows),
        ("срок жизни единиц", build_units),
        ("часовые своды", build_hourly),
        ("цепочки доступа", build_chain),
        ("отказы датчиков", build_faults),
        ("календарь", build_calendar),
    )
    for name, step in steps:
        t = time.time()
        step(con)
        print(f"{name}: {time.time() - t:.0f} c")
    con.execute(
        f"COPY (SELECT * FROM calendar ORDER BY day) "
        f"TO '{CALENDAR.as_posix()}' (FORMAT PARQUET)"
    )

    result = stats(con)
    STATS.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
