"""Назначает порог направления «несанкционированный доступ» из цены ошибки для
диспетчера. Читает модель `out/model.txt` и замер `out/metrics.json`, дописывает
в замер раздел `decision`.

Скрипт запускается после `train.py`: он берёт границы отложенной выборки из
`metrics.json` и пишет в тот же файл.

## Почему считаем на полной сетке, а не на панели

`train.py` меряет модель на прореженной панели и возвращает полную картину
весом отрицательной клетки. Для точности и полноты этого хватает. Для цены
ошибки не хватает: диспетчер платит за наряд, а не за клетку «единица и час».
Наряд на объект держится часами, и веса не скажут, сколько часов подряд одна и
та же единица висела над порогом.

Поэтому скрипт строит полную сетку отложенной выборки, 4,8 млн клеток, считает
на ней признаки заново и прогоняет через сохранённую модель. Числа выходят
прямым счётом, без весов. Совпадение с взвешенным замером `train.py` служит
проверкой схемы весов.

## Что считается ценой ошибки

Три величины, все на отложенной выборке.

1. **Нарядов в сутки.** Скрипт повторяет правило `07-auto-orders.md`: заявка
   создаётся на объект и направление, пока на том же объекте нет открытой
   заявки. Заявка занимает объект на 24 часа, то есть на горизонт прогноза:
   раньше этого срока событие, ради которого её создали, ещё не наступило.
   Клетки выше порога, попавшие в это окно, новой заявки не порождают.
2. **Доля оправданных нарядов.** Наряд оправдан, когда на пикете, ради которого
   его создали, случилась тревога доступа в горизонте 24 часа. Рядом считается
   мягкая версия: событие случилось где-нибудь на том же объекте, куда и поедет
   бригада.
3. **Доля предупреждённых проникновений.** Проникновение это не час, а случай:
   тревоги одной единицы, отстоящие друг от друга меньше чем на 24 часа,
   считаются одним проникновением. Проникновение предупреждено, когда в
   предшествующие 24 часа на той же единице был прогноз выше порога.

## Правило выбора порога

Наряд окупается, когда ожидаемая польза больше цены визита:

    p × L × q ≥ C,   то есть   p ≥ C / (L × q) = 1 / R

Здесь `C` это цена визита бригады, `L` цена проникновения, `q` доля
проникновений, которые визит предотвращает, а `R` это число напрасных визитов,
которое заказчик оплатит ради одного предотвращённого проникновения.

Чисел `C` и `L` заказчик не дал и на QA-сессии не обещал. Поэтому правило
применяется наоборот: цена ошибки замеряется, а порог назначается так, чтобы она
не выросла против сегодняшней. Сегодняшний день это наивное правило «тревога
доступа была в прошлые сутки»: оно повторяет работу диспетчера по журналу тревог
и служит планкой направления в ADR 0001.

**Порог равен тому значению, при котором оба счёта цены не хуже сегодняшних:
нарядов в сутки не больше и нарядов на одно предупреждённое проникновение не
больше.** Первый счёт связывает выбор, поэтому скрипт ищет делением отрезка тот
порог, где поток нарядов модели равен потоку наивного правила. Диспетчер
получает тот же день, а сервис предупреждает больше проникновений.

Администратор правит порог, когда цена ошибки у него иная. Таблица `ladder` в
`decision` показывает цену каждой ступени, в том числе предельную: сколько лишних
нарядов стоит одно дополнительное предупреждённое проникновение.

## Уровни здесь не назначаются, кроме границы заявки

Порог отвечает за заявку, то есть за границу уровня `HIGH`: заявку порождают
`HIGH` и `CRITICAL` (`backend/app/meta/directions.py`). Граница `MEDIUM` равна
базовой ставке направления: единица опаснее средней видна диспетчеру, но бригаду
не поднимает.

Граница `CRITICAL` не назначается. Причина в замере `mean_p`: доля оправданных
нарядов не равна вероятности модели ни в одной полосе, и выше 0,15 она перестаёт
расти вовсе. Полосу уровней надо ставить на калиброванной вероятности, а не на
сыром выходе LightGBM.
"""
import itertools
import json
import math
import pathlib
import time

