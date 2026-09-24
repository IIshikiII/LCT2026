"""Считает признаки направления «несанкционированный доступ» на момент расчёта
`at`. Признаки строятся по `out/access_hourly.parquet`, а не по
`out/access_hourly_armed.parquet`: метку решение ADR
`backend/docs/adr/0001-access-target.md` (раздел «Что из этого следует») велит
чистить от тревог подрядчика, а признакам эти тревоги оставить. Тревога внутри
окна «Снято с охраны» не проникновение для метки, но для признака это всё
равно активность на месте, и выбрасывать её значит терять сигнал о частоте
событий на пикете.

Разбор четырнадцати признаков и решения по неоднозначным формулировкам
backlog-строк T05/T06 (`loop/BACKLOG.md`):

- `alarm_hour_share_720h` считает долю тревожных часов за 30 суток, а не долю
  тревожных записей среди всех: `access_hourly` держит только строки с
  тревогой, и второе число из него не достать. Формула повторяет признак
  `duty` (`active_days / lifetime_days`) из `notebooks/01-eda-clustering.ipynb`,
  только на часовой сетке вместо суточной.
- `hours_since_last_alarm` для единицы без тревог в истории берёт не 0 и не
  бесконечность, а `hour_from` из `access_units`: это число часов, что мы
  наблюдаем единицу, и оно всегда определено.
- `night_share` считает ночную долю по всей истории тревог единицы до `at`,
  без окна в 720 часов. Формула и граница ночи (22–6 часов) повторяют признак
  «ночная доля» из `03_disarm.py`. Единица без истории получает 0, а не 0,5:
  это документированное значение по умолчанию, не оценка «пополам».
- `day_of_week` следует соглашению DuckDB: 0 это воскресенье, 6 это суббота
  (проверено запросом `dayofweek()` на известных датах).
- `neighbor_channels_1h` берёт `max(n_channels)` за последний завершённый час:
  `n_channels` в `access_hourly` уже число каналов пикета в тревоге за час, и
  агрегат `max` по одной строке ничего не меняет, но защищает от случая, когда
  часовой свод склеит несколько строк на одну единицу и час.

Каждый фильтр обязан читать `hour < at`, никогда `hour <= at`: признак не
имеет права видеть час расчёта и позже, иначе прогноз смотрит в будущее.

Нижняя граница окна включает свой час: `hour >= at - N HOUR`. Открытая
граница `hour > at - 1 HOUR` не оставляет часовому окну ни одного часа, потому
что и `hour`, и `at` стоят на границе часа. Признаки `n_alarms_1h` и
`neighbor_channels_1h` до задачи T35 были поэтому нулями на всей панели.

Признаки T06 берут дело QA-сессии §2.8: «проникновением считается
последовательность сработок: дверь открыли, потом сработал объёмный датчик,
потом движение туда и обратно».

- `is_disarmed` берёт окна из `out/disarm_windows.parquet` (T04, `03_disarm.py`)
  готовыми, второй раз их не строит. Значение 1, когда `at` попадает в окно
  «Снято с охраны» того же объекта: `t_from <= at < t_to`. Момент открытия окна
  входит в «снято», момент закрытия — уже нет, тем же правилом, что окна
  вычитает метка в версии Б.
- `has_access_sequence` смотрит на исходные события `eda/out/events.parquet`, а
  не на часовой свод: последовательность живёт внутри одного часа и меньше, и
  свод её теряет. Тип датчика берётся из `stype`
  (`notebooks/out/channel_features.parquet`). Соответствие проверено запросом
  по частоте пар «тип, значение» на алармах доступа:
    - «дверь» — каналы `КД Дверь`, `КД Люк`, `КД АВ` со значением
      «Не замкнут»: имя `КД` (контроль двери) и единственное осмысленное
      значение указывают на дверной или люковый контакт;
    - «объёмный датчик» — канал `Состояние УИР-Р`: «Р» в имени и то, что канал
      не встречается больше нигде в списке доступа, соответствуют
      радиоволновому охранному извещателю, то есть объёмному датчику;
    - «движение» — канал `Датчик движения` со значением «Обнаружено
      движение»: имя совпадает с описанием заказчика буквально.
  Признак равен 1, когда на объекте и галерее нашлась завершённая цепочка
  «дверь → объёмный датчик → движение» в строгом порядке отметок времени,
  каждый шаг на пикете не дальше `NEAR_PICKETS` от пикета точки `p` (близкий
  пикет), вся цепочка уместилась в окно `SEQUENCE_WINDOW_MINUTES` минут и
  закончилась в последние `SEQUENCE_RECENCY_HOURS` часов до `at`. Длина
  цепочки и срок её видимости — два разных числа. До задачи T35 оба равнялись
  пятнадцати минутам, и признак ловил только цепочки, закрытые в последнюю
  четверть часа перед границей часа: 19 клеток панели из 1 581 480. Срок
  видимости равен горизонту прогноза. Пикет и окно — оценка: пикет держит 10 метров
  (`docs/customer/smvu-report.md` §1), пять пикетов это 50 метров прохода,
  пятнадцать минут — короткий визит, а не рабочая смена. Оба числа документированы как решение,
  не как измерение: данных, размечающих настоящие проникновения, нет.

Признаки T08b (`loop/BACKLOG.md`) читают `out/access_hourly_armed.parquet` —
тот же источник, что и метка, а не `access_hourly.parquet`. T08 нашёл, что без
такого признака модель проигрывает наивной планке «тревога была вчера»: планка
считает по тревогам вне окон «Снято с охраны», а признаки до T08b — по всем
тревогам, и модель не видела того, на чём построена планка.

- `n_armed_alarms_24h`, `n_armed_alarms_168h` считают число сработавших
  моментов (`sum(n_moments)`) в часовом своде `armed` за 24 и 168 часов до
  `at`, тем же правилом `hour < at`, что и `n_alarms_*`.
- `hours_since_last_armed_alarm` берёт часы с последней тревоги на охране тем
  же способом, что `hours_since_last_alarm`: единица без такой тревоги в
  истории получает `hour_from` вместо 0 или бесконечности.
"""
import json
import pathlib
import time

