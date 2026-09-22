"""Калибрует вероятность модели «несанкционированный доступ» изотонической
регрессией. Читает модель `out/model.txt` и границы отрезков из
`out/metrics.json`, дописывает в него раздел `calibration`.

## Почему изотоническая регрессия и почему на проверочном отрезке

ADR 0002, находка «Вероятность модели не равна доле оправданных нарядов»:
средняя вероятность занижена в нижней полосе и завышена в верхней. LightGBM
даёт монотонный, но не откалиброванный ранг. Изотоническая регрессия чинит
именно это: она ищет неубывающую функцию от сырой вероятности к событию и не
навязывает форму кривой, в отличие от логистической калибровки (сигмоида
Платта), которая предполагает S-образную форму.

Калибратор обучается на проверочном отрезке (`valid`, 2025-07-01 —
2025-12-30), а не на отложенной выборке (`test`), которой меряется итог. Тот
же принцип держит `train.py`: `valid` служит `early_stopping`, `test` меряет
качество один раз. Калибровка на `test` дала бы кривую надёжности, подогнанную
под ту же выборку, где она замеряется, и число «после» стало бы завышенным.

## Кривая надёжности

Корзины с равным взвешенным числом клеток здесь не годятся: панель прорежена
по отрицательным клеткам, и девять корзин из десяти проваливаются ниже
вероятности 0,02, где для решения о заявке нет разницы. Границы корзин взяты
с шага `LADDER` из `04_threshold.py`: они совпадают с порогом направления и со
ступенями цены ADR 0002, и в верхних корзинах остаётся достаточно веса, чтобы
судить о границе `CRITICAL`. В каждой корзине сравнивается средняя
вероятность со взвешенной долей событий. Вес тот же, что у `train.py`:
обратный доле прореживания отрицательных клеток панели, иначе доля событий в
корзине окажется раздутой примерно в 50 раз.

## Артефакт

Калибратор сохраняется в `out/calibration.joblib` через `joblib.dump`, рядом
с моделью. Применяется он после бустера: `calibrator.predict(booster.predict(x))`.

## Граница уровня `CRITICAL`

`04_threshold.py` назначил порог заявки (`HIGH`) и границу `MEDIUM`, но
оставил `CRITICAL` без числа: доля оправданных нарядов росла с сырой
вероятностью только до значения 0,08, а дальше стояла на месте.

Скрипт перебирает пороги выше `HIGH` на панели отложенной выборки (той же,
что даёт `train.py`, с теми же весами) и меряет взвешенную долю событий на
каждом. Перебор показывает не рост, а шум: доля событий держится в полосе
0,21–0,27 на всём перебору от 0,04 до 0,3, а вес самого строгого порога —
2 846 клеток против 24 342 у самого мягкого, то есть выборка редеет в
восемь раз без надёжного выигрыша. `CRITICAL` берётся как наименьший порог
перебора: поднимать его дальше значит платить более редкой и шумной тревогой
без калиброванного основания, что она вернее.
"""
import itertools
import json
import pathlib

import duckdb
import joblib
import lightgbm as lgb
import numpy as np
import train as train_module
from sklearn.isotonic import IsotonicRegression

OUT = pathlib.Path(__file__).resolve().parent / "out"
MODEL = OUT / "model.txt"
METRICS = OUT / "metrics.json"
CALIBRATION = OUT / "calibration.joblib"

# Границы корзин кривой надёжности. Совпадают со ступенями `LADDER`
# `04_threshold.py` и с порогом направления 0,035251.
BIN_EDGES = [0.0, 0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.030, 0.035251, 0.04, 0.06, 0.08, 0.15, 0.3, 0.5, 1.0]


def reliability(y: np.ndarray, w: np.ndarray, p: np.ndarray) -> list[dict[str, float]]:
    """Делит клетки по границам `BIN_EDGES` и считает в каждой корзине
    среднюю предсказанную вероятность и взвешенную долю событий. Пустая
    корзина в вывод не попадает."""
    points = []
    for lo, hi in itertools.pairwise(BIN_EDGES):
        mask = (p >= lo) & (p < hi if hi < 1.0 else p <= hi)
        if not np.any(mask):
            continue
        wb = w[mask]
        points.append(
            {
                "range": f"[{lo:g}, {hi:g}{']' if hi == 1.0 else ')'}",
                "mean_p": round(float(np.average(p[mask], weights=wb)), 6),
                "event_rate": round(float(np.average(y[mask], weights=wb)), 6),
                "weight": round(float(wb.sum()), 1),
            }
        )
    return points


def brier(y: np.ndarray, w: np.ndarray, p: np.ndarray) -> float:
    """Взвешенный счёт Брайера: средний квадрат ошибки вероятности."""
    return round(float(np.average((p - y) ** 2, weights=w)), 6)


# Пороги перебора границы `CRITICAL`, все выше порога `HIGH` 0,035251.
CRITICAL_CANDIDATES = [0.04, 0.05, 0.06, 0.08, 0.1, 0.15, 0.2, 0.3]


