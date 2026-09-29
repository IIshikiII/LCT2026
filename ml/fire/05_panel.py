"""Панель признаков направления «пожарный риск». ADR 0014.

**Сетка.** Строка это «пикет и сутки». Точка расчёта стоит в полночь суток `D`
по Москве. Все признаки считаются по суткам до `D`. Колонка времени зовётся
`hour` и держит полночь: так панель читает протокол `ml/flood/10_train.py`.

**Метка** (ADR 0014, пересмотр). В сутки `D` на пикете был сигнал метки вне
пачки. Сигнал метки это «Обнаружен дым» датчика дыма, «Температура выше 40ºC»
датчика температуры, «Не замкнут» теплового датчика. Пачка это сигнал, рядом с
которым в пределах часа и тех же суток тревожили больше двух других пикетов
объекта: обход с проверкой или общий сбой линии. Час суток метку не решает.

**Наивная планка.** Событие было в сутки `D − 1`.

**Признаки** идут группами:

1. `evt_*`, `unit_*`: события пикета, его датчики и возраст.
2. `chk_*`: сигналы пикета в пачке, то есть обходы и сбои.
3. `obj_*`: события и обходы у остальных пикетов объекта, фаза расписания
   обходов объекта, температура объекта.
3а. `line_*`: события на пикетах той же галереи в пределах 50 метров.
4. `tmp_*`: числа датчиков температуры пикета.
5. `gas_*`: числа газовых датчиков комплекса, процент метана.
6. `hw_*`: «Неисправен» и «Отключено устройство» у датчиков пикета.
7. `pwr_*`: обесточенная фаза и обесточенный вентилятор в комплексе.
8. `ppl_*`: люди в комплексе: домофон УИР-Р и открытые двери и люки.

Группы 5, 7 и 8 считаются по комплексу, а не по объекту. Газовые датчики
стоят на отдельных объектах «ДУ», и ни один из них не делит объект с датчиком
дыма. Общий у них только комплекс.
9. `wx_*`: погода Москвы: температура, осадки, влажность, точка росы,
   давление, ветер, почва, PM2.5 и угарный газ (`07_weather.py`,
   `09_external.py`).
9а. `cond_*`: конденсат. Точка росы снаружи минус температура коллектора:
   тёплый влажный воздух в холодном коллекторе даёт туман, и датчик дыма видит
   его как дым.
9б. `rec_*`: вчерашнее событие изнутри суток: минуты от последнего сигнала до
   полуночи, тип датчика, число моментов и каналов. Сигнал в 23:40 повторяется
   назавтра впятеро чаще сигнала в 3:00.
   `rec_repeats_*`: сколько раз событие пикета продолжалось назавтра в прошлом.
   Датчик, который тревожит сериями, отличается от датчика с одиночными
   событиями.
9в. `geo_*`: положение пикета на трассе объекта и расстояние до края трассы.
9г. `week_*`: день недели и нерабочий день суток расчёта. Метка больше не
   режет рабочие часы, поэтому ритм недели здесь законный признак.
10. `cal_*`: сезон, месяц, день года, салют в сутки расчёта.

Окна считаются оконными функциями по плотной сетке суток, поэтому `ROWS
BETWEEN n PRECEDING AND 1 PRECEDING` совпадает с календарным окном.

Запуск из корня репозитория:

    .venv\\Scripts\\python.exe ml/fire/05_panel.py
"""

from __future__ import annotations

import json
import pathlib
import sys
import time

import duckdb

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "common"))

import calendar_ru

ROOT = HERE.parents[1]
OBJECTS = ROOT / "raw_task" / "dataset" / "справочник_объектов_диспетчер.csv"
EVENTS = ROOT / "eda" / "out" / "events.parquet"
CHANNELS = ROOT / "eda" / "out" / "channels.parquet"
FLOOD_OUT = HERE.parents[0] / "flood" / "out"
OUT = HERE / "out"
ALARMS = OUT / "fire_alarms.parquet"
UNITS = OUT / "fire_units.parquet"
WEATHER = FLOOD_OUT / "weather_daily.parquet"
HUMIDITY = OUT / "humidity_daily.parquet"
EXTERNAL = OUT / "external_daily.parquet"
PANEL = OUT / "panel_daily.parquet"
STATS = OUT / "panel_stats.json"

