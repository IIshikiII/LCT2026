"""Период охлаждения нового датчика и нового объекта. ADR 0016.

Новый датчик шумит: пусконаладка даёт серии тревог, которые не пожар
(объект 5962 «Гамма ПС», ответ заказчика). Правила пожара не поднимают тревогу
по датчику, пока идёт его период охлаждения. Длина периода выбирается по
данным, правило выбора записано до замера.

1. Возраст канала считается от первой записи канала в журнале. Каналы с первой
   записью до 2019-02-01 исключаются: журнал начинается 2019-01-01, их
   настоящий возраст неизвестен.
2. Частота это события вне пачки (ADR 0014) на канало-сутки в возрастной
   корзине: 0–7, 8–14, 15–30, 31–60, 61–90, 91–180, 181–365 суток и старше.
   Экспозиция это сутки жизни канала в корзине до его последней записи.
3. Зрелая частота это корзина старше года.
4. Период охлаждения это начало первой корзины, с которой нижняя граница 95 %
   интервала Пуассона каждой следующей корзины не выше 1,5 зрелой частоты.
5. То же для объекта: возраст объекта от первой записи любого его пожарного
   канала, частота на пикето-сутки.

Объект 5343 за 2020–2021 годы исключён, как везде.

Выход: `out/burn_in.json`.
"""

import json
import pathlib

import duckdb
from scipy.stats import chi2

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EVENTS = ROOT / "eda" / "out" / "events.parquet"
CHANNELS = ROOT / "notebooks" / "out" / "channel_features.parquet"
ALARMS = HERE / "out" / "fire_alarms.parquet"
RESULT = HERE / "out" / "burn_in.json"
JOURNAL_END = "2026-06-30"
CENSOR = "2019-02-01"
BUCKETS = [(0, 7), (8, 14), (15, 30), (31, 60), (61, 90), (91, 180), (181, 365), (366, 100000)]
RATIO = 1.5
BURST, BURST_MINUTES = 2, 60


def poisson_ci(k: int, exposure: float) -> tuple[float, float]:
    lo = chi2.ppf(0.025, 2 * k) / 2 if k > 0 else 0.0
    hi = chi2.ppf(0.975, 2 * k + 2) / 2
    return lo / exposure, hi / exposure


def curve(con, level: str) -> dict:
    unit = "channel_id" if level == "channel" else "object_id"
    rows = con.execute(
        f"""
        WITH life AS (SELECT {unit} AS u, min(first_day) AS born, max(last_day) AS dead
                      FROM chan GROUP BY 1 HAVING min(first_day) >= DATE '{CENSOR}'),
        b AS (SELECT * FROM (VALUES {", ".join(f"({lo}, {hi})" for lo, hi in BUCKETS)}) t(lo, hi)),
        expo AS (
            SELECT b.lo, b.hi,
                sum(greatest(0, least(b.hi, life.dead - life.born) - b.lo + 1)
                    * (SELECT count(*) FROM chan c WHERE c.{unit} = life.u)
                    ) AS exposure
            FROM life, b GROUP BY ALL
        ),
        ev AS (
            SELECT b.lo, count(*) AS k
            FROM events e JOIN life ON life.u = e.{unit}
            JOIN b ON e.day - life.born BETWEEN b.lo AND b.hi
            GROUP BY ALL
        )
        SELECT expo.lo, expo.hi, coalesce(ev.k, 0), expo.exposure,
            (SELECT count(*) FROM life)
        FROM expo LEFT JOIN ev USING (lo) ORDER BY expo.lo
        """
    ).fetchall()
    table = []
    for lo, hi, k, exposure, n in rows:
        ci = poisson_ci(int(k), float(exposure))
        table.append({"from": lo, "to": hi, "events": int(k), "exposure": float(exposure),
                      "rate": k / exposure, "ci_low": ci[0], "ci_high": ci[1]})
    mature = table[-1]["rate"]
    burn_in = None
    for i, row in enumerate(table[:-1]):
        if all(r["ci_low"] <= RATIO * mature for r in table[i:-1]):
            burn_in = row["from"]
            break
    for r in table:
        r["ratio_to_mature"] = round(r["rate"] / mature, 2) if mature else None
    return {"units": rows[0][4] if rows else 0, "mature_rate": mature,
            "burn_in_days": burn_in, "buckets": table}


def main() -> None:
    con = duckdb.connect()
    con.execute("PRAGMA threads=16")
    con.execute(
        f"""
        CREATE TABLE chan AS
        SELECT f.cid AS channel_id, f.oid AS object_id, min(e.d) AS first_day,
            least(max(e.d), DATE '{JOURNAL_END}') AS last_day
        FROM read_parquet('{EVENTS.as_posix()}') e
        JOIN read_parquet('{CHANNELS.as_posix()}') f ON f.cid = e.channel_id
        WHERE f.stype IN ('Датчик дыма', 'Датчик температуры', 'Тепловой датчик')
          AND f.picket IS NOT NULL
        GROUP BY ALL
        """
    )
    con.execute(f"CREATE TABLE a AS SELECT * FROM read_parquet('{ALARMS.as_posix()}')")
    con.execute(
        f"""
        CREATE TABLE events AS
        WITH nb AS (
            SELECT a.channel_id, a.object_id, a.moment::DATE AS day,
                (SELECT count(DISTINCT (r.gallery, r.picket)) FROM a r
                 WHERE r.object_id = a.object_id
                   AND (r.gallery <> a.gallery OR r.picket <> a.picket)
                   AND r.moment::DATE = a.moment::DATE
                   AND r.moment BETWEEN a.moment - INTERVAL {BURST_MINUTES} MINUTE
                                    AND a.moment + INTERVAL {BURST_MINUTES} MINUTE) AS n
            FROM a
        )
        SELECT DISTINCT channel_id, object_id, day FROM nb
        WHERE n <= {BURST} AND NOT (object_id = 5343 AND year(day) IN (2020, 2021))
        """
    )
    result = {"rule": f"первая корзина, с которой нижняя граница 95 % интервала каждой "
                      f"корзины не выше {RATIO} зрелой частоты",
              "censor_before": CENSOR}
    for level in ("channel", "object"):
        r = curve(con, level)
        result[level] = r
        print(f"{level}: единиц {r['units']}, зрелая частота {r['mature_rate']:.2e}, "
              f"период охлаждения {r['burn_in_days']} суток")
        for b in r["buckets"]:
            print(f"   {b['from']:>4}–{b['to']:<6} событий {b['events']:5d}, частота "
                  f"{b['rate']:.2e} [{b['ci_low']:.2e}; {b['ci_high']:.2e}], "
                  f"к зрелой {b['ratio_to_mature']}")
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
