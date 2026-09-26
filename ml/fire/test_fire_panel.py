"""Панель пожарного риска не смотрит в будущее.

Правило то же, что у подтопления (`ml/flood/test_flood_panel.py`): сигналы, числа
датчиков и погода в сутки расчёта и позже не имеют права изменить ни один
признак. Проверка идёт на трёх уровнях: пикет, соседний пикет того же объекта,
газовый объект того же комплекса.

Запуск из корня репозитория:

    .venv\\Scripts\\python.exe -m pytest ml/fire/test_fire_panel.py -q
"""

from __future__ import annotations

import datetime as dt
import importlib
import math
import pathlib
import sys

import duckdb
import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "common"))

import calendar_ru

panel_module = importlib.import_module("05_panel")


def ts(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text)


def day(text: str) -> dt.date:
    return dt.date.fromisoformat(text)


UNIT = (1, -1.0, 10.0)
NEIGHBOUR = (1, -1.0, 14.0)  # 40 метров от пикета 10
FAR = [(1, -1.0, float(k)) for k in (30, 40, 50)]
GAS_OBJECT = 2
COMPLEX = 7
LIFE = (ts("2024-01-01T00:00:00"), ts("2024-03-31T23:00:00"))
POINT = ts("2024-03-01T00:00:00")

# Сигналы метки. 28 февраля среда, 29 февраля четверг, 1 марта пятница.
# 28 февраля в 10:00 пикет тревожит вместе с тремя другими: это обход.
PAST_ALARMS = [
    (UNIT, ts("2024-02-20T03:00:00")),
    (UNIT, ts("2024-02-28T10:00:00")),
    *[(far, ts(f"2024-02-28T10:{5 * i:02d}:00")) for i, far in enumerate(FAR, 1)],
    (UNIT, ts("2024-02-29T23:30:00")),
    (NEIGHBOUR, ts("2024-02-29T03:00:00")),
]
# Пачка сразу после полуночи не делает обходом сигнал 29 февраля в 23:30.
FUTURE_ALARMS = [
    (UNIT, ts("2024-03-01T20:00:00")),
    (UNIT, ts("2024-03-01T10:00:00")),
    *[(far, ts("2024-03-01T00:10:00")) for far in FAR],
    (NEIGHBOUR, ts("2024-03-01T02:00:00")),
]
PAST_FAULTS = [(UNIT, day("2024-02-29"), 2)]
FUTURE_FAULTS = [(UNIT, day("2024-03-01"), 9)]
PAST_TEMP = [(UNIT, day("2024-02-29"), 24.0, 21.0), (UNIT, day("2024-02-10"), 20.0, 18.0)]
FUTURE_TEMP = [(UNIT, day("2024-03-01"), 60.0, 50.0)]
PAST_OBJTEMP = [(1, day("2024-02-29"), 24.0)]
FUTURE_OBJTEMP = [(1, day("2024-03-01"), 60.0)]
# (сутки, фаза, вентилятор, разговор, дверь, газ макс, газ средний, 0,1–1, ≥1, ≥1 вне окна)
PAST_CX = [(day("2024-02-29"), 1, 2, 3, 4, 0.8, 0.02, 5, 0, 0),
           (day("2024-02-15"), 0, 0, 0, 0, 1.5, 0.03, 1, 6, 0)]
FUTURE_CX = [(day("2024-03-01"), 9, 9, 9, 9, 7.0, 0.5, 9, 9, 9)]


