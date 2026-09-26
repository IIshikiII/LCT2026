"""Меряет три версии метки направления «риск подтопления» и пишет замер в
`out/target_stats.json`.

Версии метки:

- A. «Затоплен» у канала «Состояние насоса»;
- Б. A и «Не замкнут» у канала «Датчик затопления»;
- В. Б и «Работают все насосы в АНС» у канала «Состояние насоса».

Для каждой версии скрипт считает базу и наивную планку на часовой и суточной
сетке (`ml/access/target_stats.py` без правок), долю событий по значению,
распределение по месяцам года и базу по отрезкам разбиения.

Версия В проходит отдельную проверку. «Работают все насосы» может означать
штатную откачку, а не подтопление. Поэтому скрипт меряет, как часто сутки с
этим значением совпадают с сутками события A или Б на том же объекте, и
сравнивает долю со случайной. Правило выбора записано до замера, в
`backend/docs/adr/0008-flood-target.md`."""

import json
import pathlib
import sys

import duckdb

ACCESS = pathlib.Path(__file__).resolve().parents[1] / "access"
sys.path.insert(0, str(ACCESS))

# Импорт идёт после правки пути: модуль лежит у соседнего направления.
import target_stats

OUT = pathlib.Path(__file__).resolve().parent / "out"
HOURLY = OUT / "flood_hourly.parquet"
UNITS = OUT / "flood_units.parquet"
STATS = OUT / "target_stats.json"

HORIZON_HOURS = 24
# Границы отрезков совпадают с суточной моделью доступа
# (`ml/access/08_train_daily.py`): так направления сравнимы между собой.
TRAIN_END = "2025-07-01"
VALID_END = "2026-01-01"

VERSIONS = {
    "A": ("Затоплен",),
    "B": ("Затоплен", "Не замкнут"),
    "V": ("Затоплен", "Не замкнут", "Работают все насосы в АНС"),
}
ALL_PUMPS = "Работают все насосы в АНС"

con = duckdb.connect()
con.execute("PRAGMA threads=8")
con.execute("PRAGMA memory_limit='6GB'")
target_stats.attach(con, HOURLY, "raw")
target_stats.attach(con, UNITS, "unit")


def version_view(name: str, values: tuple[str, ...]) -> str:
    """Собирает представление часов с тревогой одной версии метки."""
    view = f"alarm_{name}"
    in_list = ", ".join(f"'{v}'" for v in values)
    con.execute(
        f"""
        CREATE OR REPLACE VIEW {view} AS
        SELECT object_id, gallery, picket, hour,
               sum(n_alarms) AS n_alarms,
               sum(n_moments) AS n_moments
        FROM raw WHERE value IN ({in_list})
        GROUP BY 1, 2, 3, 4
        """
    )
    return view