import duckdb

ROOT = pathlib.Path(__file__).resolve().parents[2]
EVENTS = ROOT / "eda" / "out" / "events.parquet"
CHANNELS = ROOT / "notebooks" / "out" / "channel_features.parquet"
OUT = pathlib.Path(__file__).resolve().parent / "out"
HOURLY = OUT / "access_hourly.parquet"
UNITS = OUT / "access_units.parquet"
ARMED = OUT / "access_hourly_armed.parquet"
DISARM = OUT / "disarm_windows.parquet"
OUT_FEATURES = OUT / "access_features.parquet"
STATS = OUT / "features_stats.json"

KEY = "object_id, gallery, picket"
HORIZON_HOURS = 24
# Доля отрицательных клеток, что остаётся в обучающей панели. Полная сетка
# «единица и час» держит 62,6 млн клеток (ADR 0001), и обучать модель на всех
# них дорого без выигрыша в качестве.
NEGATIVE_KEEP_RATE = 0.02
# Дальность «близкого пикета» и длина «короткого окна» для последовательности
# доступа. Обоснование — в docstring модуля, раздел про T06.
NEAR_PICKETS = 5
SEQUENCE_WINDOW_MINUTES = 15
# Срок, в течение которого завершённая цепочка остаётся видна точке расчёта.
# Равен горизонту прогноза: цепочка за сутки до `at` это след визита, а не шум.
SEQUENCE_RECENCY_HOURS = HORIZON_HOURS

DOOR_STYPES = ("КД Дверь", "КД Люк", "КД АВ")
VOLUMETRIC_STYPE = "Состояние УИР-Р"
MOTION_STYPE = "Датчик движения"