def _connect(future: bool) -> tuple[duckdb.DuckDBPyConnection, str]:
    con = duckdb.connect()
    con.execute("PRAGMA threads=2")
    calendar = calendar_ru.build_calendar(con)
    con.execute(
        "CREATE TABLE unit (object_id BIGINT, gallery DOUBLE, picket DOUBLE, "
        "hour_from TIMESTAMP, hour_to TIMESTAMP, n_channels INTEGER, n_smoke INTEGER, "
        "n_temp INTEGER, n_heat INTEGER)"
    )
    for unit in (UNIT, NEIGHBOUR):
        con.execute("INSERT INTO unit VALUES (?, ?, ?, ?, ?, 2, 1, 1, 0)", [*unit, *LIFE])
    con.execute(
        "CREATE TABLE alarm (object_id BIGINT, gallery DOUBLE, picket DOUBLE, "
        "moment TIMESTAMP, channel_id BIGINT, stype VARCHAR)"
    )
    for unit, moment in PAST_ALARMS + (FUTURE_ALARMS if future else []):
        con.execute("INSERT INTO alarm VALUES (?, ?, ?, ?, ?, 'Датчик дыма')",
                    [*unit, moment, int(unit[2])])
    con.execute(
        "CREATE TABLE fault (object_id BIGINT, gallery DOUBLE, picket DOUBLE, day DATE, "
        "n BIGINT)"
    )
    for unit, d, n in PAST_FAULTS + (FUTURE_FAULTS if future else []):
        con.execute("INSERT INTO fault VALUES (?, ?, ?, ?, ?)", [*unit, d, n])
    con.execute(
        "CREATE TABLE temp (object_id BIGINT, gallery DOUBLE, picket DOUBLE, day DATE, "
        "t_max DOUBLE, t_mean DOUBLE)"
    )
    for unit, d, t_max, t_mean in PAST_TEMP + (FUTURE_TEMP if future else []):
        con.execute("INSERT INTO temp VALUES (?, ?, ?, ?, ?, ?)", [*unit, d, t_max, t_mean])
    con.execute("CREATE TABLE objtemp (object_id BIGINT, day DATE, obj_t_max DOUBLE)")
    for row in PAST_OBJTEMP + (FUTURE_OBJTEMP if future else []):
        con.execute("INSERT INTO objtemp VALUES (?, ?, ?)", list(row))
    con.execute(
        "CREATE TABLE cxday (complex_id BIGINT, day DATE, phase_off BIGINT, "
        "fan_off BIGINT, talk BIGINT, door BIGINT, gas_max DOUBLE, gas_mean DOUBLE, "
        "gas_mid BIGINT, gas_ge1 BIGINT, gas_ge1_off BIGINT)"
    )
    for row in PAST_CX + (FUTURE_CX if future else []):
        con.execute("INSERT INTO cxday VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [COMPLEX, *row])
    con.execute("CREATE TABLE object_complex (object_id BIGINT, complex_id BIGINT)")
    con.execute(f"INSERT INTO object_complex VALUES (1, {COMPLEX}), ({GAS_OBJECT}, {COMPLEX})")
    con.execute(
        "CREATE TABLE weather (day DATE, temp_mean DOUBLE, temp_max DOUBLE, "
        "precip_mm DOUBLE)"
    )
    con.execute(
        "CREATE TABLE external (day DATE, fireworks INTEGER, wind_mean DOUBLE, "
        "gust_max DOUBLE, cloud_mean DOUBLE, soil_temp DOUBLE, pm25_mean DOUBLE, "
        "pm25_max DOUBLE, pm10_mean DOUBLE, co_max DOUBLE)"
    )
    con.execute(
        "CREATE TABLE humidity (day DATE, rh_mean DOUBLE, rh_max DOUBLE, dew_mean DOUBLE, "
        "pressure_mean DOUBLE, pressure_change DOUBLE)"
    )
    d = LIFE[0].date()
    while d <= LIFE[1].date():
        hot = future and d >= POINT.date()
        con.execute("INSERT INTO weather VALUES (?, ?, ?, ?)",
                    [d, 30.0 if hot else -2.0, 35.0 if hot else 1.0, 9.0 if hot else 0.5])
        con.execute("INSERT INTO humidity VALUES (?, ?, ?, ?, ?, ?)",
                    [d, 99.0 if hot else 70.0, 100.0 if hot else 80.0, 20.0 if hot else -5.0,
                     1000.0, -9.0 if hot else 0.5])
        con.execute("INSERT INTO external VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [d, 0, 50.0 if hot else 10.0, 90.0 if hot else 20.0, 50.0, 3.0,
                     300.0 if hot else 20.0, 400.0 if hot else 30.0, 30.0, 500.0])
        d += dt.timedelta(days=1)
    return con, calendar