import duckdb
import features
import lightgbm as lgb
import numpy as np

OUT = pathlib.Path(__file__).resolve().parent / "out"
MODEL = OUT / "model.txt"
METRICS = OUT / "metrics.json"

# Часы, которые одна заявка держит объект. Равны горизонту прогноза: раньше
# этого срока событие, ради которого заявка создана, ещё не наступило.
ORDER_HOURS = features.HORIZON_HOURS
# Шагов деления отрезка при поиске порога равного потока нарядов.
BISECTION_STEPS = 40
# Ступени таблицы цены. Они показывают, как растёт предельная цена случая, и
# дают администратору выбор с числами.
LADDER = [0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.03, 0.04, 0.06, 0.08, 0.15, 0.3, 0.5]

HOUR = np.timedelta64(1, "h")
EPOCH = np.datetime64("1970-01-01T00:00:00")
# Множитель ключа «объект и час». Больше, чем номер часа за всю выгрузку.
KEY_SCALE = 10**7


def materialize(con: duckdb.DuckDBPyConnection, names: tuple[str, ...]) -> None:
    """Превращает представления над parquet в таблицы в памяти. Сетка режется на
    куски, и каждый кусок иначе перечитывал бы одни и те же файлы."""
    for name in names:
        con.execute(f"CREATE OR REPLACE TABLE {name}_mat AS SELECT * FROM {name}")
        con.execute(f"DROP VIEW {name}")
        con.execute(f"ALTER TABLE {name}_mat RENAME TO {name}")


def build_grid(con: duckdb.DuckDBPyConnection, t_from: str, t_to: str) -> int:
    """Строит полную сетку «единица и час» на отрезке `[t_from, t_to]` и
    нумерует единицы. Возвращает число клеток."""
    con.execute(
        """
        CREATE OR REPLACE TABLE unit_ix AS
        SELECT
            object_id, gallery, picket,
            row_number() OVER (ORDER BY object_id, gallery, picket) - 1 AS unit_id,
            dense_rank() OVER (ORDER BY object_id) - 1 AS object_ix
        FROM unit
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE grid AS
        SELECT
            x.unit_id,
            x.object_ix,
            u.object_id, u.gallery, u.picket,
            unnest(generate_series(
                greatest(u.hour_from, TIMESTAMP '{t_from}'),
                least(u.hour_to, TIMESTAMP '{t_to}'),
                INTERVAL 1 HOUR
            )) AS at
        FROM unit u
        JOIN unit_ix x USING (object_id, gallery, picket)
        WHERE u.hour_to >= TIMESTAMP '{t_from}' AND u.hour_from <= TIMESTAMP '{t_to}'
        """
    )
    return int(con.execute("SELECT count(*) FROM grid").fetchone()[0])