def pick_critical(
    y: np.ndarray, w: np.ndarray, p_raw: np.ndarray, p_cal: np.ndarray, high: float
) -> dict[str, object]:
    """Меряет взвешенную долю событий на каждом пороге `CRITICAL_CANDIDATES` и
    берёт наименьший: перебор не растёт с порогом, поэтому строгий порог не
    покупает уверенности, только редеющую выборку. Разбор чисел — в docstring
    модуля, раздел «Граница уровня CRITICAL».

    Перебор начинается строго выше границы `HIGH`. Это и было замыслом правила:
    когда его писали, `HIGH` равнялся 0,035251, и первый кандидат 0,04 стоял
    сразу над ним. Переобучение T08b подняло `HIGH` до 0,156089, а кандидат
    остался прежним, и шкала перестала возрастать: `CRITICAL` оказался ниже
    `HIGH` и стал недостижим. Отбор по `HIGH` держит замысел при любом
    переобучении.
    """
    above = [value for value in CRITICAL_CANDIDATES if value > high]
    if not above:
        raise ValueError(
            f"ни один кандидат CRITICAL не лежит выше границы HIGH {high:.6f}. "
            f"Кандидаты: {CRITICAL_CANDIDATES}. Расширьте список."
        )
    rows = []
    for threshold in above:
        mask = p_raw >= threshold
        wb = w[mask]
        n = float(wb.sum())
        rows.append(
            {
                "threshold": threshold,
                "weight": round(n, 1),
                "mean_p_raw": round(float(np.average(p_raw[mask], weights=wb)), 6) if n else 0.0,
                "mean_p_calibrated": round(float(np.average(p_cal[mask], weights=wb)), 6) if n else 0.0,
                "event_rate": round(float(np.average(y[mask], weights=wb)), 6) if n else 0.0,
            }
        )
    return {
        "rule": "наименьший порог перебора: доля событий не растёт с порогом",
        "candidates": rows,
        "chosen": rows[0],
    }


def main() -> None:
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    con.execute("PRAGMA memory_limit='6GB'")
    train_module.load_panel(con)

    valid = train_module.fetch_split(con, "valid", train_module.VALID_END)
    test = train_module.fetch_split(con, "test", train_module.panel_end(con))

    booster = lgb.Booster(model_file=str(MODEL))
    p_valid = booster.predict(valid["x"])
    p_test = booster.predict(test["x"])

    before = reliability(test["y"], test["w"], p_test)
    brier_before = brier(test["y"], test["w"], p_test)

    calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    calibrator.fit(p_valid, valid["y"], sample_weight=valid["w"])

    p_test_cal = calibrator.predict(p_test)
    after = reliability(test["y"], test["w"], p_test_cal)
    brier_after = brier(test["y"], test["w"], p_test_cal)

    # Калибратор едет в прод только тогда, когда он выигрывает на отложенной
    # выборке. Метод без выигрыша не ставится вовсе: он добавляет файл, шкалу и
    # ещё одно чтение на каждый прогноз, ничего не давая взамен.
    #
    # Изотония училась на проверочном отрезке с базой 1,2284 % и переносилась на
    # тест с базой 0,9438 %. Она принесла чужую частоту событий и стала завышать
    # вероятность почти во всех полосах: в полосе [0,03, 0,035251) доля событий
    # упала с 0,03558 до 0,01303 при почти том же предсказании.
    kept = brier_after < brier_before
    if kept:
        joblib.dump(calibrator, CALIBRATION)
    else:
        CALIBRATION.unlink(missing_ok=True)

    print(f"счёт Брайера: до {brier_before:.6f}, после {brier_after:.6f}")
    print("кривая надёжности до калибровки:")
    for point in before:
        print(f"  {point}")
    print("кривая надёжности после калибровки:")
    for point in after:
        print(f"  {point}")

    print(f"калибратор {'принят' if kept else 'отклонён: проигрыш по Брайеру'}")

    metrics = json.loads(METRICS.read_text(encoding="utf-8"))
    levels = metrics["decision"]["levels"]
    critical = pick_critical(test["y"], test["w"], p_test, p_test_cal, float(levels["HIGH"]))
    print(f"граница CRITICAL: {critical['chosen']}")

    metrics["calibration"] = {
        "method": "isotonic",
        "fit_on": "valid",
        "kept": kept,
        "artifact": CALIBRATION.name if kept else None,
        "brier_before": brier_before,
        "brier_after": brier_after,
        "reliability_before": before,
        "reliability_after": after,
    }
    levels["CRITICAL"] = critical["chosen"]["threshold"]
    metrics["decision"]["scale"] = "calibrated" if kept else "raw"
    metrics["decision"]["critical_search"] = critical
    metrics["decision"]["level_note"] = (
        "границы стоят на шкале, которую отдаёт predict: "
        + ("после калибровки" if kept else "сырой выход бустера")
    )

    # Шкала обязана возрастать. Немонотонный набор делает верхний уровень
    # недостижимым, и диспетчер не видит ни одного CRITICAL. Проверка стоит
    # здесь, а не только в бэкенде: файл замера читают и люди.
    order = ["MEDIUM", "HIGH", "CRITICAL"]
    values = [float(levels[name]) for name in order]
    if values != sorted(values) or len(set(values)) != len(values):
        raise ValueError(f"границы уровней не возрастают: {dict(zip(order, values))}")

    METRICS.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"замер в {METRICS}, шкала {metrics['decision']['scale']}")


if __name__ == "__main__":
    main()
