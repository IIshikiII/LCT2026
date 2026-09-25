"""Ежемесячное дообучение с весом свежих данных на отложенном году. ADR 0015.

Сценарий эксплуатации: в первый день каждого месяца модель переобучается на
всех данных до этого дня и работает месяц. Свежие сутки весят больше старых.
Скрипт проигрывает это на отложенном году, с 2025-07-01 по 2026-06-30.

Всё записано до запуска и по результату не меняется.

1. Признаки и параметры варианта `wide_reg`, три семени, среднее по логиту.
2. Вес строки обучения `0,5 ** (давность в сутках / 365)`: полупериод один
   год. Сутки пятилетней давности весят около 0,03 от вчерашних.
3. Ранней остановки нет: в эксплуатации нет отложенного отрезка для неё. Число
   деревьев это медиана лучших итераций моделей фолдов проверочных лет.
4. Тревоги ставит скользящий бюджет 7 суток от правила «событие было вчера».
   Прогнозы разных месяцев идут одним рядом, как в эксплуатации.
5. Объект 5962 в пусконаладке (ответ заказчика) исключается из обучения с
   2025-12-01 и из оценки: эксплуатация знает его заранее.
6. Метка суток известна к их концу, поэтому обучение перед месяцем `M` видит
   метки до последнего дня перед `M`.

Для сравнения идут: то же дообучение без весов, модель `out/model.joblib`,
обученная один раз, и правило.

Это четвёртое открытие отложенного года: проверка сценария, без подбора.

**Скорость.** Месяцы друг от друга не зависят, поэтому задачи «сценарий и
месяц» идут параллельно в отдельных процессах. Потоки одного процесса не
годятся: число потоков OpenMP общее на процесс, и обучения мешают друг другу.
Панель один раз ложится в `out/monthly_cache/*.npy`, процессы читают её через
отображение файла в память и не копируют каждый. Набор LightGBM строится один
раз на задачу, три семени учатся на нём. Прогресс в процентах по каждой модели
пишется в `out/monthly_progress.txt`.

Число деревьев считает этап проверочных лет. Его можно передать ключом
`--rounds`, если этап уже прошёл: источник числа записывается в итог.

Выход: `out/monthly.json`.

Запуск из корня репозитория:

    .venv\\Scripts\\python.exe ml/fire/12_monthly.py --workers 7 [--rounds 335]
"""

import argparse
import concurrent.futures as cf
import importlib
import json
import multiprocessing as mp
import os
import pathlib
import sys
import threading
import time

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "flood"))
sys.path.insert(0, str(HERE))

VARIANT = "wide_reg"
WINDOW = 7
HALF_LIFE_DAYS = 365
SEEDS = (4217, 1301, 7717)
COMMISSIONING = 5962
COMMISSIONING_FROM = np.datetime64("2025-12-01")
TEST_FROM, TEST_TO = np.datetime64("2025-07-01"), np.datetime64("2026-07-01")
MONTHS = np.arange(np.datetime64("2025-07"), np.datetime64("2026-07"), dtype="datetime64[M]")
SCENARIOS = ("weighted", "plain")
OUT = HERE / "out"
CACHE = OUT / "monthly_cache"
RESULT = OUT / "monthly.json"
PROGRESS = OUT / "monthly_progress.txt"


def write_progress(done: int, total: int, t0: float, note: str) -> None:
    spent = time.time() - t0
    share = done / total
    left = f"{(spent / share - spent) / 60:.1f}" if share else "?"
    PROGRESS.write_text(
        f"{100 * share:.1f} %  ({done} из {total} моделей)\n"
        f"прошло {spent / 60:.1f} мин, осталось около {left} мин\n"
        f"последнее: {note}\n",
        encoding="utf-8",
    )


def prepare(rounds: int | None) -> dict:
    """Кладёт панель в кэш `.npy` и возвращает число деревьев и признаки."""
    tr = importlib.import_module("06_train")
    base = tr.base
    base.BASE_PARAMS["num_threads"] = os.cpu_count() or 8
    con, all_columns = base.connect()
    chosen = next(i for i in tr.train.read_log() if i["name"] == VARIANT)
    columns = base.columns_for(all_columns, chosen["config"]["groups"])
    source = "ключ --rounds: медиана лучших итераций из прерванного прогона"
    if rounds is None:
        write_progress(0, 1, time.time(), "проверочные годы: число деревьев")
        scored = tr.train.cross_validate(con, chosen["config"], all_columns)
        rounds = int(np.median([b.best_iteration for b in scored["_boosters"]]))
        source = "медиана лучших итераций моделей фолдов"
    write_progress(0, 1, time.time(), "панель в кэш")
    frame = con.execute(
        f"SELECT {', '.join(columns)}, label, naive, hour, object_id FROM panel "
        "ORDER BY object_id, gallery, picket, hour"
    ).df()
    CACHE.mkdir(exist_ok=True)
    np.save(CACHE / "x.npy", frame[columns].to_numpy(dtype=np.float32))
    np.save(CACHE / "y.npy", frame["label"].to_numpy(dtype=np.int8))
    np.save(CACHE / "naive.npy", frame["naive"].to_numpy(dtype=np.int8))
    np.save(CACHE / "days.npy",
            frame["hour"].to_numpy().astype("datetime64[D]").astype(np.int64))
    np.save(CACHE / "obj.npy", frame["object_id"].to_numpy().astype(np.int64))
    return {"rounds": rounds, "rounds_source": source, "columns": columns,
            "config": chosen["config"]}


def cached(name: str) -> np.ndarray:
    return np.load(CACHE / f"{name}.npy", mmap_mode="r")