KEY = "object_id, gallery, picket"
FIRE_TYPES = ("Датчик дыма", "Датчик температуры", "Тепловой датчик")
ARTIFACT = "NOT (ch.oid = 5343 AND year(e.d) IN (2020, 2021))"
# Число вне этих границ у датчика температуры это код ошибки, а не °C.
TEMP_RANGE = (-40.0, 125.0)
DAYS_CAP = 3650
# Пачка: больше BURST других пикетов объекта в пределах BURST_MINUTES.
# Порог выбран по излому доли сохранённых ночных сигналов (ADR 0014).
BURST = 2
BURST_MINUTES = 60
# Соседи по трассе: пикеты той же галереи не дальше LINE_PICKETS, то есть 50 м.
LINE_PICKETS = 5


def _roll(column: str, days: int, name: str, fn: str = "sum") -> str:
    """Свёртка по окну прошлых суток, сутки расчёта не входят."""
    return (
        f"{fn}({column}) OVER (PARTITION BY {KEY} ORDER BY day "
        f"ROWS BETWEEN {days} PRECEDING AND 1 PRECEDING) AS {name}"
    )


def _since(flag: str, name: str) -> str:
    """Сутки с последних суток, где флаг поднят, до суток расчёта."""
    return (
        f"least(coalesce(day - max(CASE WHEN {flag} > 0 THEN day END) OVER "
        f"(PARTITION BY {KEY} ORDER BY day ROWS BETWEEN UNBOUNDED PRECEDING "
        f"AND 1 PRECEDING), {DAYS_CAP}), {DAYS_CAP}) AS {name}"
    )


