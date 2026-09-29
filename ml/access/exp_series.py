"""Эксперимент: признаки, которые помогают оборвать серию событий.

Скрипт не меняет итоговую модель. Он строит кандидатов в признаки на сетке
панели и меряет, как они меняют долю продолжений серии. Серия продолжается,
когда событие было вчера и будет сегодня. Все окна кончаются строго до 00:00
суток прогноза, как в `panel.py`.

Три группы кандидатов:

- серия: длина серии, пауза перед ней, число часов с событием и размах
  последних суток с событием;
- характер: трубка УИР-Р на объекте рядом с последним событием и за прошлые
  сутки. Снятая трубка говорит о сотруднике на месте;
- соседние подсистемы объекта: пожар, затопление, обесточивание, неисправности.

Результат: `out/series_features.parquet` и `out/series_stop.json`. С ключом
`--cv` скрипт ещё обучает модели на отрезках `cv.py` с кандидатами и без них и
пишет `out/series_cv.json`. Обучение идёт несколько минут. Ключ `--holdout`
обучает те же наборы на всей истории до 2026 года на трёх семенах, как
`train.py`, и пишет замер на отложенной выборке в `out/series_holdout.json`.
Кандидаты к этому моменту уже должны быть собраны. Ключ `--stops` без обучения
разбирает по SHAP, как рабочая модель обрывает серии на отложенной выборке, и
пишет `out/series_shap.json`. Прогон идёт секунды.

Ключ `--data-root` задаёт корень копии репозитория с собранными данными:
`eda/out/events.parquet`, `notebooks/out/channel_features.parquet` и
результатами `data.py` и `panel.py` в `ml/access/out/`.

Запуск из корня репозитория:

    .venv/bin/python ml/access/exp_series.py
    .venv/bin/python ml/access/exp_series.py --cv
    .venv/bin/python ml/access/exp_series.py --holdout
    .venv/bin/python ml/access/exp_series.py --stops
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import cv  # noqa: E402

FEATURES = cv.OUT / "series_features.parquet"
REPORT = cv.OUT / "series_stop.json"
CV_REPORT = cv.OUT / "series_cv.json"
HOLDOUT_REPORT = cv.OUT / "series_holdout.json"
STOPS_REPORT = cv.OUT / "series_shap.json"

KEY = ["object_id", "gallery", "section", "day"]

GROUPS = {
    "серия": ["streak_len", "gap_before_streak", "last_ev_hours", "last_ev_span_h"],
    "характер": ["ev_near_uir", "uir_obj_1d", "uir_sec_1d"],
    "соседние подсистемы": ["fault_obj_1d", "flood_obj_7d", "power_obj_7d", "fire_obj_7d"],
}

# Границы корзин для отчёта, левая включена.
BINS = {
    "streak_len": [1, 2, 3, 5, 8, 10**6],
    "gap_before_streak": [0, 2, 8, 31, 91, 10**6],
    "last_ev_hours": [1, 2, 3, 5, 10**6],
    "last_ev_span_h": [0, 1, 3, 8, 10**6],
    "ev_near_uir": [0, 1, 2],
    "uir_obj_1d": [0, 1, 10, 10**6],
    "uir_sec_1d": [0, 1, 10**6],
    "fault_obj_1d": [0, 1, 10, 10**6],
    "flood_obj_7d": [0, 1, 10**6],
    "power_obj_7d": [0, 1, 10**6],
    "fire_obj_7d": [0, 1, 5, 10**6],
}

# Значения сигналов соседних подсистем. Счёт идёт в парах «канал и час».
SIGNALS = """
CASE
  WHEN ch.stype = 'Состояние УИР-Р' AND ev.value IN ('Вызов', 'Разговор', 'Рычаг сдернут')
    THEN 'uir'
  WHEN (ch.stype = 'Датчик дыма' AND ev.value = 'Обнаружен дым')
    OR (ch.stype = 'Тепловой датчик' AND ev.value = 'Не замкнут')
    OR (ch.stype = 'Ручной извещатель' AND ev.value IN ('Не замкнут', 'Рычаг сдернут'))
    THEN 'fire'
  WHEN (ch.stype = 'Датчик затопления' AND ev.value = 'Не замкнут')
    OR (ch.stype = 'Состояние насоса' AND ev.value IN ('Затоплен', 'Работают все насосы в АНС'))
    THEN 'flood'
  WHEN (ch.stype IN ('Состояние насоса', 'Состояние вентилятора') AND ev.value = 'Обесточен')
    OR (ch.stype = 'ИБП' AND ev.value = 'Питание от батарей')
    THEN 'power'
  WHEN ev.value IN ('Неисправен', 'Отключено устройство') THEN 'fault'
