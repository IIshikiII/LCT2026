"""Панель подтопления не смотрит в будущее.

Правило то же, что у доступа (`ml/access/test_daily_panel.py`): события и
работа насосов в час расчёта и позже не имеют права изменить ни один признак.
Проверка идёт на всех трёх уровнях сразу: единица, соседняя единица того же
объекта с насосом без пикета, соседний объект того же комплекса.

Запуск из корня репозитория:

    .venv\\Scripts\\python.exe -m pytest ml/flood/test_panel.py -q
"""

from __future__ import annotations

import datetime as dt
import importlib
import pathlib
import sys

import duckdb
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

panel_module = importlib.import_module("04_panel")

BLINK = 15.0


def ts(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text)


UNIT = (1, -1.0, 10.0)
NEIGHBOUR_UNIT = (1, -1.0, 20.0)
OTHER_OBJECT_UNIT = (2, -1.0, 5.0)
LIFE = (ts("2024-01-01T00:00:00"), ts("2024-03-31T23:00:00"))
POINT = ts("2024-03-01T00:00:00")

# События метки: (единица, час, моменты).
PAST_EVENTS = [
    (UNIT, ts("2024-02-20T03:00:00"), 2),
    (UNIT, ts("2024-02-29T22:00:00"), 1),
    (NEIGHBOUR_UNIT, ts("2024-02-29T10:00:00"), 3),
    (OTHER_OBJECT_UNIT, ts("2024-02-28T10:00:00"), 1),
]
FUTURE_EVENTS = [
    (UNIT, ts("2024-03-01T00:00:00"), 5),
    (UNIT, ts("2024-03-01T15:00:00"), 2),
    (NEIGHBOUR_UNIT, ts("2024-03-01T02:00:00"), 4),
    (OTHER_OBJECT_UNIT, ts("2024-03-02T02:00:00"), 4),
]

# Насосы: (канал, объект, галерея, пикет, комплекс, час, смены, минуты работы,
# недоступность, все насосы, затоплен). Канал 12 без пикета.
PAST_PUMPS = [
    (11, 1, -1.0, 10.0, 7, ts("2024-02-29T23:00:00"), 30, 40.0, 1, 1, 0),
    (11, 1, -1.0, 10.0, 7, ts("2024-02-25T05:00:00"), 3, 20.0, 0, 0, 0),
    (12, 1, None, None, 7, ts("2024-02-29T20:00:00"), 20, 50.0, 2, 1, 1),
    (21, 2, -1.0, 5.0, 7, ts("2024-02-29T12:00:00"), 18, 30.0, 1, 2, 0),
]
FUTURE_PUMPS = [
    (11, 1, -1.0, 10.0, 7, ts("2024-03-01T00:00:00"), 40, 60.0, 3, 1, 0),
    (12, 1, None, None, 7, ts("2024-03-01T01:00:00"), 25, 60.0, 1, 1, 2),
    (21, 2, -1.0, 5.0, 7, ts("2024-03-01T00:00:00"), 30, 60.0, 2, 3, 0),
]


# Проверки на пикете: сигналы внутри рабочего окна (ADR 0010).
PAST_CHECKS = [(UNIT, ts("2024-02-28T10:00:00"), 1)]
FUTURE_CHECKS = [(UNIT, ts("2024-03-01T10:00:00"), 1)]


def _weather(con: duckdb.DuckDBPyConnection, future: bool) -> None:
    """Погода по часам на весь срок. Будущее отличается ливнем и оттепелью."""
    con.execute(
        "CREATE TABLE weather (hour TIMESTAMP, precip DOUBLE, rain DOUBLE, "
        "snowfall DOUBLE, temp DOUBLE, snow_depth DOUBLE)"
    )
    hour = LIFE[0]
    rows = []
    while hour <= LIFE[1]:
        ahead = hour >= POINT
        rain = 5.0 if (future and ahead) else (1.0 if hour.hour == 3 else 0.0)
        temp = 8.0 if (future and ahead) else -2.0
        depth = 0.0 if (future and ahead) else 0.30 - 0.001 * (hour - LIFE[0]).days
        rows.append((hour, rain, rain, 0.0, temp, depth))
        hour += dt.timedelta(hours=1)
    con.executemany("INSERT INTO weather VALUES (?, ?, ?, ?, ?, ?)", rows)


