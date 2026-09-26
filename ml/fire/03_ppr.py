"""Разбирает график ППР датчиков метана на 2026 год и сверяет его с журналом.
ADR 0014.

Источник: `raw_task/dataset/График ППР АКМ на 2026г. РЭК.xlsx`, лист «РЭК».
У каждого объекта графика есть число датчиков и четыре даты: начало
демонтажа, сдача в поверку, вывоз из поверки, сдача работ комиссии. Строка
без дат делит окно со строкой выше: так устроены объединённые ячейки листа.

Объекты графика обезличены («Объект N»). Сопоставление с объектами выгрузки
идёт по числу газовых каналов. Пара засчитывается, только когда число
встречается ровно один раз с каждой стороны. Это слабое сопоставление, поэтому
скрипт проверяет его журналом: датчик на поверке молчит, значит внутри окна
демонтажа у сопоставленного объекта газовых записей меньше обычного.

openpyxl в наборах нет (спецификация бэкенда §2), лист читается как zip с XML.

Результат: `out/ppr_2026.parquet`, `out/ppr_check.json`.
"""

import collections
import datetime as dt
import json
import pathlib
import re
import xml.etree.ElementTree as ET
import zipfile

import duckdb
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
XLSX = ROOT / "raw_task" / "dataset" / "График ППР АКМ на 2026г. РЭК.xlsx"
EVENTS = ROOT / "eda" / "out" / "events.parquet"
CHANNELS = ROOT / "notebooks" / "out" / "channel_features.parquet"
OUT = HERE / "out"
RESULT = OUT / "ppr_2026.parquet"
CHECK = OUT / "ppr_check.json"
JOURNAL_END = dt.date(2026, 6, 30)

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
EXCEL_EPOCH = dt.date(1899, 12, 30)


def read_sheet(path: pathlib.Path) -> dict[int, dict[str, str]]:
    """Строки первого листа: номер строки → буква колонки → значение."""
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall("m:si", NS):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
    root = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows: dict[int, dict[str, str]] = {}
    for row in root.findall(".//m:sheetData/m:row", NS):
        cells = {}
        for c in row.findall("m:c", NS):
            v = c.find("m:v", NS)
            if v is None or v.text is None:
                continue
            value = shared[int(v.text)] if c.get("t") == "s" else v.text
            cells[re.match(r"[A-Z]+", c.get("r")).group(0)] = value.strip()
        rows[int(row.get("r"))] = cells
    return rows


def serial(value: str | None) -> dt.date | None:
    if value is None or not value.replace(".", "").isdigit():
        return None
    return EXCEL_EPOCH + dt.timedelta(days=int(float(value)))


def ppr_rows() -> pd.DataFrame:
    items = []
    window = (None, None, None)
    for _, cells in sorted(read_sheet(XLSX).items()):
        name = cells.get("C", "")
        if not name.startswith("Объект"):
            continue
        start, back, done = serial(cells.get("E")), serial(cells.get("G")), serial(cells.get("H"))
        if start is not None:
            window = (start, back, done)
        items.append(
            {
                "ppr_object": int(name.split()[-1]),
                "n_sensors": int(cells["D"]),
                "dismantle_from": window[0],
                "back_from_check": window[1],
                "accepted": done or window[2],
            }
        )
    return pd.DataFrame(items)


def main() -> None:
    con = duckdb.connect()
    ppr = ppr_rows()
    ours = con.execute(
        f"""
        SELECT oid AS object_id, count(*) AS n_sensors
        FROM read_parquet('{CHANNELS.as_posix()}')
        WHERE stype = 'Газовый датчик' GROUP BY 1
        """
    ).df()
    ppr_count = collections.Counter(ppr["n_sensors"])
    our_count = collections.Counter(ours["n_sensors"])
    unique = {n for n in ppr_count if ppr_count[n] == 1 and our_count.get(n) == 1}
    match = ours[ours["n_sensors"].isin(unique)].set_index("n_sensors")["object_id"]
    ppr["object_id"] = ppr["n_sensors"].map(match).astype("Int64")
    ppr.to_parquet(RESULT, index=False)
    print(f"объектов в графике {len(ppr)}, датчиков {ppr['n_sensors'].sum()}")
    print(f"сопоставлено по числу датчиков: {int(ppr['object_id'].notna().sum())}")

    # Сверка журналом: объекты с окном внутри журнала. Мера это доля газовых
    # записей в сутки внутри окна «демонтаж — вывоз из поверки» к доле в 60 сутках
    # до окна.
    checks = []
    for row in ppr.dropna(subset=["object_id"]).itertuples():
        if row.dismantle_from is None or row.dismantle_from > JOURNAL_END:
            continue
        end = min(row.back_from_check or row.accepted, JOURNAL_END)
        before, inside, alarms_after = con.execute(
            f"""
            WITH g AS (
                SELECT ev.d, try_cast(replace(ev.value, ',', '.') AS DOUBLE) AS v
                FROM read_parquet('{EVENTS.as_posix()}') ev
                JOIN read_parquet('{CHANNELS.as_posix()}') ch ON ch.cid = ev.channel_id
                WHERE ch.stype = 'Газовый датчик' AND ch.oid = {row.object_id}
                  AND ev.d BETWEEN DATE '{row.dismantle_from}' - 60 AND DATE '{end}' + 14
            )
            SELECT
                count(*) FILTER (WHERE d < DATE '{row.dismantle_from}') / 60.0,
                count(*) FILTER (WHERE d BETWEEN DATE '{row.dismantle_from}' AND DATE '{end}')
                    / (DATE '{end}' - DATE '{row.dismantle_from}' + 1)::DOUBLE,
                count(*) FILTER (WHERE d > DATE '{end}' AND v >= 1)
            FROM g
            """
        ).fetchone()
        item = {
            "ppr_object": row.ppr_object,
            "object_id": int(row.object_id),
            "window": [str(row.dismantle_from), str(end)],
            "records_per_day_before": round(before, 1),
            "records_per_day_inside": round(inside, 1),
            "inside_to_before": round(inside / before, 3) if before else None,
            "gas_readings_from_1pct_14d_after": alarms_after,
        }
        checks.append(item)
        print(item)

    CHECK.write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
