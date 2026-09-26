"""Метка подтопления по признакам события, а не по часу. ADR 0012.

Событие это сутки пикета с сигналом «Затоплен» насоса или «Не замкнут»
датчика затопления. Часть событий оставляет вода, часть плановые проверки.

- Размеченные положительные примеры: событие с сигналом ночью (22:00–6:00) или
  в нерабочий день. Проверок в это время почти нет.
- Неразмеченные: остальные события, вода вперемешку с проверками.
- Классификатор отличает первых от вторых по признакам события без признаков
  времени. Поправка Elkan и Noto переводит его выход в вероятность воды.

Классификатор учится с перекрёстной подгонкой по годам: событие года метит
модель, которая этот год не видела. События отложенного года (с 2025-07-01)
метит модель, обученная на всех событиях до него.

Выход: `out/pu_events.parquet`, `out/pu_label.json`. Каждый запуск с именем
попытки пишется в `out/pu_attempts.json`. Бюджет три попытки (ADR 0012).

Запуск: `.venv\\Scripts\\python.exe ml/flood/11_pu_label.py <попытка> <набор>`,
где набор это `daily` или `hourly`.
"""

import json
import pathlib
import sys

import duckdb
import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RAW_EVENTS = ROOT / "eda" / "out" / "events.parquet"
CHANNELS = ROOT / "notebooks" / "out" / "channel_features.parquet"
sys.path.insert(0, str(HERE.parents[0] / "access"))

import calendar_ru

OUT = HERE / "out"
HOURLY = OUT / "flood_hourly.parquet"
UNITS = OUT / "flood_units.parquet"
PUMPS = OUT / "pump_hourly.parquet"
WEATHER = OUT / "weather_daily.parquet"
EVENTS = OUT / "pu_events.parquet"
RESULT = OUT / "pu_label.json"
ATTEMPTS = OUT / "pu_attempts.json"
BUDGET = 3

TEST_START = "2025-07-01"
LAST_TRAIN_YEAR = ("2024-07-01", "2025-07-01")
# Граница «вода или проверка»: квантиль оценки у известной воды до последнего
# обучающего года. Сохраняет 80 % известной воды (ADR 0012, пересмотр).
KEEP_KNOWN = 0.8
FEATURES = [
    "n_hours",
    "n_moments",
    "has_sensor",
    "has_pump",
    "pump_on_ratio",
    "pump_changes_ratio",
    "pump_all_running",
    "pump_unavailable",
    "neighbour_units",
    "signal_yesterday",
    "precip_3d_mm",
    "melt_3d_cm",
]
HOURLY_FEATURES = [
    "pump_on_before_2h_ratio",
    "pump_on_after_2h_ratio",
    "span_minutes",
    "n_episodes",
    "other_units_within_1h",
]
ATTEMPT = sys.argv[1] if len(sys.argv) > 1 else "diagnostic"
FEATURE_SET = sys.argv[2] if len(sys.argv) > 2 else "daily"
if FEATURE_SET == "hourly":
    FEATURES = FEATURES + HOURLY_FEATURES
log = json.loads(ATTEMPTS.read_text(encoding="utf-8")) if ATTEMPTS.exists() else []
if ATTEMPT not in ("diagnostic", "export"):
    if any(item["attempt"] == ATTEMPT for item in log):
        raise SystemExit(f"попытка {ATTEMPT} уже записана")
    if len(log) >= BUDGET:
        raise SystemExit(f"бюджет попыток исчерпан: {len(log)} из {BUDGET}")
PARAMS = {
    "objective": "binary",
    "learning_rate": 0.05,
    "num_leaves": 15,
    "min_data_in_leaf": 30,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "verbose": -1,
    "seed": 4217,
    "deterministic": True,
    "num_threads": 8,
}
ROUNDS = 300

con = duckdb.connect()
con.execute("PRAGMA threads=8")
cal = calendar_ru.build_calendar(con)