def _connect(
    events: list, pumps: list, checks: list, future_weather: bool
) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("PRAGMA threads=2")
    con.execute(
        "CREATE TABLE unit (object_id BIGINT, gallery DOUBLE, picket DOUBLE, "
        "hour_from TIMESTAMP, hour_to TIMESTAMP, n_channels INTEGER)"
    )
    for unit in (UNIT, NEIGHBOUR_UNIT, OTHER_OBJECT_UNIT):
        con.execute("INSERT INTO unit VALUES (?, ?, ?, ?, ?, ?)", [*unit, *LIFE, 1])
    con.execute(
        "CREATE TABLE alarm (object_id BIGINT, gallery DOUBLE, picket DOUBLE, "
        "hour TIMESTAMP, n_moments INTEGER)"
    )
    for unit, hour, moments in events:
        con.execute("INSERT INTO alarm VALUES (?, ?, ?, ?, ?)", [*unit, hour, moments])
    con.execute(
        "CREATE TABLE check_signal (object_id BIGINT, gallery DOUBLE, picket DOUBLE, "
        "hour TIMESTAMP, n_moments INTEGER)"
    )
    for unit, hour, moments in checks:
        con.execute(
            "INSERT INTO check_signal VALUES (?, ?, ?, ?, ?)", [*unit, hour, moments]
        )
    _weather(con, future_weather)
    con.execute(
        "CREATE TABLE pump (channel_id BIGINT, object_id BIGINT, gallery DOUBLE, "
        "picket DOUBLE, complex_id BIGINT, hour TIMESTAMP, n_changes INTEGER, "
        "on_minutes DOUBLE, n_unavail INTEGER, n_all_pumps INTEGER, n_flooded INTEGER)"
    )
    for row in pumps:
        con.execute(
            "INSERT INTO pump VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", list(row)
        )
    con.execute("CREATE TABLE object_complex (object_id BIGINT, complex_id BIGINT)")
    con.execute("INSERT INTO object_complex VALUES (1, 7), (2, 7)")
    con.execute(
        "CREATE TABLE unit_meta (object_id BIGINT, gallery DOUBLE, picket DOUBLE, "
        "n_pumps INTEGER, n_flood_sensors INTEGER)"
    )
    con.execute(
        "INSERT INTO unit_meta VALUES (1, -1.0, 10.0, 1, 0), (1, -1.0, 20.0, 0, 1), "
        "(2, -1.0, 5.0, 1, 0)"
    )
    return con


def _row(
    events: list, pumps: list, checks: list, future_weather: bool
) -> dict[str, object]:
    con = _connect(events, pumps, checks, future_weather)
    panel_module.build_panel(con, BLINK)
    frame = con.execute(
        "SELECT * FROM panel WHERE object_id = ? AND picket = ? AND hour = ?",
        [UNIT[0], UNIT[2], POINT],
    ).df()
    assert len(frame) == 1, "ожидалась ровно одна строка на час расчёта"
    return frame.iloc[0].to_dict()


@pytest.fixture(scope="module")
def past_only() -> dict[str, object]:
    return _row(PAST_EVENTS, PAST_PUMPS, PAST_CHECKS, False)


@pytest.fixture(scope="module")
def with_future() -> dict[str, object]:
    return _row(
        PAST_EVENTS + FUTURE_EVENTS,
        PAST_PUMPS + FUTURE_PUMPS,
        PAST_CHECKS + FUTURE_CHECKS,
        True,
    )


def test_the_point_sees_the_past(past_only: dict[str, object]) -> None:
    """Контроль: без него тест на утечку прошёл бы и на пустых признаках."""
    assert past_only["pump_changes_1h"] == 30
    assert past_only["check_hours_168h"] == 1
    assert past_only["weather_precip_24h"] == 1.0
    assert past_only["weather_temp_mean_24h"] == -2.0
    assert past_only["weather_snow_depth_cm"] > 0
    assert past_only["pump_blink_hours_24h"] == 1
    assert past_only["pump_unavailable_24h"] == 1
    assert past_only["evt_hours_24h"] == 1
    assert past_only["evt_hours_since_last"] == 2
    # Сосед по объекту: событие соседней единицы. Затопление насоса без пикета
    # не разметить, поэтому водой оно не считается (ADR 0012).
    assert past_only["obj_events_24h"] == 3
    assert past_only["obj_pump_blink_24h"] == 1
    # соседний объект того же комплекса
    assert past_only["cx_events_24h"] == 0
    assert past_only["cx_events_168h"] == 1
    assert past_only["cx_pump_blink_24h"] == 1


def test_future_rows_change_no_feature(
    past_only: dict[str, object], with_future: dict[str, object]
) -> None:
    """События и работа насосов в час расчёта и позже не меняют ни один признак."""
    skip = {"label", "naive", "object_id", "gallery", "picket", "hour"}
    differs = [
        name
        for name, value in past_only.items()
        if name not in skip and with_future[name] != value
    ]
    assert differs == [], f"эти признаки заглянули в будущее: {differs}"


def test_the_label_does_change(
    past_only: dict[str, object], with_future: dict[str, object]
) -> None:
    """Метка обязана измениться: иначе предыдущий тест ничего не проверяет."""
    assert past_only["label"] == 0
    assert with_future["label"] == 1


def test_the_naive_rule_looks_one_day_back(past_only: dict[str, object]) -> None:
    """Событие было 29 февраля в 22:00, значит на 1 марта 00:00 правило срабатывает."""
    assert past_only["naive"] == 1