def build_panel(con: duckdb.DuckDBPyConnection, calendar: str) -> None:
    """Строит таблицу `panel` из входных таблиц.

    Входы: `unit`, `alarm`, `fault`, `temp`, `objtemp`, `cxday`,
    `object_complex`, `weather`, `humidity`, `external`, `calendar`.
    Формы описаны в `main` и в `test_fire_panel.py`.
    """
    con.execute(
        f"""
        CREATE OR REPLACE TABLE alarm_nb AS
        SELECT a.object_id, a.gallery, a.picket, a.moment, a.channel_id, a.stype,
            (SELECT count(DISTINCT (r.gallery, r.picket)) FROM alarm r
             WHERE r.object_id = a.object_id
               AND (r.gallery <> a.gallery OR r.picket <> a.picket)
               AND r.moment::DATE = a.moment::DATE
               AND r.moment BETWEEN a.moment - INTERVAL {BURST_MINUTES} MINUTE
                                AND a.moment + INTERVAL {BURST_MINUTES} MINUTE) AS nb
        FROM alarm a
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE alarm_day AS
        SELECT {KEY}, moment::DATE AS day,
            count(*) FILTER (WHERE nb <= {BURST}) AS m_ev,
            count(*) FILTER (WHERE nb > {BURST}) AS m_chk,
            date_diff('minute', max(moment) FILTER (WHERE nb <= {BURST}),
                      any_value(moment::DATE) + INTERVAL 1 DAY) AS ev_to_midnight,
            count(DISTINCT channel_id) FILTER (WHERE nb <= {BURST}) AS ev_channels,
            max((stype = 'Датчик дыма')::INT) FILTER (WHERE nb <= {BURST}) AS ev_smoke,
            max((stype = 'Тепловой датчик')::INT) FILTER (WHERE nb <= {BURST}) AS ev_heat,
            max((stype = 'Датчик температуры')::INT) FILTER (WHERE nb <= {BURST}) AS ev_temp
        FROM alarm_nb GROUP BY ALL
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE unit_day AS
        WITH grid AS (
            SELECT u.object_id, u.gallery, u.picket, g.day::DATE AS day
            FROM unit u,
                 LATERAL (SELECT unnest(generate_series(u.hour_from::DATE,
                     u.hour_to::DATE, INTERVAL 1 DAY)) AS day) g
        ),
        alive AS (
            SELECT object_id, day, count(*) AS o_units FROM grid GROUP BY ALL
        ),
        ud AS (
            SELECT g.*, coalesce(a.m_ev, 0) AS m_ev,
                CAST(coalesce(a.m_ev, 0) > 0 AS INTEGER) AS ev,
                CAST(coalesce(a.m_chk, 0) > 0 AS INTEGER) AS chk,
                a.ev_to_midnight, a.ev_channels, a.ev_smoke, a.ev_heat, a.ev_temp,
                coalesce(f.n, 0) AS faults,
                t.t_max, t.t_mean
            FROM grid g
            LEFT JOIN alarm_day a USING ({KEY}, day)
            LEFT JOIN fault f USING ({KEY}, day)
            LEFT JOIN temp t USING ({KEY}, day)
        ),
        od AS (
            SELECT object_id, day, sum(ev) AS o_ev, sum(chk) AS o_chk
            FROM ud GROUP BY ALL
        ),
        line AS (
            SELECT a.object_id, a.gallery, a.picket, a.day, sum(b.ev) AS l_ev
            FROM ud a JOIN ud b ON b.object_id = a.object_id AND b.gallery = a.gallery
                AND b.day = a.day AND b.picket <> a.picket
                AND abs(b.picket - a.picket) <= {LINE_PICKETS} AND b.ev = 1
            GROUP BY ALL
        )
        SELECT ud.*,
            ud.ev * coalesce(lag(ud.ev) OVER (PARTITION BY ud.object_id, ud.gallery,
                ud.picket ORDER BY ud.day), 0) AS rep,
            od.o_ev - ud.ev AS n_ev, od.o_chk - ud.chk AS n_chk,
            CAST(od.o_chk > 0 AS INTEGER) AS o_walk, coalesce(line.l_ev, 0) AS l_ev,
            (od.o_chk - ud.chk) / greatest(al.o_units - 1, 1) AS n_chk_share,
            x.gas_max, x.gas_mean,
            coalesce(x.gas_mid, 0) AS gas_mid, coalesce(x.gas_ge1, 0) AS gas_ge1,
            coalesce(x.gas_ge1_off, 0) AS gas_ge1_off,
            coalesce(x.phase_off, 0) AS phase_off, coalesce(x.fan_off, 0) AS fan_off,
            coalesce(x.talk, 0) AS talk, coalesce(x.door, 0) AS door,
            ot.obj_t_max
        FROM ud
        JOIN od USING (object_id, day)
        JOIN alive al USING (object_id, day)
        LEFT JOIN line USING ({KEY}, day)
        LEFT JOIN objtemp ot USING (object_id, day)
        LEFT JOIN object_complex oc USING (object_id)
        LEFT JOIN cxday x ON x.complex_id = oc.complex_id AND x.day = ud.day
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE wx AS
        SELECT day,
            lag(temp_mean) OVER w AS wx_temp_mean_1d,
            lag(temp_max) OVER w AS wx_temp_max_1d,
            max(temp_max) OVER (ORDER BY day ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING)
                AS wx_temp_max_7d,
            lag(precip_mm) OVER w AS wx_precip_1d,
            lag(h.rh_mean) OVER w AS wx_rh_mean_1d,
            lag(h.rh_max) OVER w AS wx_rh_max_1d,
            avg(h.rh_mean) OVER (ORDER BY day ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING)
                AS wx_rh_mean_7d,
            lag(h.dew_mean) OVER w AS wx_dew_mean_1d,
            lag(h.pressure_change) OVER w AS wx_pressure_change_1d,
            lag(x.wind_mean) OVER w AS wx_wind_mean_1d,
            lag(x.gust_max) OVER w AS wx_gust_max_1d,
            lag(x.cloud_mean) OVER w AS wx_cloud_mean_1d,
            lag(x.soil_temp) OVER w AS wx_soil_temp_1d,
            lag(x.pm25_mean) OVER w AS wx_pm25_mean_1d,
            max(x.pm25_max) OVER (ORDER BY day ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING)
                AS wx_pm25_max_7d,
            lag(x.pm10_mean) OVER w AS wx_pm10_mean_1d,
            lag(x.co_max) OVER w AS wx_co_max_1d,
            x.fireworks AS cal_fireworks,
            lag(x.fireworks) OVER w AS cal_fireworks_1d
        FROM weather LEFT JOIN humidity h USING (day) LEFT JOIN external x USING (day)
        WINDOW w AS (ORDER BY day)
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE panel AS
        WITH r AS (
            SELECT {KEY}, day, ev,
                {_roll('ev', 1, 'evt_days_1d')},
                {_roll('ev', 7, 'evt_days_7d')},
                {_roll('ev', 30, 'evt_days_30d')},
                {_roll('ev', 90, 'evt_days_90d')},
                {_roll('ev', 365, 'evt_days_365d')},
                {_roll('m_ev', 30, 'evt_moments_30d')},
                {_since('ev', 'evt_days_since')},
                {_roll('rep', 365, 'rec_repeats_365d')},
                sum(rep) OVER (PARTITION BY {KEY} ORDER BY day ROWS BETWEEN UNBOUNDED
                    PRECEDING AND 1 PRECEDING) AS rec_repeats_all,
                {_roll('ev_to_midnight', 1, 'rec_to_midnight_1d', 'min')},
                {_roll('ev_channels', 1, 'rec_channels_1d', 'max')},
                {_roll('m_ev', 1, 'rec_moments_1d')},
                {_roll('ev_smoke', 1, 'rec_smoke_1d', 'max')},
                {_roll('ev_heat', 1, 'rec_heat_1d', 'max')},
                {_roll('ev_temp', 1, 'rec_temp_1d', 'max')},
                sum(ev) OVER (PARTITION BY {KEY} ORDER BY day ROWS BETWEEN UNBOUNDED
                    PRECEDING AND 1 PRECEDING) AS evt_days_all,
                {_roll('l_ev', 1, 'line_events_1d')},
                {_roll('l_ev', 7, 'line_events_7d')},
                {_roll('l_ev', 30, 'line_events_30d')},
                {_since('o_walk', 'obj_walk_days_since')},
                {_roll('o_walk', 365, 'obj_walks_365d')},
                {_roll('chk', 30, 'chk_days_30d')},
                {_roll('chk', 365, 'chk_days_365d')},
                {_since('chk', 'chk_days_since')},
                {_roll('n_ev', 1, 'obj_events_1d')},
                {_roll('n_ev', 7, 'obj_events_7d')},
                {_roll('n_ev', 30, 'obj_events_30d')},
                {_roll('n_chk_share', 1, 'obj_check_share_1d')},
                {_roll('n_chk', 30, 'obj_checks_30d')},
                {_roll('obj_t_max', 1, 'obj_temp_max_1d', 'max')},
                {_roll('obj_t_max', 30, 'obj_temp_max_30d', 'avg')},
                {_roll('t_max', 1, 'tmp_max_1d', 'max')},
                {_roll('t_mean', 1, 'tmp_mean_1d', 'avg')},
                {_roll('t_mean', 7, 'tmp_mean_7d', 'avg')},
                {_roll('t_mean', 30, 'tmp_mean_30d', 'avg')},
                {_roll('t_max', 30, 'tmp_max_30d', 'max')},
                {_roll('gas_max', 1, 'gas_max_1d', 'max')},
                {_roll('gas_max', 7, 'gas_max_7d', 'max')},
                {_roll('gas_mean', 30, 'gas_mean_30d', 'avg')},
                {_roll('gas_mid', 7, 'gas_mid_7d')},
                {_roll('gas_ge1', 30, 'gas_ge1_30d')},
                {_roll('gas_ge1_off', 365, 'gas_ge1_off_365d')},
                {_since('gas_ge1', 'gas_days_since_ge1')},
                {_roll('faults', 1, 'hw_faults_1d')},
                {_roll('faults', 7, 'hw_faults_7d')},
                {_roll('faults', 30, 'hw_faults_30d')},
                {_roll('phase_off', 1, 'pwr_phase_off_1d')},
                {_roll('phase_off', 7, 'pwr_phase_off_7d')},
                {_roll('fan_off', 1, 'pwr_fan_off_1d')},
                {_roll('fan_off', 7, 'pwr_fan_off_7d')},
                {_roll('talk', 1, 'ppl_talk_1d')},
                {_roll('talk', 7, 'ppl_talk_7d')},
                {_roll('door', 1, 'ppl_door_1d')},
                {_roll('door', 7, 'ppl_door_7d')}
            FROM unit_day
        )
        SELECT r.object_id, r.gallery, r.picket, r.day::TIMESTAMP AS hour,
            r.ev AS label, CAST(coalesce(r.evt_days_1d, 0) > 0 AS INTEGER) AS naive,
            coalesce(r.evt_days_1d, 0) AS evt_days_1d,
            coalesce(r.evt_days_7d, 0) AS evt_days_7d,
            coalesce(r.evt_days_30d, 0) AS evt_days_30d,
            coalesce(r.evt_days_90d, 0) AS evt_days_90d,
            coalesce(r.evt_days_365d, 0) AS evt_days_365d,
            coalesce(r.evt_moments_30d, 0) AS evt_moments_30d,
            r.evt_days_since,
            coalesce(r.evt_days_all, 0) AS evt_days_all,
            coalesce(r.line_events_1d, 0) AS line_events_1d,
            coalesce(r.line_events_7d, 0) AS line_events_7d,
            coalesce(r.line_events_30d, 0) AS line_events_30d,
            r.obj_walk_days_since,
            coalesce(r.obj_walks_365d, 0) AS obj_walks_365d,
            r.obj_walk_days_since * coalesce(r.obj_walks_365d, 0) / 365.0 AS obj_walk_phase,
            u.n_smoke AS unit_smoke, u.n_temp AS unit_temp, u.n_heat AS unit_heat,
            r.day - u.hour_from::DATE AS unit_age_days,
            coalesce(r.chk_days_30d, 0) AS chk_days_30d,
            coalesce(r.chk_days_365d, 0) AS chk_days_365d,
            r.chk_days_since,
            coalesce(r.obj_events_1d, 0) AS obj_events_1d,
            coalesce(r.obj_events_7d, 0) AS obj_events_7d,
            coalesce(r.obj_events_30d, 0) AS obj_events_30d,
            coalesce(r.obj_check_share_1d, 0) AS obj_check_share_1d,
            coalesce(r.obj_checks_30d, 0) AS obj_checks_30d,
            r.obj_temp_max_1d,
            r.obj_temp_max_1d - r.obj_temp_max_30d AS obj_temp_rise,
            r.tmp_max_1d, r.tmp_mean_1d, r.tmp_mean_7d,
            r.tmp_mean_1d - r.tmp_mean_30d AS tmp_delta_30d,
            r.tmp_max_1d - r.tmp_max_30d AS tmp_max_rise,
            r.gas_max_1d, r.gas_max_7d, r.gas_mean_30d,
            coalesce(r.gas_mid_7d, 0) AS gas_mid_7d,
            coalesce(r.gas_ge1_30d, 0) AS gas_ge1_30d,
            coalesce(r.gas_ge1_off_365d, 0) AS gas_ge1_off_365d,
            r.gas_days_since_ge1,
            coalesce(r.hw_faults_1d, 0) AS hw_faults_1d,
            coalesce(r.hw_faults_7d, 0) AS hw_faults_7d,
            coalesce(r.hw_faults_30d, 0) AS hw_faults_30d,
            coalesce(r.pwr_phase_off_1d, 0) AS pwr_phase_off_1d,
            coalesce(r.pwr_phase_off_7d, 0) AS pwr_phase_off_7d,
            coalesce(r.pwr_fan_off_1d, 0) AS pwr_fan_off_1d,
            coalesce(r.pwr_fan_off_7d, 0) AS pwr_fan_off_7d,
            coalesce(r.ppl_talk_1d, 0) AS ppl_talk_1d,
            coalesce(r.ppl_talk_7d, 0) AS ppl_talk_7d,
            coalesce(r.ppl_door_1d, 0) AS ppl_door_1d,
            coalesce(r.ppl_door_7d, 0) AS ppl_door_7d,
            w.wx_temp_mean_1d, w.wx_temp_max_1d, w.wx_temp_max_7d, w.wx_precip_1d,
            w.wx_rh_mean_1d, w.wx_rh_max_1d, w.wx_rh_mean_7d, w.wx_dew_mean_1d,
            w.wx_pressure_change_1d, w.wx_wind_mean_1d, w.wx_gust_max_1d,
            w.wx_cloud_mean_1d, w.wx_soil_temp_1d, w.wx_pm25_mean_1d, w.wx_pm25_max_7d,
            w.wx_pm10_mean_1d, w.wx_co_max_1d, w.cal_fireworks, w.cal_fireworks_1d,
            w.wx_dew_mean_1d - r.tmp_mean_1d AS cond_dew_minus_unit,
            w.wx_dew_mean_1d - r.obj_temp_max_1d AS cond_dew_minus_object,
            w.wx_temp_mean_1d - r.obj_temp_max_1d AS cond_outside_minus_object,
            coalesce(r.rec_to_midnight_1d, 1440) AS rec_to_midnight_1d,
            coalesce(r.rec_channels_1d, 0) AS rec_channels_1d,
            coalesce(r.rec_repeats_365d, 0) AS rec_repeats_365d,
            coalesce(r.rec_repeats_all, 0) AS rec_repeats_all,
            coalesce(r.rec_moments_1d, 0) AS rec_moments_1d,
            coalesce(r.rec_smoke_1d, 0) AS rec_smoke_1d,
            coalesce(r.rec_heat_1d, 0) AS rec_heat_1d,
            coalesce(r.rec_temp_1d, 0) AS rec_temp_1d,
            (r.picket - g.pk_min) / greatest(g.pk_max - g.pk_min, 1) AS geo_position,
            least(r.picket - g.pk_min, g.pk_max - r.picket) AS geo_to_edge,
            g.units AS geo_object_units,
            k.day_of_week AS week_day_of_week, k.is_day_off AS week_is_day_off,
            k.season AS cal_season, k.month AS cal_month,
            dayofyear(r.day) AS cal_day_of_year
        FROM r
        JOIN unit u USING ({KEY})
        JOIN (SELECT object_id, gallery, min(picket) AS pk_min, max(picket) AS pk_max,
                  count(*) AS units FROM unit GROUP BY ALL) g USING (object_id, gallery)
        LEFT JOIN wx w ON w.day = r.day
        JOIN {calendar} k ON k.day = r.day
        WHERE r.day > u.hour_from::DATE
        """
    )


