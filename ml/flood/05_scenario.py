"""Проверяет сценарий заказчика: предшествует ли беда насоса подтоплению.

Заказчик на QA-сессии: исправный насос не мигает, а течь у одной
станции из-за разуклонки подтапливает соседний участок. Скрипт отвечает числом
на вопрос: как часто событие метки приходит в ближайшие 24 часа после того,
как насос мигал или был недоступен, на той же единице и у соседей по объекту.

Лифт это доля часов с событием впереди среди часов с признаком, делённая на
базу. Замер идёт дважды: по всем часам и только по часам без события в прошлые
сутки. Второй замер отделяет предвестие от простого продолжения уже идущего
затопления.

Отложенный год не читается: замер идёт по часам до 2025-07-01. Метка ADR 0010.

Выход: `out/scenario.json`."""

import json
import pathlib

import duckdb

OUT = pathlib.Path(__file__).resolve().parent / "out"
PANEL = OUT / "panel_hourly.parquet"
RESULT = OUT / "scenario.json"
VALID_END = "2025-07-01"

SIGNALS = {
    "насос единицы мигал": "pump_blink_hours_24h > 0",
    "насос единицы недоступен": "pump_unavailable_24h > 0",
    "все насосы станции работали": "pump_all_running_24h > 0",
    "насос соседа по объекту мигал": "obj_pump_blink_24h > 0",
    "насос соседа по объекту недоступен": "obj_pump_unavailable_24h > 0",
    "все насосы у соседа по объекту": "obj_pump_all_running_24h > 0",
    "насос в соседнем объекте комплекса мигал": "cx_pump_blink_24h > 0",
    "насос в соседнем объекте комплекса недоступен": "cx_pump_unavailable_24h > 0",
}

con = duckdb.connect()
con.execute("PRAGMA threads=8")
con.execute(
    f"CREATE VIEW p AS SELECT * FROM read_parquet('{PANEL.as_posix()}') "
    f"WHERE hour < TIMESTAMP '{VALID_END}'"
)

result = {"period_end": VALID_END, "window_hours": 24, "subsets": {}}
for subset, where in (
    ("все часы", "TRUE"),
    ("без события в прошлые сутки", "naive = 0"),
):
    base = con.execute(f"SELECT avg(label), count(*) FROM p WHERE {where}").fetchone()
    rows = {"base_pct": round(100.0 * base[0], 3), "rows": base[1], "signals": {}}
    for name, condition in SIGNALS.items():
        n, share, events = con.execute(
            f"SELECT count(*), avg(label), sum(label) FROM p WHERE {where} AND {condition}"
        ).fetchone()
        rows["signals"][name] = {
            "rows": n,
            "events_ahead": int(events or 0),
            "event_ahead_pct": round(100.0 * share, 3) if n else None,
            "lift": round(share / base[0], 2) if n else None,
        }
    result["subsets"][subset] = rows

RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
for subset, rows in result["subsets"].items():
    print(f"\n{subset}: база {rows['base_pct']} %, часов {rows['rows']}")
    for name, s in rows["signals"].items():
        print(
            f"  {name:48} часов {s['rows']:8}  событие впереди {s['event_ahead_pct']} %"
            f"  лифт {s['lift']}"
        )
