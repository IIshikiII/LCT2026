"""Суточная панель не смотрит в будущее.

Тест написан по следам настоящей утечки. Признак `n_channels` брал число
каналов, тревожащих **в эти же сутки**, то есть прямо выдавал метку. PR-AUC на
проверке подскочил до 0,955 при базе 0,56 %, и только несуразность этого числа
выдала ошибку. Проверка глазами такое пропускает, потому что колонка называлась
безобидно.

Правило проверки одно: добавление тревог с 00:00 точки расчёта и позже не
имеет права изменить ни один признак. Точка расчёта стоит за сутки до
прогнозируемых суток.

Запуск из корня репозитория:

    .venv/bin/python -m pytest ml/access/test_panel.py -q
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys

import duckdb
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import data  # noqa: E402
import panel as panel_module  # noqa: E402

def ts(text: str) -> dt.datetime:
    """Время без часового пояса: DuckDB хранит TIMESTAMP именно таким."""
    return dt.datetime.fromisoformat(text)


UNIT = ("OBJ-1", 0, 10)
# Сетка захватывает прошлый год: признак доли событий в эту же дату смотрит
# только в прошлые годы, и без них тест его не проверит.
DAY_FROM = ts("2023-01-01T00:00:00")
DAY_TO = ts("2024-03-31T00:00:00")
POINT = dt.date(2024, 3, 1)  # прогнозируемые сутки
# Точка расчёта стоит за `LEAD_DAYS` суток до прогнозируемых.
AS_OF = POINT - dt.timedelta(days=panel_module.LEAD_DAYS)
LAST_KNOWN = AS_OF - dt.timedelta(days=1)


def _at(day: dt.date, hour: int) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, hour)


# 2024 год високосный: тест держит границы месяца под присмотром.
PAST_ALARMS = [
    (ts("2024-02-20T03:00:00"), 4, 2),
    (ts("2024-02-25T23:00:00"), 2, 1),
    (_at(LAST_KNOWN, 10), 7, 3),
]
# Всё, что случилось с 00:00 точки расчёта. Тревоги суток POINT это метка.
FUTURE_ALARMS = [
    (_at(AS_OF, 15), 3, 1),
    (ts("2024-03-01T00:00:00"), 9, 4),
    (ts("2024-03-01T15:00:00"), 5, 2),
    (ts("2024-03-10T06:00:00"), 8, 3),
]


def _connect(alarms: list[tuple[dt.datetime, int, int]]) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("PRAGMA threads=2")

    con.execute(
        "CREATE TABLE unit (object_id VARCHAR, gallery INTEGER, section INTEGER, "
        "hour_from TIMESTAMP, hour_to TIMESTAMP, n_channels INTEGER)"
    )
    con.execute("INSERT INTO unit VALUES (?, ?, ?, ?, ?, ?)", [*UNIT, DAY_FROM, DAY_TO, 3])

    con.execute(
        "CREATE TABLE alarm (object_id VARCHAR, gallery INTEGER, section INTEGER, "
        "hour TIMESTAMP, n_alarms INTEGER, n_moments INTEGER, n_channels INTEGER)"
    )
    con.execute(
        "CREATE TABLE armed (object_id VARCHAR, gallery INTEGER, section INTEGER, "
        "hour TIMESTAMP, n_alarms INTEGER, n_moments INTEGER, n_channels INTEGER)"
    )
    for hour, moments, channels in alarms:
        row = [*UNIT, hour, moments, moments, channels]
        con.execute("INSERT INTO alarm VALUES (?, ?, ?, ?, ?, ?, ?)", row)
        con.execute("INSERT INTO armed VALUES (?, ?, ?, ?, ?, ?, ?)", row)

    con.execute(
        "CREATE TABLE disarm_window (object_id VARCHAR, t_from TIMESTAMP, "
        "t_to TIMESTAMP, open_end BOOLEAN)"
    )
    con.execute(
        "INSERT INTO disarm_window VALUES (?, ?, ?, ?)",
        ["OBJ-1", ts("2024-02-26T08:00:00"), ts("2024-02-26T17:00:00"), False],
    )

    con.execute(
        "CREATE TABLE access_chain (object_id VARCHAR, gallery INTEGER, "
        "section INTEGER, completed_ts TIMESTAMP)"
    )
    con.execute(
        "CREATE TABLE fault_day (object_id VARCHAR, gallery INTEGER, section INTEGER, "
        "day DATE, n_fault INTEGER, n_fault_channels INTEGER)"
    )
    con.execute(
        "CREATE TABLE section_info (object_id VARCHAR, gallery INTEGER, section INTEGER, "
        "is_branch INTEGER, no_entry INTEGER, n_pickets INTEGER, n_contact INTEGER, "
        "n_motion INTEGER, n_emergency_exit INTEGER, n_hatch INTEGER, n_vent_shaft INTEGER)"
    )
    con.execute(
        "CREATE TABLE guard_unknown (object_id VARCHAR, t_from TIMESTAMP, t_to TIMESTAMP)"
    )
    data.build_calendar(con)
    return con


def _same(a: object, b: object) -> bool:
    """Два пустых значения равны: NaN не равен сам себе."""
    a_empty = a is None or a != a
    b_empty = b is None or b != b
    if a_empty or b_empty:
        return a_empty and b_empty
    return a == b


def _row(alarms: list[tuple[dt.datetime, int, int]]) -> dict[str, object]:
    con = _connect(alarms)
    panel_module.build_day_grid(con)
    panel_module.build_daily_counts(con)
    panel_module.build_panel(con)
    frame = con.execute(
        "SELECT * FROM panel_daily WHERE day = ?", [POINT]
    ).df()
    assert len(frame) == 1, "ожидалась ровно одна строка на день расчёта"
    return frame.iloc[0].to_dict()


@pytest.fixture(scope="module")
def past_only() -> dict[str, object]:
    return _row(PAST_ALARMS)


@pytest.fixture(scope="module")
def with_future() -> dict[str, object]:
    return _row(PAST_ALARMS + FUTURE_ALARMS)


def test_the_point_sees_the_past(past_only: dict[str, object]) -> None:
    """Контроль: без него тест на утечку прошёл бы и на пустых признаках."""
    assert past_only["n_alarms_7d"] > 0
    assert past_only["n_alarms_30d"] > 0
    assert past_only["as_of"].date() == AS_OF
    assert past_only["days_since_last_armed"] == 1
    assert past_only["disarm_hours_7d"] > 0
    assert past_only["date_event_share_prev_years"] == 0  # прошлый год есть, событий нет


def test_future_rows_change_no_feature(
    past_only: dict[str, object], with_future: dict[str, object]
) -> None:
    """Тревоги на день расчёта и позже не меняют ни один признак."""
    skip = {"label", "naive", "object_id", "gallery", "section", "day", "as_of"}
    differs = [
        name
        for name, value in past_only.items()
        if name not in skip and not _same(with_future[name], value)
    ]
    assert differs == [], f"эти признаки заглянули в будущее: {differs}"


def test_the_label_does_change(
    past_only: dict[str, object], with_future: dict[str, object]
) -> None:
    """Метка обязана измениться: иначе предыдущий тест ничего не проверяет."""
    assert past_only["label"] == 0
    assert with_future["label"] == 1


def test_the_naive_rule_looks_one_day_back(past_only: dict[str, object]) -> None:
    """Планка это «событие было в последние известные сутки». В сутки
    LAST_KNOWN событие было, значит правило срабатывает."""
    assert past_only["naive"] == 1


def test_unknown_guard_mode_drops_the_day() -> None:
    """Сутки, когда режим охраны неизвестен, в панель не входят. Соседние
    сутки остаются."""
    con = _connect(PAST_ALARMS + FUTURE_ALARMS)
    con.execute(
        "INSERT INTO guard_unknown VALUES (?, ?, ?)",
        ["OBJ-1", ts("2024-02-29T12:00:00"), ts("2024-03-01T06:00:00")],
    )
    panel_module.build_day_grid(con)
    panel_module.build_daily_counts(con)
    panel_module.build_panel(con)
    days = {
        row[0]
        for row in con.execute(
            "SELECT day FROM panel_daily WHERE day BETWEEN ? AND ?",
            [dt.date(2024, 2, 28), dt.date(2024, 3, 2)],
        ).fetchall()
    }
    assert days == {dt.date(2024, 2, 28), dt.date(2024, 3, 2)}


# --- скользящая точка расчёта ------------------------------------------------

ROLL_HOUR = 12
ROLL_AT = _at(POINT, ROLL_HOUR)
# До точки расчёта в сутки POINT: признаки свежести обязаны это видеть.
ROLL_PAST = [
    *PAST_ALARMS,
    (_at(POINT, 3), 2, 1),
    (_at(POINT, 11), 6, 2),
]
# С момента t и позже. Тревога в 15:00 попадает в окно метки (t, t + 24 ч].
ROLL_FUTURE = [
    (_at(POINT, ROLL_HOUR), 5, 2),
    (_at(POINT, 15), 4, 1),
    (ts("2024-03-02T09:00:00"), 3, 1),
    (ts("2024-03-10T06:00:00"), 8, 3),
]


def _rolling_row(alarms: list[tuple[dt.datetime, int, int]]) -> dict[str, object]:
    con = _connect(alarms)
    panel_module.build_day_grid(con)
    panel_module.build_daily_counts(con)
    panel_module.build_panel(con, guard_filter=False)
    panel_module.build_rolling(con, (0, 6, 12, 18))
    frame = con.execute(
        "SELECT * FROM panel_rolling WHERE as_of = ?", [ROLL_AT]
    ).df()
    assert len(frame) == 1, "ожидалась ровно одна строка на точку расчёта"
    return frame.iloc[0].to_dict()


@pytest.fixture(scope="module")
def rolling_past() -> dict[str, object]:
    return _rolling_row(ROLL_PAST)


@pytest.fixture(scope="module")
def rolling_future() -> dict[str, object]:
    return _rolling_row(ROLL_PAST + ROLL_FUTURE)


def test_the_rolling_point_sees_the_same_day(rolling_past: dict[str, object]) -> None:
    """Контроль: свежесть видит тревоги суток до t, иначе тест ниже пуст."""
    assert rolling_past["as_of_hour"] == ROLL_HOUR
    assert rolling_past["alarms_last_1h"] == 6
    assert rolling_past["alarms_last_6h"] == 6
    assert rolling_past["events_last_6h"] == 6
    assert rolling_past["hours_since_last_alarm_t"] == 1
    assert rolling_past["naive"] == 1


def test_rolling_future_changes_no_feature(
    rolling_past: dict[str, object], rolling_future: dict[str, object]
) -> None:
    """Тревоги с момента t и позже не меняют ни один признак."""
    skip = {"label", "naive", "object_id", "gallery", "section", "day", "as_of"}
    differs = [
        name
        for name, value in rolling_past.items()
        if name not in skip and not _same(rolling_future[name], value)
    ]
    assert differs == [], f"эти признаки заглянули в будущее: {differs}"


def test_the_rolling_label_looks_24_hours_ahead(
    rolling_past: dict[str, object], rolling_future: dict[str, object]
) -> None:
    """Метка это событие в окне (t, t + 24 ч]: тревога в 15:00 её поднимает."""
    assert rolling_past["label"] == 0
    assert rolling_future["label"] == 1