def score_grid(
    con: duckdb.DuckDBPyConnection, booster: lgb.Booster, chunks: list[tuple[str, str]]
) -> dict[str, np.ndarray]:
    """Считает признаки, метку и наивное правило на сетке и прогоняет их через
    модель. Сетка режется на куски по месяцам: признаки одного куска держат в
    памяти таблицу на несколько сотен мегабайт."""
    cols = ", ".join(f'f."{c}"' for c in features.FEATURE_COLUMNS)
    parts: list[dict[str, np.ndarray]] = []
    for lo, hi in chunks:
        t = time.time()
        con.execute(
            f"""
            CREATE OR REPLACE TABLE points_chunk AS
            SELECT * FROM grid
            WHERE "at" >= TIMESTAMP '{lo}' AND "at" < TIMESTAMP '{hi}'
            """
        )
        features.build_features(con, points="points_chunk")
        frame = con.execute(
            f"""
            SELECT
                g.unit_id, g.object_ix, f."at", {cols},
                CASE WHEN EXISTS (
                    SELECT 1 FROM armed a
                    WHERE (a.object_id, a.gallery, a.picket)
                        = (f.object_id, f.gallery, f.picket)
                      AND a.hour > f."at"
                      AND a.hour <= f."at" + INTERVAL {features.HORIZON_HOURS} HOUR
                ) THEN 1 ELSE 0 END AS label,
                CASE WHEN EXISTS (
                    SELECT 1 FROM armed a
                    WHERE (a.object_id, a.gallery, a.picket)
                        = (f.object_id, f.gallery, f.picket)
                      AND a.hour > f."at" - INTERVAL {features.HORIZON_HOURS} HOUR
                      AND a.hour <= f."at"
                ) THEN 1 ELSE 0 END AS naive
            FROM features f
            JOIN points_chunk g USING (object_id, gallery, picket, "at")
            ORDER BY g.unit_id, f."at"
            """
        ).df()
        prediction = booster.predict(
            frame[features.FEATURE_COLUMNS].to_numpy(dtype=np.float32)
        )
        parts.append(
            {
                "unit_id": frame["unit_id"].to_numpy(dtype=np.int32),
                "object_ix": frame["object_ix"].to_numpy(dtype=np.int32),
                "at": (frame["at"].to_numpy() - EPOCH) // HOUR,
                "p": np.asarray(prediction, dtype=np.float64),
                "label": frame["label"].to_numpy(dtype=np.int8),
                "naive": frame["naive"].to_numpy(dtype=np.int8),
            }
        )
        print(f"  {lo}: {len(frame)} клеток за {time.time() - t:.0f} c")
    cells = {key: np.concatenate([part[key] for part in parts]) for key in parts[0]}
    order = np.lexsort((cells["at"], cells["unit_id"]))
    return {key: value[order] for key, value in cells.items()}