def by_value(values: tuple[str, ...]) -> dict:
    in_list = ", ".join(f"'{v}'" for v in values)
    rows = con.execute(
        f"""
        SELECT value, count(DISTINCT (object_id, gallery, picket, hour::DATE)),
               count(DISTINCT object_id)
        FROM raw WHERE value IN ({in_list}) GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    total = sum(r[1] for r in rows)
    return {
        v: {"unit_days": n, "objects": o, "share_pct": round(100.0 * n / total, 1)}
        for v, n, o in rows
    }


def by_month(view: str) -> dict:
    rows = con.execute(
        f"""
        SELECT month(hour) AS m, count(DISTINCT (object_id, gallery, picket, hour::DATE))
        FROM {view} GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    total = sum(r[1] for r in rows)
    return {str(m): round(100.0 * n / total, 1) for m, n in rows}


def top_object(view: str) -> dict:
    """Объект с наибольшей долей положительных единице-суток версии."""
    oid, n, total = con.execute(
        f"""
        WITH d AS (SELECT DISTINCT object_id, gallery, picket, hour::DATE AS day
                   FROM {view})
        SELECT object_id, count(*) AS n, (SELECT count(*) FROM d)
        FROM d GROUP BY 1 ORDER BY n DESC, object_id LIMIT 1
        """
    ).fetchone()
    return {"object_id": oid, "unit_days": n, "share_pct": round(100.0 * n / total, 1)}


def by_split(view: str) -> dict:
    """База по суткам на трёх отрезках разбиения."""
    result = {}
    bounds = {
        "train": ("1900-01-01", TRAIN_END),
        "valid": (TRAIN_END, VALID_END),
        "test": (VALID_END, "2100-01-01"),
    }
    for split, (lo, hi) in bounds.items():
        cells, positive = con.execute(
            f"""
            SELECT
                (SELECT sum(greatest(0, date_diff('day',
                        greatest(hour_from::DATE, DATE '{lo}'),
                        least(hour_to::DATE, DATE '{hi}' - 1)) + 1)) FROM unit),
                (SELECT count(DISTINCT (object_id, gallery, picket, hour::DATE))
                 FROM {view} WHERE hour >= DATE '{lo}' AND hour < DATE '{hi}')
            """
        ).fetchone()
        result[split] = {
            "unit_days": int(cells),
            "positive_days": int(positive),
            "base_pct": round(100.0 * positive / cells, 3) if cells else None,
        }
    return result


def all_pumps_check() -> dict:
    """Совпадает ли «Работают все насосы» с событием A или Б на том же объекте.

    Единица счёта — сутки объекта. Сутки «покрыты», если на объекте в эти, в
    предыдущие или в следующие сутки было событие A или Б. Наблюдаемая доля
    считается по суткам с «Работают все насосы». Случайная доля считается по
    всем суткам жизни тех же объектов. Лифт — их отношение.
    """
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE ab_day AS
        SELECT DISTINCT object_id, hour::DATE AS day FROM raw
        WHERE value IN ('Затоплен', 'Не замкнут')
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE covered AS
        SELECT DISTINCT object_id, day + s.i::INTEGER AS day
        FROM ab_day, generate_series(-1, 1) s(i)
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE pumps_day AS
        SELECT DISTINCT object_id, hour::DATE AS day FROM raw WHERE value = '{ALL_PUMPS}'
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE obj_life AS
        SELECT DISTINCT u.object_id, d.day
        FROM unit u,
             LATERAL (SELECT unnest(generate_series(u.hour_from::DATE, u.hour_to::DATE,
                                                    INTERVAL 1 DAY))::DATE AS day) d
        WHERE u.object_id IN (SELECT object_id FROM pumps_day)
        """
    )
    obs_n, obs_hit, exp_n, exp_hit, objects = con.execute(
        """
        SELECT
            (SELECT count(*) FROM pumps_day),
            (SELECT count(*) FROM pumps_day p JOIN covered c USING (object_id, day)),
            (SELECT count(*) FROM obj_life),
            (SELECT count(*) FROM obj_life l JOIN covered c USING (object_id, day)),
            (SELECT count(DISTINCT object_id) FROM pumps_day)
        """
    ).fetchone()
    per_object = con.execute(
        """
        SELECT p.object_id, count(*) AS days,
               count(c.day) AS hit
        FROM pumps_day p LEFT JOIN covered c USING (object_id, day)
        GROUP BY 1 ORDER BY days DESC
        """
    ).fetchall()
    observed = obs_hit / obs_n
    expected = exp_hit / exp_n
    return {
        "objects": objects,
        "pump_days": obs_n,
        "pump_days_with_ab": obs_hit,
        "observed_pct": round(100.0 * observed, 2),
        "expected_pct": round(100.0 * expected, 2),
        "lift": round(observed / expected, 2) if expected else None,
        "per_object": [
            {"object_id": o, "days": d, "with_ab_pct": round(100.0 * h / d, 1)}
            for o, d, h in per_object
        ],
    }


result = {
    "direction": "FLOOD_RISK",
    "horizon_hours": HORIZON_HOURS,
    "units": con.execute("SELECT count(*) FROM unit").fetchone()[0],
    "channels": int(con.execute("SELECT sum(n_channels) FROM unit").fetchone()[0]),
    "train_end": TRAIN_END,
    "valid_end": VALID_END,
    "versions": {},
}

for name, values in VERSIONS.items():
    view = version_view(name, values)
    result["versions"][name] = {
        "values": list(values),
        "units_with_event": con.execute(
            f"SELECT count(DISTINCT (object_id, gallery, picket)) FROM {view}"
        ).fetchone()[0],
        "objects_with_event": con.execute(
            f"SELECT count(DISTINCT object_id) FROM {view}"
        ).fetchone()[0],
        "hourly": target_stats.hourly_stats(con, HORIZON_HOURS, alarm=view),
        "daily": target_stats.daily_stats(con, alarm=view),
        "by_value": by_value(values),
        "by_month_pct": by_month(view),
        "top_object": top_object(view),
        "by_split": by_split(view),
    }

result["all_pumps_check"] = all_pumps_check()

STATS.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"единиц: {result['units']}, каналов: {result['channels']}")
for name, v in result["versions"].items():
    print(
        f"версия {name}: единиц с событием {v['units_with_event']}, "
        f"объектов {v['objects_with_event']}"
    )
    print("  " + target_stats.line(v["hourly"]))
    print("  " + target_stats.line(v["daily"]))
    print(f"  по значениям: {v['by_value']}")
    print(f"  по отрезкам: {v['by_split']}")
    print(f"  крупнейший объект: {v['top_object']}")
    print(f"  по месяцам, %: {v['by_month_pct']}")
check = result["all_pumps_check"]
print(
    f"«Работают все насосы» рядом с A или Б: {check['observed_pct']} % суток "
    f"против {check['expected_pct']} % случайно, лифт {check['lift']}"
)
print(f"замер записан в {STATS}")