def load_inputs(con: duckdb.DuckDBPyConnection, calendar: str) -> None:
    """Кладёт входные таблицы `build_panel` из выгрузки."""
    ev, ch = EVENTS.as_posix(), CHANNELS.as_posix()
    con.execute(f"CREATE TABLE unit AS SELECT * FROM read_parquet('{UNITS.as_posix()}')")
    con.execute(
        f"""
        CREATE TABLE alarm AS
        SELECT object_id, gallery, picket, moment, channel_id, stype
        FROM read_parquet('{ALARMS.as_posix()}')
        """
    )
    con.execute(
        f"""
        CREATE TABLE raw AS
        SELECT ch.stype, ch.oid AS object_id, ch.gal_key AS gallery, ch.picket,
            e.d AS day, e.t, e.alarm, e.value
        FROM read_parquet('{ev}') e
        JOIN read_parquet('{ch}') ch ON ch.cid = e.channel_id
        WHERE ch.stype IN ('Датчик дыма', 'Датчик температуры', 'Тепловой датчик',
                'Состояние фазы', 'Состояние вентилятора', 'Состояние УИР-Р',
                'КД Дверь', 'КД Люк')
          AND {ARTIFACT}
          AND (e.alarm OR ch.stype IN ('Датчик температуры', 'Состояние фазы',
                'Состояние УИР-Р'))
        """
    )
    con.execute(
        f"""
        CREATE TABLE fault AS
        SELECT object_id, gallery, picket, day, count(*) AS n FROM raw
        WHERE alarm AND value IN ('Неисправен', 'Отключено устройство')
          AND stype IN {FIRE_TYPES} AND picket IS NOT NULL
        GROUP BY ALL
        """
    )
    con.execute(
        """
        CREATE TABLE temp_raw AS
        SELECT object_id, gallery, picket, day,
            try_cast(replace(value, ',', '.') AS DOUBLE) AS v
        FROM raw WHERE stype = 'Датчик температуры'
        """
    )
    con.execute(
        f"""
        CREATE TABLE temp AS
        SELECT object_id, gallery, picket, day, max(v) AS t_max, avg(v) AS t_mean
        FROM temp_raw WHERE picket IS NOT NULL AND v BETWEEN {TEMP_RANGE[0]} AND {TEMP_RANGE[1]}
        GROUP BY ALL
        """
    )
    con.execute(
        f"""
        CREATE TABLE gas AS
        SELECT ch.oid AS object_id, e.d AS day,
            max(v) AS gas_max, avg(v) AS gas_mean,
            count(*) FILTER (WHERE v >= 0.1 AND v < 1) AS gas_mid,
            count(*) FILTER (WHERE v >= 1) AS gas_ge1,
            count(*) FILTER (WHERE v >= 1 AND (k.is_day_off = 1 OR hour(e.t) < 8
                OR hour(e.t) >= 16)) AS gas_ge1_off
        FROM (
            SELECT e.*, try_cast(replace(e.value, ',', '.') AS DOUBLE) AS v
            FROM read_parquet('{ev}') e
            WHERE e.channel_id IN (SELECT cid FROM read_parquet('{ch}')
                                   WHERE stype = 'Газовый датчик')
        ) e
        JOIN read_parquet('{ch}') ch ON ch.cid = e.channel_id
        JOIN {calendar} k ON k.day = e.d
        WHERE e.v BETWEEN 0 AND 100 AND {ARTIFACT}
        GROUP BY ALL
        """
    )
    con.execute(
        f"""
        CREATE TABLE object_complex AS
        SELECT "ид_объект" AS object_id, "родитель" AS complex_id
        FROM read_csv_auto('{OBJECTS.as_posix()}')
        """
    )
    con.execute(
        f"""
        CREATE TABLE objtemp AS
        SELECT object_id, day, max(v) AS obj_t_max FROM temp_raw
        WHERE v BETWEEN {TEMP_RANGE[0]} AND {TEMP_RANGE[1]} GROUP BY ALL
        """
    )
    con.execute(
        """
        CREATE TABLE cxday AS
        WITH o AS (
            SELECT oc.complex_id, r.day,
                count(*) FILTER (WHERE stype = 'Состояние фазы' AND value = 'Обесточен')
                    AS phase_off,
                count(*) FILTER (WHERE stype = 'Состояние вентилятора' AND alarm
                    AND value = 'Обесточен') AS fan_off,
                count(*) FILTER (WHERE stype = 'Состояние УИР-Р'
                    AND value IN ('Разговор', 'Вызов')) AS talk,
                count(*) FILTER (WHERE stype IN ('КД Дверь', 'КД Люк') AND alarm
                    AND value = 'Не замкнут') AS door
            FROM raw r JOIN object_complex oc USING (object_id) GROUP BY ALL
        ),
        g AS (
            SELECT oc.complex_id, g.day, max(gas_max) AS gas_max, avg(gas_mean) AS gas_mean,
                sum(gas_mid) AS gas_mid, sum(gas_ge1) AS gas_ge1,
                sum(gas_ge1_off) AS gas_ge1_off
            FROM gas g JOIN object_complex oc USING (object_id) GROUP BY ALL
        )
        SELECT * FROM o FULL JOIN g USING (complex_id, day)
        """
    )
    con.execute(f"CREATE TABLE weather AS SELECT * FROM read_parquet('{WEATHER.as_posix()}')")
    con.execute(f"CREATE TABLE humidity AS SELECT * FROM read_parquet('{HUMIDITY.as_posix()}')")
    con.execute(f"CREATE TABLE external AS SELECT * FROM read_parquet('{EXTERNAL.as_posix()}')")