def train_month(scenario: str, i: int, rounds: int, config: dict, threads: int, queue) -> tuple:
    import lightgbm as lgb

    flood = importlib.import_module("10_train")
    x, y = cached("x"), cached("y")
    days = np.asarray(cached("days")).astype("datetime64[D]")
    obj = np.asarray(cached("obj"))
    exclude = (obj == COMMISSIONING) & (days >= COMMISSIONING_FROM)
    month = MONTHS[i]
    start = month.astype("datetime64[D]")
    end = (month + np.timedelta64(1, "M")).astype("datetime64[D]")
    fit = np.flatnonzero((days < start) & ~exclude)
    weight = None
    if scenario == "weighted":
        weight = 0.5 ** ((start - days[fit]).astype(np.int64) / HALF_LIFE_DAYS)
    target = (days >= start) & (days < end)
    if i == 0:
        target |= (days >= TEST_FROM - np.timedelta64(WINDOW - 1, "D")) & (days < start)
    target = np.flatnonzero(target)
    data = lgb.Dataset(np.asarray(x[fit]), label=np.asarray(y[fit]), weight=weight,
                       params={"num_threads": threads, "verbose": -1}).construct()
    rows = np.asarray(x[target])
    margins = []
    for seed in SEEDS:
        params = {**flood.BASE_PARAMS, **config.get("params", {}), "seed": seed,
                  "num_threads": threads}
        booster = lgb.train(params, data, num_boost_round=rounds)
        margins.append(booster.predict(rows, raw_score=True, num_threads=threads))
        queue.put(f"{scenario} {month}, семя {seed}")
    return scenario, target, 1 / (1 + np.exp(-np.mean(margins, axis=0)))


def report(meta: dict, preds: dict) -> dict:
    import joblib

    tr = importlib.import_module("06_train")
    base = tr.base
    rolling = importlib.import_module("14_rolling_budget")
    x, y_all, naive = cached("x"), cached("y"), cached("naive")
    days_all = np.asarray(cached("days")).astype("datetime64[D]")
    obj = np.asarray(cached("obj"))
    exclude = (obj == COMMISSIONING) & (days_all >= COMMISSIONING_FROM)
    lo = TEST_FROM - np.timedelta64(WINDOW - 1, "D")
    keep = np.flatnonzero((days_all >= lo) & (days_all < TEST_TO) & ~exclude)
    static = joblib.load(OUT / "model.joblib")
    all_preds = {"static_model": static.predict(np.asarray(x[keep])),
                 "monthly_plain": preds["plain"][keep],
                 "monthly_weighted": preds["weighted"][keep]}
    y, nv, days = np.asarray(y_all[keep]), np.asarray(naive[keep]), days_all[keep]
    t = days >= TEST_FROM
    month_of = days.astype("datetime64[M]")
    result = {
        "opening": "четвёртое: сценарий ежемесячного дообучения, без подбора",
        "variant": VARIANT, "rounds": meta["rounds"], "rounds_source": meta["rounds_source"],
        "window_days": WINDOW, "half_life_days": HALF_LIFE_DAYS,
        "excluded_object": COMMISSIONING, "base": round(float(y[t].mean()), 6),
        "rule": {**base.rule_point(y[t], nv[t] == 1),
                 "hits_by_month": [int((y * nv)[t & (month_of == m)].sum()) for m in MONTHS]},
    }
    for name, p in all_preds.items():
        flag = rolling.rolling_alerts(p, nv, days, WINDOW)
        point = base.rule_point(y[t], flag[t])
        result[name] = {
            **point,
            "pr_auc": round(base.pr_auc(y[t], p[t]), 6),
            "beats_rule": point["precision"] > result["rule"]["precision"]
            and point["recall"] > result["rule"]["recall"],
            "hits_by_month": [int((y * flag)[t & (month_of == m)].sum()) for m in MONTHS],
        }
    result["opened_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=7)
    parser.add_argument("--rounds", type=int, default=None)
    args = parser.parse_args()
    cpus = os.cpu_count() or 8
    threads = max(1, cpus // args.workers)
    meta = prepare(args.rounds)
    total = len(SCENARIOS) * len(MONTHS) * len(SEEDS)
    t0 = time.time()
    write_progress(0, total, t0, f"обучение: {args.workers} процессов по {threads} потоков, "
                                 f"деревьев {meta['rounds']}")
    n = len(cached("y"))
    preds = {s: np.zeros(n) for s in SCENARIOS}
    manager = mp.Manager()
    queue = manager.Queue()
    done = [0]

    def listen() -> None:
        while True:
            note = queue.get()
            if note is None:
                return
            done[0] += 1
            write_progress(done[0], total, t0, note)

    listener = threading.Thread(target=listen, daemon=True)
    listener.start()
    # Сначала поздние месяцы: у них больше строк, так очередь закончится ровнее.
    jobs = [(s, i) for i in reversed(range(len(MONTHS))) for s in SCENARIOS]
    with cf.ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(train_month, s, i, meta["rounds"], meta["config"], threads, queue)
                   for s, i in jobs]
        for future in cf.as_completed(futures):
            scenario, target, p = future.result()
            preds[scenario][target] = p
    queue.put(None)
    listener.join()
    write_progress(total, total, t0, "сводка")
    result = report(meta, preds)
    write_progress(total, total, t0, "готово, итог в out/monthly.json")
    print(f"отложенный год без объекта {COMMISSIONING}, база {result['base']}, "
          f"деревьев {meta['rounds']}")
    for name in ("rule", "static_model", "monthly_plain", "monthly_weighted"):
        s = result[name]
        print(f"  {name}: тревог {s['alerts']}, попаданий {s['hits']}, precision "
              f"{s['precision']:.3f}, recall {s['recall']:.3f}, PR-AUC {s.get('pr_auc', '-')}, "
              f"по месяцам {s['hits_by_month']}")


if __name__ == "__main__":
    main()