def _row(future: bool) -> dict[str, object]:
    con, calendar = _connect(future)
    panel_module.build_panel(con, calendar)
    frame = con.execute(
        "SELECT * FROM panel WHERE object_id = ? AND picket = ? AND hour = ?",
        [UNIT[0], UNIT[2], POINT],
    ).df()
    assert len(frame) == 1, "ожидалась ровно одна строка на сутки расчёта"
    return frame.iloc[0].to_dict()


@pytest.fixture(scope="module")
def past_only() -> dict[str, object]:
    return _row(False)


@pytest.fixture(scope="module")
def with_future() -> dict[str, object]:
    return _row(True)


def test_the_point_sees_the_past(past_only: dict[str, object]) -> None:
    """Контроль: без него тест на утечку прошёл бы и на пустых признаках."""
    assert past_only["evt_days_1d"] == 1
    assert past_only["evt_days_30d"] == 2
    assert past_only["evt_days_all"] == 2
    assert past_only["evt_days_since"] == 1
    assert past_only["chk_days_30d"] == 1
    assert past_only["chk_days_since"] == 2
    assert past_only["obj_events_1d"] == 1
    assert past_only["line_events_1d"] == 1
    assert past_only["obj_walk_days_since"] == 2
    assert past_only["obj_walks_365d"] == 1
    assert past_only["wx_rh_mean_1d"] == 70.0
    assert past_only["wx_pm25_mean_1d"] == 20.0
    # последний сигнал 29 февраля в 23:30: до полуночи 30 минут
    assert past_only["rec_to_midnight_1d"] == 30
    assert past_only["rec_smoke_1d"] == 1
    assert past_only["rec_repeats_all"] == 0
    assert past_only["cond_dew_minus_unit"] == -5.0 - 21.0
    assert past_only["hw_faults_1d"] == 2
    assert past_only["tmp_max_1d"] == 24.0
    assert past_only["obj_temp_max_1d"] == 24.0
    assert past_only["gas_max_1d"] == 0.8
    assert past_only["gas_ge1_30d"] == 6
    assert past_only["gas_days_since_ge1"] == 15
    assert past_only["pwr_fan_off_1d"] == 2
    assert past_only["ppl_talk_1d"] == 3
    assert past_only["ppl_door_1d"] == 4
    assert past_only["wx_temp_mean_1d"] == -2.0


def test_future_rows_change_no_feature(
    past_only: dict[str, object], with_future: dict[str, object]
) -> None:
    """Сигналы, числа и погода в сутки расчёта и позже не меняют ни один признак."""
    # День недели и салют суток расчёта известны заранее, как календарь.
    skip = {"label", "naive", "object_id", "gallery", "picket", "hour",
            "week_day_of_week", "week_is_day_off", "cal_fireworks"}
    differs = [
        name
        for name, value in past_only.items()
        if name not in skip and not _same(with_future[name], value)
    ]
    assert differs == [], f"эти признаки заглянули в будущее: {differs}"


def _same(a: object, b: object) -> bool:
    both_nan = (
        isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b)
    )
    return both_nan or a == b


def test_the_label_does_change(
    past_only: dict[str, object], with_future: dict[str, object]
) -> None:
    """Метка обязана измениться: иначе предыдущий тест ничего не проверяет."""
    assert past_only["label"] == 0
    assert with_future["label"] == 1


def test_a_burst_is_a_walk_not_an_event() -> None:
    """Сигнал 28 февраля в 10:00 идёт в пачке с тремя пикетами: метки 28-го нет."""
    con, calendar = _connect(False)
    panel_module.build_panel(con, calendar)
    label = con.execute(
        "SELECT label FROM panel WHERE object_id = ? AND picket = ? AND hour = ?",
        [UNIT[0], UNIT[2], ts("2024-02-28T00:00:00")],
    ).fetchone()[0]
    assert label == 0


def test_the_naive_rule_looks_one_day_back(past_only: dict[str, object]) -> None:
    """Событие было 29 февраля в 23:30, значит на 1 марта правило срабатывает."""
    assert past_only["naive"] == 1