con.execute(
    f"""
    CREATE TABLE sig AS
    SELECT h.object_id, h.gallery, h.picket, h.hour, h.hour::DATE AS day,
           max((h.value = 'Не замкнут')::INT) AS from_sensor,
           max((h.value = 'Затоплен')::INT) AS from_pump,
           sum(h.n_moments) AS moments,
           hour(h.hour) >= 22 OR hour(h.hour) < 6 OR k.is_day_off = 1 AS off_hours
    FROM read_parquet('{HOURLY.as_posix()}') h
    JOIN {cal} k ON k.day = h.hour::DATE
    WHERE h.value IN ('Затоплен', 'Не замкнут')
    GROUP BY h.object_id, h.gallery, h.picket, h.hour, k.is_day_off
    """
)
# Работа насоса пикета по суткам и её обычный уровень по обучающим годам.
con.execute(
    f"""
    CREATE TABLE pump_day AS
    SELECT object_id, gallery, picket, hour::DATE AS day,
           sum(on_minutes) AS on_min, sum(n_changes) AS changes,
           sum(n_all_pumps) AS allp, sum(n_unavail) AS unavail
    FROM read_parquet('{PUMPS.as_posix()}') WHERE picket IS NOT NULL GROUP BY ALL
    """
)
con.execute(
    f"""
    CREATE TABLE pump_norm AS
    SELECT object_id, gallery, picket,
           avg(on_min) AS mean_on, avg(changes) AS mean_changes
    FROM pump_day WHERE day < DATE '{TEST_START}' GROUP BY ALL
    """
)
con.execute(
    f"""
    CREATE TABLE wx AS
    SELECT day,
           sum(precip_mm) OVER w AS precip_3d_mm,
           sum(melt_cm) OVER w AS melt_3d_cm
    FROM read_parquet('{WEATHER.as_posix()}')
    WINDOW w AS (ORDER BY day ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)
    """
)
# Сигналы с точностью до секунды: длительность, эпизоды, обход техника.
con.execute(
    f"""
    CREATE TABLE sig_raw AS
    SELECT ch.oid AS object_id, ch.gal_key AS gallery, ch.picket, e.d AS day,
           e.d + e.t AS ts
    FROM read_parquet('{RAW_EVENTS.as_posix()}') e
    JOIN read_parquet('{CHANNELS.as_posix()}') ch ON ch.cid = e.channel_id
    WHERE e.alarm AND ch.picket IS NOT NULL
      AND ((ch.stype = 'Состояние насоса' AND e.value = 'Затоплен')
        OR (ch.stype = 'Датчик затопления' AND e.value = 'Не замкнут'))
      AND NOT (ch.oid = 5343 AND year(e.d) IN (2020, 2021))
    """
)
con.execute(
    """
    CREATE TABLE ev_time AS
    WITH g AS (
        SELECT *, date_diff('minute', lag(ts) OVER w, ts) AS gap
        FROM sig_raw WINDOW w AS (PARTITION BY object_id, gallery, picket, day ORDER BY ts)
    )
    SELECT object_id, gallery, picket, day, min(ts) AS first_ts,
           date_diff('minute', min(ts), max(ts)) AS span_minutes,
           1 + count(*) FILTER (WHERE gap > 30) AS n_episodes
    FROM g GROUP BY ALL
    """
)
con.execute(
    """
    CREATE TABLE ev_round AS
    SELECT t.object_id, t.gallery, t.picket, t.day,
           count(DISTINCT (r.gallery, r.picket)) AS other_units_within_1h
    FROM ev_time t
    LEFT JOIN sig_raw r ON r.object_id = t.object_id
        AND (r.gallery <> t.gallery OR r.picket <> t.picket)
        AND r.ts BETWEEN t.first_ts - INTERVAL 1 HOUR AND t.first_ts + INTERVAL 1 HOUR
    GROUP BY ALL
    """
)
con.execute(
    f"""
    CREATE TABLE pump_hour AS
    SELECT object_id, gallery, picket, hour, sum(on_minutes) AS on_min
    FROM read_parquet('{PUMPS.as_posix()}') WHERE picket IS NOT NULL GROUP BY ALL
    """
)
con.execute(
    """
    CREATE TABLE ev_pump AS
    SELECT t.object_id, t.gallery, t.picket, t.day,
        coalesce(sum(p.on_min) FILTER (WHERE p.hour >= date_trunc('hour', t.first_ts)
            - INTERVAL 2 HOUR AND p.hour < date_trunc('hour', t.first_ts)), 0)
            / greatest(coalesce(any_value(n.mean_on), 0) / 12, 1) AS pump_on_before_2h_ratio,
        coalesce(sum(p.on_min) FILTER (WHERE p.hour >= date_trunc('hour', t.first_ts)
            AND p.hour < date_trunc('hour', t.first_ts) + INTERVAL 2 HOUR), 0)
            / greatest(coalesce(any_value(n.mean_on), 0) / 12, 1) AS pump_on_after_2h_ratio
    FROM ev_time t
    LEFT JOIN pump_hour p ON p.object_id = t.object_id AND p.gallery = t.gallery
        AND p.picket = t.picket
        AND p.hour BETWEEN date_trunc('hour', t.first_ts) - INTERVAL 2 HOUR
                       AND date_trunc('hour', t.first_ts) + INTERVAL 1 HOUR
    LEFT JOIN pump_norm n ON n.object_id = t.object_id AND n.gallery = t.gallery
        AND n.picket = t.picket
    GROUP BY ALL
    """
)
con.execute(
    """
    CREATE TABLE ev AS
    WITH d AS (
        SELECT object_id, gallery, picket, day,
               count(*) AS n_hours, sum(moments) AS n_moments,
               max(from_sensor) AS has_sensor, max(from_pump) AS has_pump,
               bool_or(off_hours) AS labeled
        FROM sig GROUP BY ALL
    )
    SELECT d.*,
        coalesce(p.on_min, 0) / greatest(coalesce(n.mean_on, 0), 1) AS pump_on_ratio,
        coalesce(p.changes, 0) / greatest(coalesce(n.mean_changes, 0), 1)
            AS pump_changes_ratio,
        coalesce(p.allp, 0) AS pump_all_running,
        coalesce(p.unavail, 0) AS pump_unavailable,
        (SELECT count(*) FROM d o WHERE o.object_id = d.object_id AND o.day = d.day
            AND (o.gallery <> d.gallery OR o.picket <> d.picket)) AS neighbour_units,
        exists(SELECT 1 FROM d y WHERE y.object_id = d.object_id AND y.gallery = d.gallery
            AND y.picket = d.picket AND y.day = d.day - 1)::INT AS signal_yesterday,
        coalesce(w.precip_3d_mm, 0) AS precip_3d_mm,
        coalesce(w.melt_3d_cm, 0) AS melt_3d_cm,
        coalesce(tp.pump_on_before_2h_ratio, 0) AS pump_on_before_2h_ratio,
        coalesce(tp.pump_on_after_2h_ratio, 0) AS pump_on_after_2h_ratio,
        coalesce(tt.span_minutes, 0) AS span_minutes,
        coalesce(tt.n_episodes, 1) AS n_episodes,
        coalesce(tr.other_units_within_1h, 0) AS other_units_within_1h
    FROM d
    LEFT JOIN ev_time tt USING (object_id, gallery, picket, day)
    LEFT JOIN ev_round tr USING (object_id, gallery, picket, day)
    LEFT JOIN ev_pump tp USING (object_id, gallery, picket, day)
    LEFT JOIN pump_day p USING (object_id, gallery, picket, day)
    LEFT JOIN pump_norm n USING (object_id, gallery, picket)
    LEFT JOIN wx w USING (day)
    ORDER BY object_id, gallery, picket, day
    """
)
frame = con.execute("SELECT * FROM ev").df()
x = frame[FEATURES].to_numpy(dtype=np.float32)
s = frame["labeled"].to_numpy().astype(np.int8)
days = frame["day"].to_numpy().astype("datetime64[D]")
train = days < np.datetime64(TEST_START)
# Год события: с июля по июнь, как в проверочных годах модели.
season_year = (days - np.timedelta64(181, "D")).astype("datetime64[Y]").astype(int) + 1970