END
"""
ACCESS_STYPES = (
    "'КД Дверь', 'КД АВ', 'КД Люк', 'Стекло', '9-секционный люк', "
    "'Датчик движения', 'Состояние фазы'"
)


def _window(column: str, days: int, part: str) -> str:
    return (
        f"sum(coalesce({column}, 0)) OVER (PARTITION BY {part} ORDER BY day "
        f"RANGE BETWEEN INTERVAL {days} DAY PRECEDING AND INTERVAL 1 DAY PRECEDING)"
    )


def build(root: pathlib.Path) -> None:
    """Кандидаты в признаки на строках панели: `out/series_features.parquet`."""
    out = root / "ml" / "access" / "out"
    events = (root / "eda" / "out" / "events.parquet").as_posix()
    channels = (root / "notebooks" / "out" / "channel_features.parquet").as_posix()
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    con.execute("PRAGMA memory_limit='6GB'")
    for view, name in (("panel", "daily_panel"), ("armed", "access_hourly_armed"),
                       ("sections", "sections"), ("disarm", "disarm_windows")):
        con.execute(f"CREATE VIEW {view} AS SELECT * FROM '{(out / name).as_posix()}.parquet'")

    con.execute(
        f"""
        CREATE TABLE sig AS
        SELECT ch.oid AS object_id, ch.gal_key AS gallery, sm.section,
               {SIGNALS} AS kind,
               date_trunc('hour', ev.d + ev.t) AS hour,
               count(DISTINCT ev.channel_id) AS n_ch
        FROM '{events}' ev JOIN '{channels}' ch ON ch.cid = ev.channel_id
        LEFT JOIN sections sm
          ON sm.object_id = ch.oid AND sm.gallery = ch.gal_key AND sm.picket = ch.picket
        WHERE ch.stype NOT IN ({ACCESS_STYPES})
        GROUP BY ALL HAVING kind IS NOT NULL
        """
    )
    # Сутки с событием, номер серии и пауза перед её началом.
    con.execute(
        """
        CREATE TABLE ev_series AS
        WITH d AS (
            SELECT object_id, gallery, section, date_trunc('day', hour)::DATE AS day,
                   count(*) AS ev_hours, min(hour) AS first_hour, max(hour) AS last_hour
            FROM armed GROUP BY ALL
        ),
        a AS (
            SELECT *, day - CAST(row_number() OVER w AS INTEGER) AS grp,
                   lag(day) OVER w AS prev_day
            FROM d WINDOW w AS (PARTITION BY object_id, gallery, section ORDER BY day)
        ),
        s AS (
            SELECT *, min(day) OVER (PARTITION BY object_id, gallery, section, grp) AS s_start
            FROM a
        ),
        g AS (
            SELECT object_id, gallery, section, s_start,
                   min(prev_day) FILTER (WHERE day = s_start) AS before_start
            FROM s GROUP BY ALL
        )
        SELECT s.object_id, s.gallery, s.section, s.day, s.ev_hours, s.first_hour, s.last_hour,
               date_diff('day', s.s_start, s.day) + 1 AS streak_len,
               date_diff('day', g.before_start, s.s_start) - 1 AS gap_before_streak
        FROM s JOIN g USING (object_id, gallery, section, s_start)
        """
    )
    con.execute(
        """
        CREATE TABLE last_ev AS
        SELECT p.object_id, p.gallery, p.section, p.day,
               e.ev_hours AS last_ev_hours,
               date_diff('hour', e.first_hour, e.last_hour) AS last_ev_span_h,
               CASE WHEN e.day = p.day - 1 THEN e.streak_len ELSE 0 END AS streak_len,
               CASE WHEN e.day = p.day - 1 THEN e.gap_before_streak END AS gap_before_streak,
               e.last_hour
        FROM panel p
        ASOF LEFT JOIN ev_series e
          ON e.object_id = p.object_id AND e.gallery = p.gallery
         AND e.section = p.section AND e.day < p.day
        """
    )
    # Трубка УИР-Р на объекте за 3 часа до или после последнего события.
    con.execute(
        """
        CREATE TABLE near_uir AS
        SELECT l.object_id, l.gallery, l.section, l.day,
               CAST(EXISTS (
                   SELECT 1 FROM sig s
                   WHERE s.object_id = l.object_id AND s.kind = 'uir'
                     AND s.hour BETWEEN l.last_hour - INTERVAL 3 HOUR
                                    AND l.last_hour + INTERVAL 3 HOUR
                     AND s.hour < l.day
               ) AS INTEGER) AS ev_near_uir
        FROM last_ev l WHERE l.last_hour IS NOT NULL
        """
    )
    obj = "object_id"
    sec = "object_id, gallery, section"
    con.execute(
        f"""
        CREATE TABLE obj_rolled AS
        WITH d AS (
            SELECT object_id, date_trunc('day', hour)::DATE AS day,
                   count(*) FILTER (WHERE kind = 'uir') AS uir,
                   count(*) FILTER (WHERE kind = 'fire') AS fire,
                   count(*) FILTER (WHERE kind = 'flood') AS flood,
                   count(*) FILTER (WHERE kind = 'power') AS power,
                   count(*) FILTER (WHERE kind = 'fault') AS fault
            FROM sig GROUP BY ALL
        ),
        g AS (SELECT DISTINCT object_id, day FROM panel)
        SELECT object_id, day,
               {_window('uir', 1, obj)} AS uir_obj_1d,
               {_window('fire', 7, obj)} AS fire_obj_7d,
               {_window('flood', 7, obj)} AS flood_obj_7d,
               {_window('power', 7, obj)} AS power_obj_7d,
               {_window('fault', 1, obj)} AS fault_obj_1d
        FROM g LEFT JOIN d USING (object_id, day)
        """
    )
    con.execute(
        f"""
        CREATE TABLE sec_rolled AS
        WITH d AS (
            SELECT object_id, gallery, section, date_trunc('day', hour)::DATE AS day,
                   count(*) AS uir
            FROM sig WHERE kind = 'uir' AND section IS NOT NULL GROUP BY ALL
        )
        SELECT object_id, gallery, section, day, {_window('uir', 1, sec)} AS uir_sec_1d
        FROM (SELECT object_id, gallery, section, day FROM panel) p
        LEFT JOIN d USING (object_id, gallery, section, day)
        """
    )
    con.execute(
        f"""
        COPY (
            SELECT p.object_id, p.gallery, p.section, p.day,
                   coalesce(l.streak_len, 0) AS streak_len, l.gap_before_streak,
                   l.last_ev_hours, l.last_ev_span_h,
                   coalesce(n.ev_near_uir, 0) AS ev_near_uir,
                   coalesce(o.uir_obj_1d, 0) AS uir_obj_1d,
                   coalesce(s.uir_sec_1d, 0) AS uir_sec_1d,
                   coalesce(o.fault_obj_1d, 0) AS fault_obj_1d,
                   coalesce(o.flood_obj_7d, 0) AS flood_obj_7d,
                   coalesce(o.power_obj_7d, 0) AS power_obj_7d,
                   coalesce(o.fire_obj_7d, 0) AS fire_obj_7d
            FROM panel p
            LEFT JOIN last_ev l USING (object_id, gallery, section, day)
            LEFT JOIN near_uir n USING (object_id, gallery, section, day)
            LEFT JOIN obj_rolled o USING (object_id, day)
            LEFT JOIN sec_rolled s USING (object_id, gallery, section, day)
        ) TO '{FEATURES.as_posix()}' (FORMAT PARQUET)
        """
    )


def load(root: pathlib.Path) -> pd.DataFrame:
    """Панель с кандидатами в признаки, строки в порядке `cv.load`."""
    panel = (root / "ml" / "access" / "out" / "daily_panel.parquet").as_posix()
    return duckdb.connect().execute(
        f"""
        SELECT p.*, f.* EXCLUDE (object_id, gallery, section, day)
        FROM '{panel}' p JOIN '{FEATURES.as_posix()}' f USING (object_id, gallery, section, day)
        ORDER BY object_id, gallery, section, day
        """
    ).df()


def continuation(rows: pd.DataFrame, column: str) -> list[dict]:
    """Доля продолжений серии по корзинам признака среди строк «событие было вчера»."""
    bins = pd.cut(rows[column], BINS[column], right=False)
    table = rows.groupby(bins, observed=True)["label"].agg(["size", "sum", "mean"])
    return [
        {"bin": str(b), "rows": int(r["size"]), "continued": int(r["sum"]),
         "share": round(float(r["mean"]), 4)}
        for b, r in table.iterrows()
    ]


def report(frame: pd.DataFrame) -> dict:
    """Продолжения серий по кандидатам и обрывы серий у рабочей модели."""
    import lightgbm as lgb

    history = frame[(frame["naive"] == 1) & (frame["day"] < pd.Timestamp(cv.HOLDOUT_FROM))]
    result = {
        "history": {
            "rows": int(len(history)),
            "continued": int(history["label"].sum()),
            "features": {c: continuation(history, c) for c in BINS},
        }
    }

    metrics = json.loads((cv.OUT / "daily_metrics.json").read_text(encoding="utf-8"))
    selected, threshold = metrics["selected"], float(metrics["decision"]["threshold"])
    test = frame[(frame["naive"] == 1) & (frame["day"] >= pd.Timestamp(cv.HOLDOUT_FROM))]
    booster = lgb.Booster(model_file=str(cv.OUT / "daily_model.txt"))
    p = booster.predict(test[selected].to_numpy(np.float32))
    keep = p >= threshold
    y = test["label"].to_numpy()
    result["holdout"] = {
        "rows": int(len(test)),
        "continued": int(y.sum()),
        "model_kept": int(keep.sum()),
        "model_kept_continued": int(y[keep].sum()),
        "model_stopped": int((~keep).sum()),
        "model_stopped_right": int((y[~keep] == 0).sum()),
        "features": {c: continuation(test, c) for c in BINS},
    }
    return result


def stops(frame: pd.DataFrame) -> dict:
    """Как рабочая модель обрывает серии на отложенной выборке: средний вклад
    признаков по SHAP в четырёх группах и примеры. Обучения нет, прогон идёт
    секунды: он берёт только строки «событие было вчера» из 2026 года."""
    import lightgbm as lgb
    import shap

    metrics = json.loads((cv.OUT / "daily_metrics.json").read_text(encoding="utf-8"))
    selected, threshold = metrics["selected"], float(metrics["decision"]["threshold"])
    rows = frame[(frame["naive"] == 1) & (frame["day"] >= pd.Timestamp(cv.HOLDOUT_FROM))].copy()
    booster = lgb.Booster(model_file=str(cv.OUT / "daily_model.txt"))
    x = rows[selected].to_numpy(np.float32)
    rows["p"] = booster.predict(x)
    contrib = pd.DataFrame(shap.TreeExplainer(booster).shap_values(x),
                           columns=selected, index=rows.index)
    keep, y = rows["p"] >= threshold, rows["label"] == 1
    rows["group"] = np.select(
        [~keep & ~y, keep & y, ~keep & y],
        ["верно оборвала", "верно продлила", "ошибочно оборвала"], "ошибочно продлила")

    candidates = [c for group in GROUPS.values() for c in group]

    def value(v) -> float | None:
        return None if pd.isna(v) else round(float(v), 4)

    def card(i: int) -> dict:
        r = rows.loc[i]
        top = contrib.loc[i].abs().sort_values(ascending=False).index[:5]
        return {
            "object_id": int(r["object_id"]), "gallery": int(r["gallery"]),
            "section": int(r["section"]), "day": str(r["day"])[:10],
            "probability": round(float(r["p"]), 4),
            "candidates": {c: value(r[c]) for c in candidates},
            "factors": [{"feature": f, "value": value(r[f]),
                         "shap": round(float(contrib.loc[i, f]), 4)} for f in top],
        }

    right = rows[rows["group"] == "верно оборвала"]
    return {
        "threshold": threshold,
        "groups": rows["group"].value_counts().to_dict(),
        "mean_shap": contrib.groupby(rows["group"]).mean().round(4).T.to_dict(),
        "mean_candidates": rows.groupby("group")[candidates].mean().round(3).T.to_dict(),
        # Планка уверена сильнее всего: длинная или частая серия, а модель верно оборвала.
        "longest_stopped": [card(i) for i in right.sort_values(
            ["streak_len", "armed_day_share_30d"], ascending=False).index[:8]],
        "surest_stopped": [card(i) for i in right.nsmallest(5, "p").index],
        "wrongly_stopped": [card(i) for i in rows[rows["group"] == "ошибочно оборвала"].index],
    }


def to_panel(frame: pd.DataFrame) -> cv.Panel:
    columns = [c for c in frame.columns if c not in cv.NOT_FEATURES]
    return cv.Panel(
        x=frame[columns].to_numpy(dtype=np.float32),
        y=frame["label"].to_numpy(dtype=np.int8),
        naive=frame["naive"].to_numpy(dtype=np.int8),
        day=frame["day"].to_numpy(dtype="datetime64[D]"),
        since_armed=frame["days_since_last_armed"].to_numpy(dtype=np.int32),
        columns=columns,
    )


def feature_sets() -> dict[str, list[str]]:
    """Рабочий набор `train.py` и он же с кандидатами."""
    selected = json.loads((cv.OUT / "daily_metrics.json").read_text(encoding="utf-8"))["selected"]
    candidates = [c for group in GROUPS.values() for c in group]
    return {
        "рабочий": selected,
        "рабочий и серия": selected + GROUPS["серия"],
        "рабочий и все кандидаты": selected + candidates,
    }


def series_point(test: cv.Panel, p: np.ndarray, threshold: float) -> dict:
    """Строки «событие было вчера» на пороге: сколько серий модель продлила и оборвала."""
    mask = test.naive == 1
    y, keep = test.y[mask], p[mask] >= threshold
    return {
        "rows": int(mask.sum()),
        "continued": int(y.sum()),
        "kept": int(keep.sum()),
        "kept_continued": int(y[keep].sum()),
        "stopped": int((~keep).sum()),
        "stopped_right": int((y[~keep] == 0).sum()),
        "series_pr_auc": round(cv.pr_auc(y, p[mask]), 6),
    }


def holdout(frame: pd.DataFrame) -> dict:
    """Итоговый замер как в `train.py`: обучение на всей истории до 2026 года,
    замер на первом полугодии 2026 года, три семени."""
    import train

    panel = to_panel(frame)
    fit, es, test = cv.split(panel, cv.HOLDOUT_FROM, cv.HOLDOUT_FROM, None)
    days = int((test.day.max() - test.day.min()).astype(int)) + 1
    result = {}
    for name, features in feature_sets().items():
        index = [panel.columns.index(c) for c in features]
        runs = []
        for seed in train.SEEDS:
            booster = cv.fit(fit, es, index, seed=seed)
            p = booster.predict(test.x[:, index])
            item = cv.score(test, p)
            working = item["at_naive_recall"]
            threshold = float(working["threshold"])
            runs.append({
                "seed": seed,
                "trees": booster.num_trees(),
                "pr_auc": item["pr_auc"],
                "pr_auc_lift": round(item["pr_auc"] / item["base"], 3),
                "precision_at_naive_recall": working["precision"],
                "recall_at_naive_alerts": item["at_naive_alerts"]["recall"],
                "threshold": round(threshold, 6),
                "alerts_per_day": round(working["alerts"] / days, 2),
                "curve": {n: cv.top_k(test.y, p, n * days) for n in (2, 5, 10)},
                "by_recency": train.by_recency(test, p, threshold),
                "series": series_point(test, p, threshold),
            })
        mean = {k: round(float(np.mean([r[k] for r in runs])), 4) for k in
                ("pr_auc", "pr_auc_lift", "precision_at_naive_recall",
                 "recall_at_naive_alerts", "alerts_per_day")}
        mean["recall_at_5_per_day"] = round(
            float(np.mean([r["curve"][5]["recall"] for r in runs])), 4)
        result[name] = {"features": len(features), "mean": mean, "runs": runs}
        print(f"{name:26s} PR-AUC {mean['pr_auc']:.4f}  точность {mean['precision_at_naive_recall']:.3f}  "
              f"полнота при 5 в сутки {mean['recall_at_5_per_day']:.3f}  "
              f"тревог в сутки {mean['alerts_per_day']}")
    result["naive"] = cv.rule_point(test.y, test.naive == 1)
    result["days"] = days
    return result


def compare(frame: pd.DataFrame) -> dict:
    """Модели на отрезках `cv.py`: рабочий набор против набора с кандидатами."""
    panel = to_panel(frame)
    columns = panel.columns
    sets = feature_sets()
    result, folds = {}, {}
    for name, features in sets.items():
        index = [columns.index(c) for c in features]
        runs = []
        for start, end in cv.FOLDS:
            train, es, test = cv.split(panel, start, start, end)
            booster = cv.fit(train, es, index)
            p = booster.predict(test.x[:, index])
            item = cv.score(test, p)
            # Обрыв серии: ранжирование среди строк «событие было вчера».
            mask = test.naive == 1
            item["series_pr_auc"] = round(cv.pr_auc(test.y[mask], p[mask]), 6)
            item["gain"] = dict(zip(features, booster.feature_importance("gain").tolist()))
            runs.append(item)
        folds[name] = runs
        pr = np.array([r["pr_auc"] for r in runs])
        prec = np.array([r["at_naive_recall"]["precision"] for r in runs])
        series = np.array([r["series_pr_auc"] for r in runs])
        result[name] = {
            "features": len(features),
            "pr_auc_mean": round(float(pr.mean()), 6),
            "pr_auc_se": round(float(pr.std(ddof=1) / np.sqrt(len(pr))), 6),
            "precision_at_naive_recall_mean": round(float(prec.mean()), 6),
            "series_pr_auc_mean": round(float(series.mean()), 6),
        }
        print(f"{name:26s} PR-AUC {pr.mean():.4f} ± {result[name]['pr_auc_se']:.4f}  "
              f"точность {prec.mean():.3f}  серии {series.mean():.3f}")

    base = np.array([r["pr_auc"] for r in folds["рабочий"]])
    for name in list(sets)[1:]:
        diff = np.array([r["pr_auc"] for r in folds[name]]) - base
        delta, se = float(diff.mean()), float(diff.std(ddof=1) / np.sqrt(len(diff)))
        result[name]["delta_pr_auc"] = round(delta, 6)
        result[name]["delta_se"] = round(se, 6)
        result[name]["significant"] = bool(abs(delta) > 2 * se)
        print(f"{name}: разность {delta:+.4f} ± {se:.4f}")
    gain = {}
    for r in folds["рабочий и все кандидаты"]:
        total = sum(r["gain"].values())
        for c, v in r["gain"].items():
            gain[c] = gain.get(c, 0.0) + v / total / len(cv.FOLDS)
    result["gain_share_all"] = {c: round(v, 5) for c, v in
                                sorted(gain.items(), key=lambda kv: -kv[1])}
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", default=str(cv.OUT.parents[2]),
                        help="корень копии репозитория с собранными данными")
    parser.add_argument("--cv", action="store_true", help="обучить модели на отрезках cv.py")
    parser.add_argument("--holdout", action="store_true",
                        help="только замер на отложенной выборке, кандидаты уже собраны")
    parser.add_argument("--stops", action="store_true",
                        help="быстрый разбор обрывов серий по SHAP, кандидаты уже собраны")
    args = parser.parse_args()
    root = pathlib.Path(args.data_root)

    if args.stops:
        result = stops(load(root))
        STOPS_REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
        print(result["groups"])
        return
    if args.holdout:
        HOLDOUT_REPORT.write_text(
            json.dumps(holdout(load(root)), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        return

    t = time.time()
    build(root)
    print(f"кандидаты собраны за {time.time() - t:.0f} c")
    frame = load(root)
    result = report(frame)
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    h = result["holdout"]
    print(f"отложенная выборка: вчера событие в {h['rows']} строках, продолжений "
          f"{h['continued']}; модель оборвала {h['model_stopped']}, верно "
          f"{h['model_stopped_right']}")
    if args.cv:
        CV_REPORT.write_text(json.dumps(compare(frame), ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")


if __name__ == "__main__":
    main()