def main() -> None:
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    con.execute("PRAGMA memory_limit='8GB'")
    calendar = calendar_ru.build_calendar(con)
    t = time.time()
    load_inputs(con, calendar)
    print(f"входы собраны за {time.time() - t:.0f} c")
    t = time.time()
    build_panel(con, calendar)
    con.execute(f"COPY panel TO '{PANEL.as_posix()}' (FORMAT PARQUET)")
    print(f"панель построена за {time.time() - t:.0f} c")
    rows, units, pos, naive = con.execute(
        "SELECT count(*), count(DISTINCT (object_id, gallery, picket)), sum(label), "
        "sum(naive) FROM panel"
    ).fetchone()
    by_year = con.execute(
        "SELECT year(hour), count(*), sum(label), round(avg(label), 6) FROM panel "
        "GROUP BY 1 ORDER BY 1"
    ).fetchall()
    nulls = {
        name: con.execute(f"SELECT round(avg(({name} IS NULL)::INT), 3) FROM panel").fetchone()[0]
        for name in ("tmp_max_1d", "gas_max_1d", "obj_temp_max_1d", "pwr_fan_off_1d",
                     "wx_temp_mean_1d")
    }
    stats = {"rows": rows, "units": units, "positives": int(pos), "naive_alerts": int(naive),
             "by_year": [list(r) for r in by_year], "null_share": nulls}
    STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=2, default=str),
                     encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