score = np.zeros(len(frame))
for year in sorted(set(season_year[train])):
    fit = train & (season_year != year)
    apply = train & (season_year == year)
    booster = lgb.train(PARAMS, lgb.Dataset(x[fit], label=s[fit]), num_boost_round=ROUNDS)
    score[apply] = booster.predict(x[apply])
final = lgb.train(PARAMS, lgb.Dataset(x[train], label=s[train]), num_boost_round=ROUNDS)
score[~train] = final.predict(x[~train])

# Поправка Elkan и Noto: c = средний выход на размеченных положительных.
c = float(score[train & (s == 1)].mean())
p_real = np.where(s == 1, 1.0, np.minimum(score / c, 1.0))
early = days < np.datetime64(LAST_TRAIN_YEAR[0])
CUTOFF = float(np.quantile(score[early & (s == 1)] / c, 1 - KEEP_KNOWN))
real = p_real >= CUTOFF
frame["score"] = score
frame["p_real"] = p_real
frame["real"] = real
if ATTEMPT == "export":
    # Режим выгрузки: классификатор для бэкенда. Разметка обязана совпасть с
    # принятой попыткой строка в строку, иначе бэкенд размечал бы иначе, чем
    # обучение. Файлы разметки и журнал попыток не меняются.
    accepted = pd.read_parquet(EVENTS)
    same = (accepted["real"].to_numpy() == real).all() and len(accepted) == len(real)
    if not same:
        raise SystemExit("выгрузка не воспроизводит принятую разметку")
    joblib.dump(
        {
            "booster": final,
            "features": FEATURES,
            "c": c,
            "cutoff": CUTOFF,
            "test_start": TEST_START,
            "note": "ADR 0012: вода это сигнал ночью или в нерабочий день либо score / c >= cutoff",
        },
        OUT / "pu_classifier.joblib",
    )
    print(f"классификатор выгружен: признаков {len(FEATURES)}, c {c:.4f}, граница {CUTOFF:.4f}")
    raise SystemExit(0)