FEATURE_COLUMNS = [
    "n_alarms_1h",
    "n_alarms_24h",
    "n_alarms_168h",
    "n_alarms_720h",
    "alarm_hour_share_720h",
    "hours_since_last_alarm",
    "night_share",
    "hour_of_day",
    "day_of_week",
    "month",
    "is_weekend",
    "neighbor_channels_1h",
    "is_disarmed",
    "has_access_sequence",
    "n_armed_alarms_24h",
    "n_armed_alarms_168h",
    "hours_since_last_armed_alarm",
]


def attach_sources(
    con: duckdb.DuckDBPyConnection,
    hourly: pathlib.Path = HOURLY,
    units: pathlib.Path = UNITS,
    disarm: pathlib.Path = DISARM,
    armed: pathlib.Path = ARMED,
) -> None:
    """Подключает часовой свод, границы жизни единиц, окна «Снято с охраны» и
    часовой свод тревог на охране как представления `alarm`, `unit`,
    `disarm_window` и `armed`."""
    con.execute(
        f"CREATE OR REPLACE VIEW alarm AS "
        f"SELECT * FROM read_parquet('{hourly.as_posix()}')"
    )
    con.execute(
        f"CREATE OR REPLACE VIEW unit AS "
        f"SELECT * FROM read_parquet('{units.as_posix()}')"
    )
    con.execute(
        f"CREATE OR REPLACE VIEW disarm_window AS "
        f"SELECT * FROM read_parquet('{disarm.as_posix()}')"
    )
    con.execute(
        f"CREATE OR REPLACE VIEW armed AS "
        f"SELECT * FROM read_parquet('{armed.as_posix()}')"
    )


def build_sequence_chain(
    con: duckdb.DuckDBPyConnection,
    events: pathlib.Path = EVENTS,
    channels: pathlib.Path = CHANNELS,
) -> None:
    """Строит представление `access_chain`: завершённые цепочки «дверь →
    объёмный датчик → движение» на близких пикетах внутри короткого окна.
    Разбор типов датчиков и констант окна — в docstring модуля."""
    door_list = ", ".join(f"'{s}'" for s in DOOR_STYPES)
    con.execute(
        f"""
        CREATE OR REPLACE VIEW access_moment AS
        SELECT
            ch.oid AS object_id,
            ch.gal_key AS gallery,
            ch.picket AS picket,
            ev.d + ev.t AS ts,
            CASE
                WHEN ch.stype IN ({door_list}) AND ev.value = 'Не замкнут'
                    THEN 'door'
                WHEN ch.stype = '{VOLUMETRIC_STYPE}' THEN 'volumetric'
                WHEN ch.stype = '{MOTION_STYPE}' AND ev.value = 'Обнаружено движение'
                    THEN 'motion'
            END AS category
        FROM read_parquet('{events.as_posix()}') ev
        JOIN read_parquet('{channels.as_posix()}') ch ON ch.cid = ev.channel_id
        WHERE ev.alarm AND ch.picket IS NOT NULL
          AND (
                (ch.stype IN ({door_list}) AND ev.value = 'Не замкнут')
             OR ch.stype = '{VOLUMETRIC_STYPE}'
             OR (ch.stype = '{MOTION_STYPE}' AND ev.value = 'Обнаружено движение')
          )
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE VIEW access_door_vol AS
        SELECT d.object_id, d.gallery, d.picket AS door_picket, d.ts AS door_ts,
               min(v.ts) AS vol_ts
        FROM access_moment d
        JOIN access_moment v
          ON v.object_id = d.object_id AND v.gallery = d.gallery
          AND d.category = 'door' AND v.category = 'volumetric'
          AND abs(v.picket - d.picket) <= {NEAR_PICKETS}
          AND v.ts > d.ts AND v.ts <= d.ts + INTERVAL {SEQUENCE_WINDOW_MINUTES} MINUTE
        GROUP BY 1, 2, 3, 4
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE VIEW access_chain AS
        SELECT dv.object_id, dv.gallery, dv.door_picket AS picket,
               min(m.ts) AS completed_ts
        FROM access_door_vol dv
        JOIN access_moment m
          ON m.object_id = dv.object_id AND m.gallery = dv.gallery
          AND m.category = 'motion'
          AND abs(m.picket - dv.door_picket) <= {NEAR_PICKETS}
          AND m.ts > dv.vol_ts AND m.ts <= dv.door_ts + INTERVAL {SEQUENCE_WINDOW_MINUTES} MINUTE
        GROUP BY 1, 2, 3
        """
    )


