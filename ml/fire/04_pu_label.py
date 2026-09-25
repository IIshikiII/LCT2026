"""Метка пожарного риска по признакам события, а не по часу. ADR 0014.

Способ повторяет `ml/flood/11_pu_label.py` (ADR 0012).

Событие это сутки пикета с сигналом метки: «Обнаружен дым» датчика дыма,
«Температура выше 40ºC» датчика температуры, «Не замкнут» теплового датчика.
Часть событий оставляет обход с проверкой, часть нет.

- Размеченные положительные примеры: событие с сигналом ночью (22:00–6:00) или
  в нерабочий день. Обходов в это время почти нет.
- Неразмеченные: остальные события.
- Классификатор отличает первых от вторых по признакам события без признаков
  времени. Поправка Elkan и Noto переводит его выход в вероятность события вне
  проверки.

Главный признак обхода это его форма: техник идёт по коллектору и проверяет
датчики подряд. Сутки объекта с дымом только в рабочем окне дают в среднем
15,9 датчика разом.

Попытка `a2`: число пикетов в тревоге делится на число живых пикетов объекта
с пожарными датчиками. В 2025–2026 годах датчиков дыма стало больше, и
абсолютное число пикетов в тревоге сдвинулось между годами. Попытка `a1`
брала абсолютное число и потеряла 23,6 % размеченных событий последнего
обучающего года.

Классификатор учится с перекрёстной подгонкой по годам. События отложенного
года (с 2025-07-01) метит модель, обученная на всех событиях до него.

Выход: `out/pu_events.parquet`, `out/pu_label.json`. Попытка с именем пишется в
`out/pu_attempts.json`, бюджет три попытки.

Запуск: `.venv\\Scripts\\python.exe ml/fire/04_pu_label.py <попытка>`. Имя
`diagnostic` журнал не пишет, имя `export` выгружает классификатор.
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
ALARMS = OUT / "fire_alarms.parquet"
UNITS = OUT / "fire_units.parquet"
PPR = OUT / "ppr_2026.parquet"
EVENTS = OUT / "pu_events.parquet"
RESULT = OUT / "pu_label.json"
ATTEMPTS = OUT / "pu_attempts.json"
BUDGET = 3

TEST_START = "2025-07-01"
LAST_TRAIN_YEAR = ("2024-07-01", "2025-07-01")
# Граница «событие или проверка»: квантиль оценки у размеченных событий до
# последнего обучающего года. Сохраняет 80 % из них, как у подтопления.
KEEP_KNOWN = 0.8
# Сопоставление объекта 4610 с графиком ППР журнал не подтвердил (03_ppr.py).
PPR_REJECTED = (4610,)
FEATURES = [
    "n_moments",
    "n_hours",
    "n_channels",
    "has_smoke",
    "has_heat",
    "has_temp",
    "span_minutes",
    "n_episodes",
    "object_units_share",
    "other_units_within_1h_share",
    "walk_gap_minutes",
    "faults_same_day",
    "signal_yesterday",
]
ATTEMPT = sys.argv[1] if len(sys.argv) > 1 else "diagnostic"
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
    SELECT a.*, a.moment::DATE AS day,
        hour(a.moment) >= 22 OR hour(a.moment) < 6 OR k.is_day_off = 1 AS off_hours
    FROM read_parquet('{ALARMS.as_posix()}') a
    JOIN {cal} k ON k.day = a.moment::DATE
    """
)
# Неисправность датчиков пикета в те же сутки: пыль и влага дают датчику дыма
# и ложный дым, и «Неисправен».
con.execute(
    f"""
    CREATE TABLE fault AS
    SELECT ch.oid AS object_id, ch.gal_key AS gallery, ch.picket, e.d AS day,
        count(*) AS faults_same_day
    FROM read_parquet('{RAW_EVENTS.as_posix()}') e
    JOIN read_parquet('{CHANNELS.as_posix()}') ch ON ch.cid = e.channel_id
    WHERE e.alarm AND e.value = 'Неисправен' AND ch.picket IS NOT NULL
      AND ch.stype IN ('Датчик дыма', 'Датчик температуры', 'Тепловой датчик')
      AND NOT (ch.oid = 5343 AND year(e.d) IN (2020, 2021))
    GROUP BY ALL
    """
)
con.execute(
    """
    CREATE TABLE ev_time AS
    WITH g AS (
        SELECT *, date_diff('minute', lag(moment) OVER w, moment) AS gap
        FROM sig WINDOW w AS (PARTITION BY object_id, gallery, picket, day ORDER BY moment)
    )
    SELECT object_id, gallery, picket, day, min(moment) AS first_ts,
        count(*) AS n_moments,
        count(DISTINCT hour(moment)) AS n_hours,
        count(DISTINCT channel_id) AS n_channels,
        max((stype = 'Датчик дыма')::INT) AS has_smoke,
        max((stype = 'Тепловой датчик')::INT) AS has_heat,
        max((stype = 'Датчик температуры')::INT) AS has_temp,
        date_diff('minute', min(moment), max(moment)) AS span_minutes,
        1 + count(*) FILTER (WHERE gap > 30) AS n_episodes,
        bool_or(off_hours) AS labeled
    FROM g GROUP BY object_id, gallery, picket, day
    """
)
# Форма обхода: шаг между первыми сигналами соседних пикетов объекта за сутки.
con.execute(
    """
    CREATE TABLE ev_walk AS
    WITH o AS (
        SELECT object_id, day, first_ts,
            date_diff('minute', lag(first_ts) OVER w, first_ts) AS step
        FROM ev_time WINDOW w AS (PARTITION BY object_id, day ORDER BY first_ts)
    )
    SELECT object_id, day, count(*) AS object_units_same_day,
        coalesce(median(step), 1440) AS walk_gap_minutes
    FROM o GROUP BY ALL
    """
)
con.execute(
    """
    CREATE TABLE ev_round AS
    SELECT t.object_id, t.gallery, t.picket, t.day,
        count(DISTINCT (r.gallery, r.picket)) AS other_units_within_1h
    FROM ev_time t
    LEFT JOIN sig r ON r.object_id = t.object_id
        AND (r.gallery <> t.gallery OR r.picket <> t.picket)
        AND r.moment BETWEEN t.first_ts - INTERVAL 1 HOUR AND t.first_ts + INTERVAL 1 HOUR
    GROUP BY ALL
    """
)
con.execute(
    f"""
    CREATE TABLE alive AS
    SELECT e.object_id, e.day, count(*) AS object_units_alive
    FROM (SELECT DISTINCT object_id, day FROM ev_time) e
    JOIN read_parquet('{UNITS.as_posix()}') u ON u.object_id = e.object_id
        AND e.day BETWEEN u.hour_from::DATE AND u.hour_to::DATE
    GROUP BY ALL
    """
)
con.execute(
    """
    CREATE TABLE ev AS
    SELECT t.* EXCLUDE (first_ts),
        w.object_units_same_day - 1 AS object_units_same_day,
        w.walk_gap_minutes,
        coalesce(r.other_units_within_1h, 0) AS other_units_within_1h,
        coalesce(f.faults_same_day, 0) AS faults_same_day,
        (w.object_units_same_day - 1) / greatest(a.object_units_alive, 1)
            AS object_units_share,
        coalesce(r.other_units_within_1h, 0) / greatest(a.object_units_alive, 1)
            AS other_units_within_1h_share,
        exists(SELECT 1 FROM ev_time y WHERE y.object_id = t.object_id
            AND y.gallery = t.gallery AND y.picket = t.picket
            AND y.day = t.day - 1)::INT AS signal_yesterday
    FROM ev_time t
    JOIN ev_walk w USING (object_id, day)
    LEFT JOIN alive a USING (object_id, day)
    LEFT JOIN ev_round r USING (object_id, gallery, picket, day)
    LEFT JOIN fault f USING (object_id, gallery, picket, day)
    ORDER BY object_id, gallery, picket, day
    """
)
frame = con.execute("SELECT * FROM ev").df()
x = frame[FEATURES].to_numpy(dtype=np.float32)
s = frame["labeled"].to_numpy().astype(np.int8)
days = frame["day"].to_numpy().astype("datetime64[D]")
train = days < np.datetime64(TEST_START)
season_year = (days - np.timedelta64(181, "D")).astype("datetime64[Y]").astype(int) + 1970