def unit_offsets(unit_id: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Возвращает начало каждой единицы в сетке и её длину. Сетка отсортирована
    по единице и часу, поэтому единица занимает непрерывный отрезок."""
    starts = np.flatnonzero(np.diff(unit_id, prepend=unit_id[0] - 1))
    lengths = np.diff(np.append(starts, len(unit_id)))
    return starts, lengths


def episodes(unit_id: np.ndarray, hour: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Схлопывает часы тревоги в проникновения. Тревоги одной единицы,
    отстоящие меньше чем на `ORDER_HOURS`, это один случай."""
    order = np.lexsort((hour, unit_id))
    unit_id, hour = unit_id[order], hour[order]
    new = np.ones(len(hour), dtype=bool)
    new[1:] = (unit_id[1:] != unit_id[:-1]) | (np.diff(hour) >= ORDER_HOURS)
    return unit_id[new], hour[new]


def orders_at(
    object_ix: np.ndarray, hour: np.ndarray, flag: np.ndarray
) -> np.ndarray:
    """Возвращает клетки, породившие наряд, по правилу `07-auto-orders.md`:
    заявка создаётся, когда на объекте нет открытой заявки.

    Клетки отсортированы по единице, а не по объекту, поэтому тревоги сначала
    пересортируются по объекту и часу. Внутри объекта работает жадный обход:
    первая тревога открывает заявку, следующие `ORDER_HOURS` часов пропускаются.
    """
    index = np.flatnonzero(flag)
    if len(index) == 0:
        return np.array([], dtype=np.int64)
    obj, at = object_ix[index], hour[index]
    order = np.lexsort((at, obj))
    index, obj, at = index[order], obj[order], at[order]
    bounds = np.append(np.flatnonzero(np.diff(obj, prepend=obj[0] - 1)), len(obj))
    starts: list[int] = []
    for k in range(len(bounds) - 1):
        lo, hi = int(bounds[k]), int(bounds[k + 1])
        hours = at[lo:hi]
        i = 0
        while i < hi - lo:
            starts.append(lo + i)
            i += int(np.searchsorted(hours[i:], hours[i] + ORDER_HOURS, side="left"))
    return index[np.array(starts, dtype=np.int64)]


def covered(
    starts: np.ndarray,
    lengths: np.ndarray,
    unit_hour0: np.ndarray,
    ep_unit: np.ndarray,
    ep_hour: np.ndarray,
    flag: np.ndarray,
) -> int:
    """Считает проникновения, перед которыми на той же единице был прогноз выше
    порога. Окно предупреждения это `ORDER_HOURS` часов до случая."""
    cumulative = np.concatenate(([0], np.cumsum(flag)))
    base = starts[ep_unit] - unit_hour0[ep_unit]
    hi = np.clip(base + ep_hour, starts[ep_unit], starts[ep_unit] + lengths[ep_unit])
    lo = np.clip(
        base + ep_hour - ORDER_HOURS, starts[ep_unit], starts[ep_unit] + lengths[ep_unit]
    )
    return int(np.count_nonzero(cumulative[hi] - cumulative[lo] > 0))


def main() -> None:
    metrics = json.loads(METRICS.read_text(encoding="utf-8"))
    window = metrics["splits"]["test"]
    t_from, t_to = window["from"], window["to"]

    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    con.execute("PRAGMA memory_limit='6GB'")
    features.attach_sources(con)
    t = time.time()
    features.build_sequence_chain(con)
    materialize(con, ("alarm", "unit", "armed", "disarm_window", "access_chain"))
    print(f"источники в памяти за {time.time() - t:.0f} c")

    n_cells = build_grid(con, t_from, t_to)
    print(f"сетка: {n_cells} клеток, ожидалось {window['cells']}")

    edges = (
        con.execute(
            """
            SELECT DISTINCT date_trunc('month', "at") AS m
            FROM grid ORDER BY m
            """
        )
        .df()["m"]
        .tolist()
    )
    chunks = [
        (str(edges[i]), str(edges[i + 1]) if i + 1 < len(edges) else "2100-01-01")
        for i in range(len(edges))
    ]
    booster = lgb.Booster(model_file=str(MODEL))
    cells = score_grid(con, booster, chunks)

    starts, lengths = unit_offsets(cells["unit_id"])
    unit_hour0 = cells["at"][starts]
    alarm = con.execute(
        f"""
        SELECT x.unit_id, a.hour
        FROM armed a JOIN unit_ix x USING (object_id, gallery, picket)
        WHERE a.hour > TIMESTAMP '{t_from}' + INTERVAL {ORDER_HOURS} HOUR
          AND a.hour <= TIMESTAMP '{t_to}'
        ORDER BY x.unit_id, a.hour
        """
    ).df()
    ep_unit, ep_hour = episodes(
        alarm["unit_id"].to_numpy(dtype=np.int32),
        (alarm["hour"].to_numpy() - EPOCH) // HOUR,
    )
    # Единицы вне сетки отложенной выборки в счёт не идут.
    keep = np.isin(ep_unit, cells["unit_id"][starts])
    ep_unit, ep_hour = ep_unit[keep], ep_hour[keep]
    # Номер единицы служит индексом массивов `starts` и `lengths`, поэтому
    # нумерация сетки переводится в позицию.
    position = np.full(int(cells["unit_id"].max()) + 1, -1, dtype=np.int64)
    position[cells["unit_id"][starts]] = np.arange(len(starts))
    ep_position = position[ep_unit]

    # Тревоги объекта: по ним считается мягкая версия оправданного наряда.
    # Бригада едет на объект, поэтому событие на соседнем пикете того же объекта
    # визит тоже оправдывает.
    object_alarm = con.execute(
        f"""
        SELECT DISTINCT x.object_ix, a.hour
        FROM armed a JOIN unit_ix x USING (object_id, gallery, picket)
        WHERE a.hour > TIMESTAMP '{t_from}'
          AND a.hour <= TIMESTAMP '{t_to}' + INTERVAL {ORDER_HOURS} HOUR
        ORDER BY 1, 2
        """
    ).df()
    # Ключ «объект и час» кладёт часы одного объекта подряд, и поиск по нему
    # находит окно наряда одним вызовом на все наряды сразу.
    object_key = (
        object_alarm["object_ix"].to_numpy(dtype=np.int64) * KEY_SCALE
        + (object_alarm["hour"].to_numpy() - EPOCH) // HOUR
    )

    days = (int(cells["at"].max()) - int(cells["at"].min()) + 1) / 24.0
    label = cells["label"].astype(bool)
    n_events = len(ep_hour)

    def measure(flag: np.ndarray) -> dict[str, float]:
        """Меряет один набор тревог: клетки, наряды и предупреждённые случаи."""
        index = orders_at(cells["object_ix"], cells["at"], flag)
        n_orders = len(index)
        justified = int(np.count_nonzero(label[index])) if n_orders else 0
        key = cells["object_ix"][index].astype(np.int64) * KEY_SCALE + cells["at"][index]
        wide = int(
            np.count_nonzero(
                np.searchsorted(object_key, key + ORDER_HOURS, side="right")
                - np.searchsorted(object_key, key, side="right")
            )
        ) if n_orders else 0
        caught = covered(starts, lengths, unit_hour0, ep_position, ep_hour, flag)
        n_flag = int(np.count_nonzero(flag))
        true_positive = int(np.count_nonzero(flag & label))
        return {
            "cells": n_flag,
            "cell_precision": round(true_positive / n_flag, 6) if n_flag else 0.0,
            "cell_recall": round(true_positive / int(label.sum()), 6),
            "mean_p": round(float(cells["p"][flag].mean()), 6) if n_flag else 0.0,
            "orders": n_orders,
            "orders_per_day": round(n_orders / days, 2),
            "justified_orders": justified,
            "justified_share": round(justified / n_orders, 6) if n_orders else 0.0,
            "justified_object_orders": wide,
            "justified_object_share": round(wide / n_orders, 6) if n_orders else 0.0,
            "events_caught": caught,
            "event_recall": round(caught / n_events, 6) if n_events else 0.0,
            "visits_per_catch": round(n_orders / caught, 2) if caught else None,
        }

    naive = measure(cells["naive"].astype(bool))
    print(f"наивное правило: {naive}")

    # Порог равного потока нарядов. Поток падает с ростом порога, поэтому
    # деление отрезка сходится.
    lo, hi = 0.0, 1.0
    for _ in range(BISECTION_STEPS):
        mid = (lo + hi) / 2
        n_orders = len(orders_at(cells["object_ix"], cells["at"], cells["p"] >= mid))
        if n_orders > naive["orders"]:
            lo = mid
        else:
            hi = mid
    # Порог округляется вверх: округление вниз опустило бы его под найденную
    # границу и вернуло бы лишние наряды.
    threshold = math.ceil(hi * 1e6) / 1e6
    chosen = measure(cells["p"] >= threshold)
    print(f"порог {threshold}: {chosen}")

    base = float(label.mean())
    ladder = {f"{value:g}": measure(cells["p"] >= value) for value in LADDER}
    # Предельная цена ступени: сколько лишних нарядов стоит одно дополнительное
    # предупреждённое проникновение при переходе на ступень ниже.
    rungs = [ladder[key] for key in sorted(ladder, key=float, reverse=True)]
    for upper, lower in itertools.pairwise(rungs):
        gain = lower["events_caught"] - upper["events_caught"]
        lower["marginal_visits_per_catch"] = (
            round((lower["orders"] - upper["orders"]) / gain, 2) if gain else None
        )

    metrics["decision"] = {
        "rule": (
            "порог держит цену ошибки на сегодняшнем уровне: нарядов в сутки не "
            "больше, чем даёт наивное правило, и нарядов на одно предупреждённое "
            "проникновение не больше"
        ),
        "threshold": threshold,
        "levels": {"MEDIUM": round(base, 6), "HIGH": threshold},
        "level_note": "граница CRITICAL ждёт калибровки вероятности",
        "order_hours": ORDER_HOURS,
        "grid": {
            "cells": n_cells,
            "days": round(days, 1),
            "units": len(starts),
            "base": round(base, 6),
            "events": n_events,
        },
        "naive": naive,
        "chosen": chosen,
        "ladder": ladder,
    }
    METRICS.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"решение записано в {METRICS}")


if __name__ == "__main__":
    main()