def build_features(
    con: duckdb.DuckDBPyConnection,
    points: str = "points",
    alarm: str = "alarm",
    unit: str = "unit",
) -> str:
    """Строит представление `features` над точками `points(object_id, gallery,
    picket, at)`. Каждое окно строго до `at`, час расчёта в него не входит.
    """
    con.execute(
        f"""
        CREATE OR REPLACE VIEW features_raw AS
        SELECT
            p.object_id,
            p.gallery,
            p.picket,
            p.at,
            (SELECT sum(a.n_moments) FROM {alarm} a
             WHERE (a.object_id, a.gallery, a.picket) = (p.object_id, p.gallery, p.picket)
               AND a.hour >= p.at - INTERVAL 1 HOUR AND a.hour < p.at) AS n_alarms_1h,
            (SELECT sum(a.n_moments) FROM {alarm} a
             WHERE (a.object_id, a.gallery, a.picket) = (p.object_id, p.gallery, p.picket)
               AND a.hour > p.at - INTERVAL 24 HOUR AND a.hour < p.at) AS n_alarms_24h,
            (SELECT sum(a.n_moments) FROM {alarm} a
             WHERE (a.object_id, a.gallery, a.picket) = (p.object_id, p.gallery, p.picket)
               AND a.hour > p.at - INTERVAL 168 HOUR AND a.hour < p.at) AS n_alarms_168h,
            (SELECT sum(a.n_moments) FROM {alarm} a
             WHERE (a.object_id, a.gallery, a.picket) = (p.object_id, p.gallery, p.picket)
               AND a.hour > p.at - INTERVAL 720 HOUR AND a.hour < p.at) AS n_alarms_720h,
            (SELECT count(*) FROM {alarm} a
             WHERE (a.object_id, a.gallery, a.picket) = (p.object_id, p.gallery, p.picket)
               AND a.hour > p.at - INTERVAL 720 HOUR AND a.hour < p.at) AS alarm_hours_720h,
            (SELECT max(a.hour) FROM {alarm} a
             WHERE (a.object_id, a.gallery, a.picket) = (p.object_id, p.gallery, p.picket)
               AND a.hour < p.at) AS last_alarm_hour,
            (SELECT u.hour_from FROM {unit} u
             WHERE (u.object_id, u.gallery, u.picket) = (p.object_id, p.gallery, p.picket)) AS hour_from,
            (SELECT sum(a.n_moments) FILTER (hour(a.hour) >= 22 OR hour(a.hour) < 6)
             FROM {alarm} a
             WHERE (a.object_id, a.gallery, a.picket) = (p.object_id, p.gallery, p.picket)
               AND a.hour < p.at) AS night_moments,
            (SELECT sum(a.n_moments) FROM {alarm} a
             WHERE (a.object_id, a.gallery, a.picket) = (p.object_id, p.gallery, p.picket)
               AND a.hour < p.at) AS past_moments,
            (SELECT max(a.n_channels) FROM {alarm} a
             WHERE (a.object_id, a.gallery, a.picket) = (p.object_id, p.gallery, p.picket)
               AND a.hour >= p.at - INTERVAL 1 HOUR AND a.hour < p.at) AS neighbor_channels_1h,
            CASE WHEN EXISTS (
                SELECT 1 FROM disarm_window w
                WHERE w.object_id = p.object_id
                  AND p.at >= w.t_from AND p.at < w.t_to
            ) THEN 1 ELSE 0 END AS is_disarmed,
            CASE WHEN EXISTS (
                SELECT 1 FROM access_chain c
                WHERE c.object_id = p.object_id AND c.gallery = p.gallery
                  AND abs(c.picket - p.picket) <= {NEAR_PICKETS}
                  AND c.completed_ts < p.at
                  AND c.completed_ts >= p.at - INTERVAL {SEQUENCE_RECENCY_HOURS} HOUR
            ) THEN 1 ELSE 0 END AS has_access_sequence,
            (SELECT sum(a.n_moments) FROM armed a
             WHERE (a.object_id, a.gallery, a.picket) = (p.object_id, p.gallery, p.picket)
               AND a.hour > p.at - INTERVAL 24 HOUR AND a.hour < p.at) AS n_armed_alarms_24h,
            (SELECT sum(a.n_moments) FROM armed a
             WHERE (a.object_id, a.gallery, a.picket) = (p.object_id, p.gallery, p.picket)
               AND a.hour > p.at - INTERVAL 168 HOUR AND a.hour < p.at) AS n_armed_alarms_168h,
            (SELECT max(a.hour) FROM armed a
             WHERE (a.object_id, a.gallery, a.picket) = (p.object_id, p.gallery, p.picket)
               AND a.hour < p.at) AS last_armed_alarm_hour
        FROM {points} p
        """
    )
    con.execute(
        """
        CREATE OR REPLACE VIEW features AS
        SELECT
            object_id,
            gallery,
            picket,
            "at",
            coalesce(n_alarms_1h, 0) AS n_alarms_1h,
            coalesce(n_alarms_24h, 0) AS n_alarms_24h,
            coalesce(n_alarms_168h, 0) AS n_alarms_168h,
            coalesce(n_alarms_720h, 0) AS n_alarms_720h,
            coalesce(alarm_hours_720h, 0) / 720.0 AS alarm_hour_share_720h,
            date_diff(
                'hour',
                coalesce(last_alarm_hour, hour_from),
                "at"
            ) AS hours_since_last_alarm,
            coalesce(night_moments, 0) / greatest(coalesce(past_moments, 0), 1) AS night_share,
            hour("at") AS hour_of_day,
            dayofweek("at") AS day_of_week,
            month("at") AS month,
            CAST(dayofweek("at") IN (0, 6) AS INTEGER) AS is_weekend,
            coalesce(neighbor_channels_1h, 0) AS neighbor_channels_1h,
            is_disarmed,
            has_access_sequence,
            coalesce(n_armed_alarms_24h, 0) AS n_armed_alarms_24h,
            coalesce(n_armed_alarms_168h, 0) AS n_armed_alarms_168h,
            date_diff(
                'hour',
                coalesce(last_armed_alarm_hour, hour_from),
                "at"
            ) AS hours_since_last_armed_alarm
        FROM features_raw
        """
    )
    return "features"


