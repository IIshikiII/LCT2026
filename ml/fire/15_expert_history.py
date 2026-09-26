"""Разметка истории экспертными правилами пожара. ADR 0016.

Правила лежат в `backend/app/ml/fire_rules.py` и грузятся оттуда как есть:
история размечается тем же кодом, что работает в сервисе. Скрипт считает
факты эпизода по журналу, ставит уровень и меряет три вещи.

1. **Нагрузку.** Сколько эпизодов каждого уровня в год и в сутки на сеть.
2. **Повтор назавтра.** Доля эпизодов уровня, после которых на пикете назавтра
   пришёл сигнал вне пачки (метка ADR 0014). Это не точность обнаружения
   пожара: подтверждённых пожаров нет. Это измеримая величина, и сервис
   показывает её как вероятность сигнала пожарной сигнализации в 24 часа.
   Доля считается на годах до 2025-07-01, отложенный год идёт проверкой.
3. **Примеры.** По пять эпизодов каждого уровня для проверки глазами.

Факты. Соседний пикет лежит на той же галерее объекта не дальше 50 м. Обход
объекта это хотя бы одна пачка в сутки. Возраст канала считается от его
первой записи, объект новый, если его первый пожарный канал появился после
2019-02-01 и меньше 180 суток назад. Газовые датчики стоят на отдельных
объектах «ДУ» (ADR 0015), поэтому газ меряется отдельно по объекто-суткам.

Выход: `out/expert_history.json`, `out/expert_examples.csv`,
`out/expert_episodes.parquet`.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import duckdb
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parents[0] / "access"))

import calendar_ru

EVENTS = ROOT / "eda" / "out" / "events.parquet"
CHANNELS = ROOT / "notebooks" / "out" / "channel_features.parquet"
OBJECTS = ROOT / "raw_task" / "dataset" / "справочник_объектов_диспетчер.csv"
OUT = HERE / "out"
ALARMS = OUT / "fire_alarms.parquet"
PANEL = OUT / "panel_daily.parquet"
PPR = OUT / "ppr_2026.parquet"
RESULT = OUT / "expert_history.json"
EXAMPLES = OUT / "expert_examples.csv"
EPISODES = OUT / "expert_episodes.parquet"
CALIBRATION_END = "2025-07-01"
CENSOR = "2019-02-01"


def load_rules():
    path = ROOT / "backend" / "app" / "ml" / "fire_rules.py"
    spec = importlib.util.spec_from_file_location("fire_rules", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["fire_rules"] = module
    spec.loader.exec_module(module)
    return module


def facts_table(con: duckdb.DuckDBPyConnection, cal: str) -> pd.DataFrame:
    con.execute(f"CREATE TABLE a AS SELECT * FROM read_parquet('{ALARMS.as_posix()}')")
    con.execute(
        """
        CREATE TABLE s AS SELECT a.*,
            (SELECT count(DISTINCT (r.gallery, r.picket)) FROM a r
             WHERE r.object_id = a.object_id AND (r.gallery <> a.gallery OR r.picket <> a.picket)
               AND r.moment::DATE = a.moment::DATE
               AND r.moment BETWEEN a.moment - INTERVAL 60 MINUTE AND a.moment + INTERVAL 60 MINUTE
            ) AS nb,
            (SELECT count(*) FROM a r
             WHERE r.object_id = a.object_id AND r.gallery = a.gallery
               AND r.picket <> a.picket AND abs(r.picket - a.picket) <= 5
               AND r.moment BETWEEN a.moment - INTERVAL 30 MINUTE AND a.moment + INTERVAL 30 MINUTE
            ) AS near30,
            (SELECT count(*) FROM a r
             WHERE a.stype = 'Датчик дыма' AND r.stype <> 'Датчик дыма'
               AND r.object_id = a.object_id AND r.gallery = a.gallery
               AND abs(r.picket - a.picket) <= 5
               AND r.moment BETWEEN a.moment - INTERVAL 60 MINUTE AND a.moment + INTERVAL 60 MINUTE
            ) AS heat60
        FROM a
        """
    )
    con.execute("CREATE TABLE walk AS SELECT DISTINCT object_id, moment::DATE AS d FROM s WHERE nb > 2")
    con.execute(
        f"""
        CREATE TABLE chan AS
        SELECT f.cid AS channel_id, f.oid AS object_id, min(e.d) AS born
        FROM read_parquet('{EVENTS.as_posix()}') e
        JOIN read_parquet('{CHANNELS.as_posix()}') f ON f.cid = e.channel_id
        WHERE f.stype IN ('Датчик дыма', 'Датчик температуры', 'Тепловой датчик')
        GROUP BY ALL
        """
    )
    con.execute("CREATE TABLE obj AS SELECT object_id, min(born) AS born FROM chan GROUP BY 1")
    con.execute(
        f"""
        CREATE TABLE t AS
        SELECT ch.oid AS object_id, date_trunc('hour', e.d + e.t) AS h,
            max(try_cast(replace(e.value, ',', '.') AS DOUBLE)) AS v
        FROM read_parquet('{EVENTS.as_posix()}') e
        JOIN read_parquet('{CHANNELS.as_posix()}') ch ON ch.cid = e.channel_id
        WHERE ch.stype = 'Датчик температуры'
          AND try_cast(replace(e.value, ',', '.') AS DOUBLE) BETWEEN -40 AND 125
        GROUP BY ALL
        """
    )
    con.execute(
        """
        CREATE TABLE tn AS SELECT object_id, h, v - median(v) OVER (PARTITION BY object_id
            ORDER BY h RANGE BETWEEN INTERVAL 30 DAY PRECEDING AND INTERVAL 1 HOUR PRECEDING) AS rise
        FROM t
        """
    )
    con.execute(
        f"""
        CREATE TABLE cx AS SELECT "ид_объект" AS object_id, "родитель" AS complex_id
        FROM read_csv_auto('{OBJECTS.as_posix()}')
        """
    )
    con.execute(
        f"""
        CREATE TABLE ph AS
        SELECT cx.complex_id, date_trunc('hour', e.d + e.t) AS h
        FROM read_parquet('{EVENTS.as_posix()}') e
        JOIN read_parquet('{CHANNELS.as_posix()}') ch ON ch.cid = e.channel_id
        JOIN cx ON cx.object_id = ch.oid
        WHERE ch.stype = 'Состояние фазы' AND e.value = 'Обесточен'
        GROUP BY ALL
        """
    )
    return con.execute(
        f"""
        WITH ep AS (
            SELECT s.object_id, s.gallery, s.picket, s.moment::DATE AS d,
                min(s.moment) FILTER (WHERE nb <= 2) AS m,
                bool_or(nb <= 2) AS signal,
                bool_and(nb > 2) AS burst_only,
                bool_or(nb <= 2 AND s.stype = 'Датчик дыма') AS smoke,
                bool_or(nb <= 2 AND near30 > 0) AS spread,
                bool_or(nb <= 2 AND heat60 > 0) AS heat_independent,
                bool_or(nb <= 2 AND (k.is_day_off = 1 OR hour(s.moment) < 6
                                     OR hour(s.moment) >= 22)) AS off_hours,
                max(s.moment::DATE - c.born) FILTER (WHERE nb <= 2) AS sensor_age_max,
                min(s.moment::DATE - c.born) FILTER (WHERE nb <= 2) AS sensor_age_min
            FROM s JOIN {cal} k ON k.day = s.moment::DATE
            JOIN chan c ON c.channel_id = s.channel_id
            WHERE NOT (s.object_id = 5343 AND year(s.moment) IN (2020, 2021))
            GROUP BY ALL
        )
        SELECT ep.*, (w.d IS NOT NULL) AS walk_day,
            ep.d - o.born AS object_age, o.born >= DATE '{CENSOR}' AS object_known_birth,
            coalesce((SELECT max(tn.rise) FROM tn WHERE tn.object_id = ep.object_id
                AND tn.h BETWEEN date_trunc('hour', ep.m) - INTERVAL 2 HOUR
                             AND date_trunc('hour', ep.m) + INTERVAL 2 HOUR), 0) AS temp_rise_c,
            exists(SELECT 1 FROM ph JOIN cx c2 ON c2.complex_id = ph.complex_id
                   WHERE c2.object_id = ep.object_id
                     AND ph.h BETWEEN date_trunc('hour', ep.m) - INTERVAL 1 HOUR
                                  AND date_trunc('hour', ep.m) + INTERVAL 1 HOUR) AS power_off
        FROM ep
        LEFT JOIN walk w ON w.object_id = ep.object_id AND w.d = ep.d
        JOIN obj o ON o.object_id = ep.object_id
        """
    ).df()


def main() -> None:
    rules = load_rules()
    con = duckdb.connect()
    con.execute("PRAGMA threads=16")
    cal = calendar_ru.build_calendar(con)
    ep = facts_table(con, cal)

    levels, signs, tags = [], [], []
    for row in ep.itertuples():
        # Все сигналы пикета в сутках новые: датчик охлаждается, если даже
        # самый старый из тревоживших моложе порога.
        sensor_cooling = bool(row.signal) and (row.sensor_age_max or 0) < rules.SENSOR_BURN_IN_DAYS
        object_cooling = bool(row.object_known_birth) and row.object_age < rules.OBJECT_BURN_IN_DAYS
        left = 0
        if sensor_cooling:
            left = rules.SENSOR_BURN_IN_DAYS - int(row.sensor_age_max or 0)
        elif object_cooling:
            left = rules.OBJECT_BURN_IN_DAYS - int(row.object_age)
        facts = rules.Facts(
            signal=bool(row.signal),
            burst_only=bool(row.burst_only),
            smoke=bool(row.smoke),
            heat_independent=bool(row.heat_independent),
            spread=bool(row.spread),
            temp_rise_c=float(row.temp_rise_c if row.signal else 0.0),
            off_hours_no_walk=bool(row.off_hours) and not bool(row.walk_day),
            power_off=bool(row.power_off) and bool(row.signal),
            sensor_cooling=sensor_cooling,
            object_cooling=object_cooling,
            cooling_days_left=left,
        )
        result = rules.assess(facts)
        levels.append(result.level)
        signs.append(result.signs)
        tags.append(result.tag)
    ep["level"], ep["signs"], ep["tag"] = levels, signs, tags

    # Повтор назавтра: сигнал вне пачки на пикете в следующие сутки.
    panel = con.execute(
        f"""
        SELECT object_id, gallery, picket, (hour::DATE - 1) AS d, label AS next_label
        FROM read_parquet('{PANEL.as_posix()}')
        """
    ).df()
    ep["d"] = pd.to_datetime(ep["d"])
    panel["d"] = pd.to_datetime(panel["d"])
    ep = ep.merge(panel, on=["object_id", "gallery", "picket", "d"], how="left")
    ep["next_label"] = ep["next_label"].fillna(0)
    ep.to_parquet(EPISODES, index=False)

    calib = ep["d"] < pd.Timestamp(CALIBRATION_END)
    days_calib = (pd.Timestamp(CALIBRATION_END) - pd.Timestamp("2019-01-01")).days
    days_hold = (pd.Timestamp("2026-07-01") - pd.Timestamp(CALIBRATION_END)).days
    # Сутки пикета без эпизода: доля сигнала назавтра на панели.
    quiet = con.execute(
        f"""
        SELECT avg(label) FROM read_parquet('{PANEL.as_posix()}')
        WHERE hour < TIMESTAMP '{CALIBRATION_END}' AND evt_days_1d = 0 AND chk_days_30d >= 0
        """
    ).fetchone()[0]
    result = {"calibration_before": CALIBRATION_END, "quiet_next_day_rate": round(float(quiet), 6),
              "levels": {}}
    for level in rules.LEVELS:
        for name, mask, days in (("calibration", calib, days_calib), ("holdout", ~calib, days_hold)):
            m = mask & (ep["level"] == level) & (ep["signal"] | (level == "CRITICAL"))
            item = result["levels"].setdefault(level, {})
            item[name] = {
                "episodes": int(m.sum()),
                "per_year": round(float(m.sum()) / days * 365, 1),
                "per_day": round(float(m.sum()) / days, 3),
                "next_day_signal_rate": round(float(ep.loc[m, "next_label"].mean()), 4)
                if m.any() else None,
            }
    walks = ep["burst_only"]
    result["burst_only_per_year"] = round(float((walks & calib).sum()) / days_calib * 365, 1)

    # Газ: объекто-сутки с метаном от 5 % вне рабочих часов, окна ППР 2026 года
    # исключены.
    gas = con.execute(
        f"""
        WITH g AS (
            SELECT ch.oid AS object_id, e.d, max(try_cast(replace(e.value, ',', '.') AS DOUBLE)) AS v
            FROM read_parquet('{EVENTS.as_posix()}') e
            JOIN read_parquet('{CHANNELS.as_posix()}') ch ON ch.cid = e.channel_id
            JOIN {cal} k ON k.day = e.d
            WHERE ch.stype = 'Газовый датчик'
              AND try_cast(replace(e.value, ',', '.') AS DOUBLE) BETWEEN 5 AND 100
              AND (k.is_day_off = 1 OR hour(e.t) < 8 OR hour(e.t) >= 16)
            GROUP BY ALL
        )
        SELECT count(*) FROM g
        WHERE NOT EXISTS (SELECT 1 FROM read_parquet('{PPR.as_posix()}') p
                          WHERE p.object_id = g.object_id
                            AND g.d BETWEEN p.dismantle_from AND coalesce(p.back_from_check, p.accepted) + 14)
        """
    ).fetchone()[0]
    result["gas_ignition_object_days_total"] = int(gas)

    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    examples = ep[ep["signal"]].sample(frac=1, random_state=0).groupby("level").head(5)
    examples = examples.sort_values(["level", "m"])
    examples[["level", "object_id", "gallery", "picket", "m", "smoke", "spread", "heat_independent",
              "temp_rise_c", "off_hours", "walk_day", "power_off", "sensor_age_max", "object_age",
              "next_label"]].to_csv(EXAMPLES, index=False, encoding="utf-8")

    print(f"эпизодов {len(ep)}, пачек в год {result['burst_only_per_year']}, "
          f"газ от 5 % вне окон: {gas} объекто-суток за всё время")
    print(f"сутки без эпизода: сигнал назавтра {result['quiet_next_day_rate']:.4%}")
    for level, item in result["levels"].items():
        c, h = item["calibration"], item["holdout"]
        print(f"{level:8s} 2019–2025: {c['per_year']:6.1f} в год, повтор {c['next_day_signal_rate']}; "
              f"отложенный: {h['per_year']:6.1f} в год, повтор {h['next_day_signal_rate']}")


if __name__ == "__main__":
    main()