con.register("labelled", frame)
con.execute(f"COPY (SELECT * FROM labelled) TO '{EVENTS.as_posix()}' (FORMAT PARQUET)")

# --- правило приёмки, только обучающие годы ------------------------------
volume = int(real[train].sum())
last = (days >= np.datetime64(LAST_TRAIN_YEAR[0])) & (days < np.datetime64(LAST_TRAIN_YEAR[1]))
own_score = score[last & (s == 1)] / c
kept_known = float((own_score >= CUTOFF).mean())
con.execute(
    f"""
    CREATE TABLE life AS
    SELECT u.object_id, u.gallery, u.picket, d.day::DATE AS day, k.is_day_off = 1 AS day_off
    FROM read_parquet('{UNITS.as_posix()}') u,
         LATERAL (SELECT unnest(generate_series(u.hour_from::DATE, u.hour_to::DATE,
                                                INTERVAL 1 DAY)) AS day) d
    JOIN {cal} k ON k.day = d.day::DATE
    WHERE d.day < DATE '{TEST_START}'
    """
)
rate = con.execute(
    """
    SELECT NULL,
           avg(coalesce(e.real, false)::INT) FILTER (WHERE NOT l.day_off),
           avg(coalesce(e.real, false)::INT) FILTER (WHERE l.day_off)
    FROM life l
    LEFT JOIN labelled e USING (object_id, gallery, picket, day)
    """
).fetchone()
ratio = rate[1] / rate[2]
# Для сравнения: та же мера у всех сигналов без разбора.
raw = con.execute(
    """
    SELECT avg((e.day IS NOT NULL)::INT) FILTER (WHERE NOT l.day_off)
         / avg((e.day IS NOT NULL)::INT) FILTER (WHERE l.day_off)
    FROM life l LEFT JOIN labelled e USING (object_id, gallery, picket, day)
    """
).fetchone()[0]

passes = {
    "volume": volume >= 1000,
    "no_work_week": 1 / 1.5 <= ratio <= 1.5,
    "keeps_known_water": kept_known >= 0.8,
}


def profile(mask: np.ndarray) -> dict:
    return {name: round(float(frame.loc[mask, name].mean()), 3) for name in FEATURES}


unl_train = train & (s == 0)
result = {
    "c": round(c, 4),
    "cutoff": round(CUTOFF, 4),
    "events_train": int(train.sum()),
    "labeled_positive_train": int((train & (s == 1)).sum()),
    "unlabeled_train": int(unl_train.sum()),
    "unlabeled_called_water_train": int((unl_train & real).sum()),
    "real_unit_days_train": volume,
    "real_unit_days_test": int(real[~train].sum()),
    "workday_to_day_off_rate_label": round(ratio, 3),
    "workday_to_day_off_rate_all_signals": round(raw, 3),
    "known_water_kept_last_train_year": round(kept_known, 3),
    "passes": passes,
    "passes_all": all(passes.values()),
    "feature_means": {
        "labeled_positive": profile(train & (s == 1)),
        "unlabeled_called_water": profile(unl_train & real),
        "unlabeled_called_check": profile(unl_train & ~real),
    },
    "gain": {
        name: round(float(g), 1)
        for name, g in sorted(
            zip(FEATURES, final.feature_importance("gain"), strict=True),
            key=lambda kv: -kv[1],
        )
    },
}
RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
if ATTEMPT != "diagnostic":
    log.append({"attempt": ATTEMPT, "feature_set": FEATURE_SET, **result})
    ATTEMPTS.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"попытка {ATTEMPT}: {len(log)} из {BUDGET}")
print(json.dumps({k: v for k, v in result.items() if k != "feature_means"},
                 ensure_ascii=False, indent=2))
for group, means in result["feature_means"].items():
    print(group, means)
