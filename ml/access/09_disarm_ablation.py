"""Меряет вклад признаков режима «снято с охраны» в суточную модель доступа.

## Вопрос

`smvu-insights.md` §2 называет снятие с охраны самым дешёвым способом поднять
точность направления и приводит лифт 2,1×. Замер сделан на сыром сравнении
«доля тревог внутри окон против доли времени в окнах», а не на модели. Суточная
модель держит два таких признака, `disarm_hours_7d` и `disarm_share_30d`, и их
вклад по обучению равен 16 878 из 303 107, то есть 5,6 %. Вклад по обучению не
отвечает на вопрос о пользе: он считается по разрезам дерева, а не по качеству
на отложенной выборке.

Скрипт отвечает на вопрос прямо: обучает модель без этих признаков и сравнивает
с полным набором.

## Чего скрипт не меряет

Метка направления исключает тревоги внутри окон снятия охраны (ADR 0001 §9).
Это решение о метке, а не о признаках, и проверять его надо пересбором панели, а
не отбором колонок. Скрипт его не трогает.

## Помеха сравнению

Ранняя остановка обрывает обучение по проверочному отрезку. Базы отрезков
разъезжаются (обучение 0,46 %, проверка 1,21 %, отложенная 0,94 %), поэтому
число деревьев скачет, и PR-AUC меняется даже тогда, когда набор признаков не
изменился по сути. Помеха снимается повтором: каждый набор обучается на трёх
семенах, и вклад считается по среднему. Размах внутри набора идёт в замер рядом
со средним. Разность меньше размаха выводом не является.

## Запуск

Порядок обязателен: сначала `08_train_daily.py`, потом этот скрипт. Он читает
отобранные признаки из `out/daily_metrics.json`.

    .venv\\Scripts\\python.exe ml\\access\\09_disarm_ablation.py      (Windows)
    .venv/bin/python ml/access/09_disarm_ablation.py                 (Ubuntu, macOS)
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import time

import duckdb
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "out"
METRICS = OUT / "daily_metrics.json"
ABLATION = OUT / "disarm_ablation.json"

# Имя файла начинается с цифры, поэтому обычный import его не берёт.
_spec = importlib.util.spec_from_file_location("train_daily", HERE / "08_train_daily.py")
train_daily = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(train_daily)

SEEDS = (train_daily.SEED, 1301, 7717)


def variants(selected: list[str]) -> dict[str, list[str]]:
    """Наборы признаков. Ключ это имя набора, значение это отброшенные имена."""
    disarm = [c for c in selected if c.startswith("disarm")]
    return {
        "полный": [],
        "без disarm": disarm,
    }


def measure(
    data: dict[str, dict[str, np.ndarray]],
    columns: list[str],
    keep: list[str],
) -> dict[str, object]:
    """Обучает набор на трёх семенах и возвращает средние с размахом."""
    index = [columns.index(name) for name in keep]
    scores, precisions, trees = [], [], []
    for seed in SEEDS:
        train_daily.PARAMS["seed"] = seed
        booster = train_daily.train_once(data["train"], data["valid"], columns, index)
        p = booster.predict(data["test"]["x"][:, index])
        y = data["test"]["y"]
        scores.append(train_daily.pr_auc(y, p))
        naive_recall = train_daily.rule_point(y, data["test"]["naive"] == 1)["recall"]
        point = train_daily.point_at(
            y, p, train_daily.threshold_for_recall(y, p, naive_recall)
        )
        precisions.append(point["precision"])
        trees.append(booster.num_trees())
    train_daily.PARAMS["seed"] = train_daily.SEED
    return {
        "features": len(keep),
        "pr_auc_mean": round(float(np.mean(scores)), 6),
        "pr_auc_spread": round(float(max(scores) - min(scores)), 6),
        "pr_auc_runs": [round(s, 6) for s in scores],
        "precision_at_naive_recall_mean": round(float(np.mean(precisions)), 6),
        "precision_runs": [round(p, 6) for p in precisions],
        "trees": trees,
    }


def main() -> None:
    selected = json.loads(METRICS.read_text(encoding="utf-8"))["selected"]

    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    con.execute("PRAGMA memory_limit='6GB'")
    columns = train_daily.load(con)

    t = time.time()
    data = {name: train_daily.split(con, name, columns) for name in ("train", "valid", "test")}
    print(f"панель загружена за {time.time() - t:.0f} c")
    print(f"отобранных признаков: {len(selected)}")

    result: dict[str, object] = {"seeds": list(SEEDS), "selected": selected, "variants": {}}
    base: dict[str, object] | None = None

    for name, dropped in variants(selected).items():
        keep = [c for c in selected if c not in dropped]
        t = time.time()
        item = measure(data, columns, keep)
        item["dropped"] = dropped
        if base is None:
            base = item
        else:
            item["delta_pr_auc"] = round(base["pr_auc_mean"] - item["pr_auc_mean"], 6)
            item["delta_precision"] = round(
                base["precision_at_naive_recall_mean"]
                - item["precision_at_naive_recall_mean"],
                6,
            )
            item["above_spread"] = bool(
                abs(item["delta_pr_auc"]) > max(base["pr_auc_spread"], item["pr_auc_spread"])
            )
        result["variants"][name] = item
        print(
            f"  {name:16} признаков {item['features']:2d}  "
            f"PR-AUC {item['pr_auc_mean']:.6f} ±{item['pr_auc_spread']:.6f}  "
            f"точность {item['precision_at_naive_recall_mean']:.6f}  "
            f"{time.time() - t:.0f} c"
        )

    ABLATION.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\nзамер в {ABLATION}")


if __name__ == "__main__":
    main()