if __name__ == "__main__":
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    con.execute("PRAGMA memory_limit='6GB'")
    con.execute("SELECT setseed(0.4217)")

    attach_sources(con)

    t = time.time()
    build_sequence_chain(con)
    n_chains = con.execute("SELECT count(*) FROM access_chain").fetchone()[0]
    print(f"цепочек «дверь → объёмный датчик → движение»: {n_chains}, посчитано за {time.time() - t:.0f} c")

    # Положительные точки: те же 24 часа перед каждым часом с тревогой в
    # версии Б метки, что и в `target_stats.hourly_stats`, обрезанные по жизни
    # единицы.
    t = time.time()
    con.execute(
        f"""
        CREATE TABLE positive AS
        SELECT DISTINCT p.object_id, p.gallery, p.picket, p.hour AS at
        FROM (
            SELECT {KEY}, hour - (s.i * INTERVAL 1 HOUR) AS hour
            FROM armed, generate_series(1, {HORIZON_HOURS}) s(i)
        ) p
        JOIN unit u USING ({KEY})
        WHERE p.hour BETWEEN u.hour_from AND u.hour_to
        """
    )
    n_positive = con.execute("SELECT count(*) FROM positive").fetchone()[0]
    print(f"положительных точек: {n_positive}, посчитано за {time.time() - t:.0f} c")

    # Отрицательные точки: случайные часы жизни каждой единицы, доля
    # `NEGATIVE_KEEP_RATE` от длины жизни. Столкновения с положительным
    # множеством убираются анти-join, чтобы точка не несла обе метки.
    t = time.time()
    con.execute(
        f"""
        CREATE TABLE negative_raw AS
        SELECT
            {KEY},
            hour_from + (
                (random() * date_diff('hour', hour_from, hour_to))::BIGINT * INTERVAL 1 HOUR
            ) AS at
        FROM unit, generate_series(
            1, CAST(ceil(
                (date_diff('hour', hour_from, hour_to) + 1) * {NEGATIVE_KEEP_RATE}
            ) AS BIGINT)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE negative AS
        SELECT DISTINCT n.object_id, n.gallery, n.picket, n.at
        FROM negative_raw n
        ANTI JOIN positive p ON (n.object_id, n.gallery, n.picket, n.at)
                              = (p.object_id, p.gallery, p.picket, p.at)
        """
    )
    n_negative = con.execute("SELECT count(*) FROM negative").fetchone()[0]
    print(f"отрицательных точек: {n_negative}, посчитано за {time.time() - t:.0f} c")

    con.execute(
        """
        CREATE TABLE points AS
        SELECT object_id, gallery, picket, "at", 1 AS label FROM positive
        UNION ALL
        SELECT object_id, gallery, picket, "at", 0 AS label FROM negative
        """
    )

    t = time.time()
    build_features(con)
    con.execute(
        f"""
        COPY (
            SELECT f.*, pt.label
            FROM features f
            JOIN points pt USING (object_id, gallery, picket, "at")
            ORDER BY object_id, gallery, picket, "at"
        ) TO '{OUT_FEATURES.as_posix()}' (FORMAT PARQUET)
        """
    )
    print(f"признаки посчитаны за {time.time() - t:.0f} c")

    stats = con.execute(
        f"""
        SELECT
            count(*),
            count(*) FILTER (label = 1),
            count(*) FILTER (label = 0),
            count(DISTINCT (object_id, gallery, picket)),
            min(night_share),
            max(night_share),
            min(alarm_hour_share_720h),
            max(alarm_hour_share_720h),
            min(hours_since_last_alarm),
            avg(is_disarmed) FILTER (label = 1),
            avg(is_disarmed) FILTER (label = 0),
            avg(has_access_sequence) FILTER (label = 1),
            avg(has_access_sequence) FILTER (label = 0)
        FROM (
            SELECT f.*, pt.label
            FROM read_parquet('{OUT_FEATURES.as_posix()}') f
            JOIN points pt USING (object_id, gallery, picket, "at")
        )
        """
    ).fetchone()
    result = {
        "rows": int(stats[0]),
        "positive_rows": int(stats[1]),
        "negative_rows": int(stats[2]),
        "negative_keep_rate": NEGATIVE_KEEP_RATE,
        "units": int(stats[3]),
        "night_share_range": [float(stats[4]), float(stats[5])],
        "alarm_hour_share_720h_range": [float(stats[6]), float(stats[7])],
        "hours_since_last_alarm_min": int(stats[8]),
        "is_disarmed_share_label1": float(stats[9]),
        "is_disarmed_share_label0": float(stats[10]),
        "has_access_sequence_share_label1": float(stats[11]),
        "has_access_sequence_share_label0": float(stats[12]),
    }
    STATS.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"строк: {result['rows']}, положительных: {result['positive_rows']}, "
        f"отрицательных: {result['negative_rows']}, единиц: {result['units']}"
    )
    print(
        f"is_disarmed: {result['is_disarmed_share_label1']:.4f} на положительных, "
        f"{result['is_disarmed_share_label0']:.4f} на отрицательных"
    )
    print(
        f"has_access_sequence: {result['has_access_sequence_share_label1']:.4f} на положительных, "
        f"{result['has_access_sequence_share_label0']:.4f} на отрицательных"
    )
    print(f"замер записан в {STATS}")
