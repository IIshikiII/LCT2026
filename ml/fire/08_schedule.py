"""Сверяет график ТО АКМ и ДУ с журналом. ADR 0014.

Источник: `raw_task/dataset/График_ТО_АКМ_и_ДУ_на_2026г_РЭК_3_на_А4.xlsx`.
По каждому объекту график называет оборудование, его число и месяцы ТО.
Газоанализаторы проходят ТО раз в два или три месяца. Пожарной сигнализации
в графике нет: АКМ это контроль метана, ДУ это диспетчерское управление.

Объекты графика обезличены. Сопоставление идёт по числу газоанализаторов, как
в `03_ppr.py`: пара засчитывается, когда число встречается ровно один раз с
каждой стороны.

Проверка: у сопоставленного объекта доля рабочих суток с показанием газа от 1 %
в месяцы ТО выше, чем в остальные месяцы. Если да, то проверки газа идут по
расписанию объекта, и расписание можно восстановить из журнала за все годы.
Лист назван «2025 год», заголовок говорит о 2026 годе. Поэтому сверка идёт по
месяцам, а не по датам, на всём журнале с 2022 года.

Результат: `out/to_schedule.parquet`, `out/to_check.json`.
"""

import collections
import importlib
import json
import pathlib
import sys

import duckdb
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "access"))
ppr = importlib.import_module("03_ppr")

import calendar_ru

ROOT = HERE.parents[1]
XLSX = ROOT / "raw_task" / "dataset" / "График_ТО_АКМ_и_ДУ_на_2026г_РЭК_3_на_А4.xlsx"
EVENTS = ROOT / "eda" / "out" / "events.parquet"
CHANNELS = ROOT / "notebooks" / "out" / "channel_features.parquet"
OUT = HERE / "out"
RESULT = OUT / "to_schedule.parquet"
CHECK = OUT / "to_check.json"
MONTH_COLUMNS = "GHIJKLMNOPQR"


def schedule() -> pd.DataFrame:
    rows, current = [], None
    for _, cells in sorted(ppr.read_sheet(XLSX).items()):
        if cells.get("C", "").startswith("Объект") and cells.get("B", "").isdigit():
            current = int(cells["B"])
            continue
        if current is None or "Газоанализатор" not in cells.get("D", ""):
            continue
        months = [i + 1 for i, col in enumerate(MONTH_COLUMNS) if "ТО" in cells.get(col, "")]
        rows.append({"to_object": current, "n_gas": int(float(cells["E"])), "months": months})
    return pd.DataFrame(rows)


def main() -> None:
    con = duckdb.connect()
    cal = calendar_ru.build_calendar(con)
    plan = schedule()
    ours = con.execute(
        f"""SELECT oid AS object_id, count(*) AS n_gas FROM read_parquet('{CHANNELS.as_posix()}')
        WHERE stype = 'Газовый датчик' GROUP BY 1"""
    ).df()
    a, b = collections.Counter(plan["n_gas"]), collections.Counter(ours["n_gas"])
    unique = {n for n in a if a[n] == 1 and b.get(n) == 1}
    match = ours[ours["n_gas"].isin(unique)].set_index("n_gas")["object_id"]
    plan["object_id"] = plan["n_gas"].map(match).astype("Int64")
    plan.to_parquet(RESULT, index=False)
    print(f"объектов с газоанализаторами в графике {len(plan)}, сопоставлено "
          f"{int(plan['object_id'].notna().sum())}")

    checks = []
    for row in plan.dropna(subset=["object_id"]).itertuples():
        months = row.months or [0]
        share = con.execute(
            f"""
            WITH days AS (
                SELECT k.day, month(k.day) AS m FROM {cal} k
                WHERE k.is_day_off = 0 AND k.day BETWEEN DATE '2022-01-01' AND DATE '2026-06-30'
            ),
            hit AS (
                SELECT DISTINCT e.d AS day
                FROM read_parquet('{EVENTS.as_posix()}') e
                JOIN read_parquet('{CHANNELS.as_posix()}') ch ON ch.cid = e.channel_id
                WHERE ch.stype = 'Газовый датчик' AND ch.oid = {row.object_id}
                  AND e.d >= DATE '2022-01-01'
                  AND try_cast(replace(e.value, ',', '.') AS DOUBLE) BETWEEN 1 AND 100
            )
            SELECT
                avg((h.day IS NOT NULL)::INT) FILTER (WHERE d.m IN ({",".join(map(str, months))})),
                avg((h.day IS NOT NULL)::INT) FILTER (WHERE d.m NOT IN ({",".join(map(str, months))}))
            FROM days d LEFT JOIN hit h USING (day)
            """
        ).fetchone()
        item = {"to_object": row.to_object, "object_id": int(row.object_id),
                "to_months": row.months,
                "check_day_share_in_to_months": round(share[0] or 0, 4),
                "check_day_share_other_months": round(share[1] or 0, 4)}
        item["lift"] = (round(item["check_day_share_in_to_months"]
                              / item["check_day_share_other_months"], 2)
                        if item["check_day_share_other_months"] else None)
        checks.append(item)
        print(item)
    CHECK.write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