score = np.zeros(len(frame))
for year in sorted(set(season_year[train])):
    fit = train & (season_year != year)
    apply = train & (season_year == year)
    booster = lgb.train(PARAMS, lgb.Dataset(x[fit], label=s[fit]), num_boost_round=ROUNDS)
    score[apply] = booster.predict(x[apply])
final = lgb.train(PARAMS, lgb.Dataset(x[train], label=s[train]), num_boost_round=ROUNDS)
score[~train] = final.predict(x[~train])

c = float(score[train & (s == 1)].mean())
p_real = np.where(s == 1, 1.0, np.minimum(score / c, 1.0))
early = days < np.datetime64(LAST_TRAIN_YEAR[0])
CUTOFF = float(np.quantile(score[early & (s == 1)] / c, 1 - KEEP_KNOWN))
real = p_real >= CUTOFF
frame["score"] = score
frame["p_real"] = p_real
frame["real"] = real
if ATTEMPT == "export":
    accepted = pd.read_parquet(EVENTS)
    same = len(accepted) == len(real) and (accepted["real"].to_numpy() == real).all()
    if not same:
        raise SystemExit("выгрузка не воспроизводит принятую разметку")
    joblib.dump(
        {
            "booster": final,
            "features": FEATURES,
            "c": c,
            "cutoff": CUTOFF,
            "test_start": TEST_START,
            "note": "ADR 0014: событие это сигнал ночью или в нерабочий день либо score / c >= cutoff",
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
kept_known = float((score[last & (s == 1)] / c >= CUTOFF).mean())
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


def week_ratio(condition: str) -> float:
    return con.execute(
        f"""
        SELECT avg(coalesce({condition}, false)::INT) FILTER (WHERE NOT l.day_off)
             / avg(coalesce({condition}, false)::INT) FILTER (WHERE l.day_off)
        FROM life l LEFT JOIN labelled e USING (object_id, gallery, picket, day)
        """
    ).fetchone()[0]


ratio = week_ratio("e.real")
raw = week_ratio("e.day IS NOT NULL")

# Внешняя проверка графиком ППР, первое полугодие 2026 года. График описывает
# датчики метана, а не дыма, поэтому проверка косвенная: объект в окне ППР
# принимает бригаду, и её обход задевает и пожарную сигнализацию.
ppr = pd.read_parquet(PPR).dropna(subset=["object_id"])
ppr = ppr[~ppr["object_id"].isin(PPR_REJECTED)]
in_window = np.zeros(len(frame), dtype=bool)
for row in ppr.itertuples():
    end = row.back_from_check or row.accepted
    in_window |= (
        (frame["object_id"].to_numpy() == row.object_id)
        & (days >= np.datetime64(row.dismantle_from))
        & (days <= np.datetime64(end))
    )
h1_2026 = (days >= np.datetime64("2026-01-01")) & np.isin(
    frame["object_id"].to_numpy(), ppr["object_id"].to_numpy()
)
ppr_check = {
    "events_in_window": int((h1_2026 & in_window).sum()),
    "check_share_in_window": round(float((~real[h1_2026 & in_window]).mean()), 3)
    if (h1_2026 & in_window).any()
    else None,
    "events_out_of_window": int((h1_2026 & ~in_window).sum()),
    "check_share_out_of_window": round(float((~real[h1_2026 & ~in_window]).mean()), 3)
    if (h1_2026 & ~in_window).any()
    else None,
}

passes = {
    "volume": volume >= 1000,
    "no_work_week": 1 / 1.5 <= ratio <= 1.5,
    "keeps_known_events": kept_known >= 0.8,
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
    "unlabeled_called_event_train": int((unl_train & real).sum()),
    "real_unit_days_train": volume,
    "real_unit_days_test": int(real[~train].sum()),
    "workday_to_day_off_rate_label": round(ratio, 3),
    "workday_to_day_off_rate_all_signals": round(raw, 3),
    "known_events_kept_last_train_year": round(kept_known, 3),
    "ppr_check_2026_h1": ppr_check,
    "passes": passes,
    "passes_all": all(passes.values()),
    "feature_means": {
        "labeled_positive": profile(train & (s == 1)),
        "unlabeled_called_event": profile(unl_train & real),
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
    log.append({"attempt": ATTEMPT, **result})
    ATTEMPTS.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"попытка {ATTEMPT}: {len(log)} из {BUDGET}")
print(json.dumps({k: v for k, v in result.items() if k != "feature_means"},
                 ensure_ascii=False, indent=2))
for group, means in result["feature_means"].items():
    print(group, means)
