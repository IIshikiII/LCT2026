"""Суточная панель не смотрит в будущее.

Тест написан по следам настоящей утечки. Признак `n_channels` брал число
каналов, тревожащих **в эти же сутки**, то есть прямо выдавал метку. PR-AUC на
проверке подскочил до 0,955 при базе 0,56 %, и только несуразность этого числа
выдала ошибку. Проверка глазами такое пропускает, потому что колонка называлась
безобидно.

Правило проверки одно: добавление тревог на день расчёта и позже не имеет права
изменить ни один признак.

Запуск из корня репозитория:

    .venv\\Scripts\\python.exe -m pytest ml/access/test_daily_panel.py -q
"""

from __future__ import annotations

import datetime as dt
import importlib
import pathlib
import sys

import duckdb
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

panel_module = importlib.import_module("07_daily_panel")

def ts(text: str) -> dt.datetime:
    """Время без часового пояса: DuckDB хранит TIMESTAMP именно таким."""
    return dt.datetime.fromisoformat(text)


UNIT = ("OBJ-1", 0, 10)
DAY_FROM = ts("2024-01-01T00:00:00")
DAY_TO = ts("2024-03-31T00:00:00")
POINT = dt.date(2024, 3, 1)

# 2024 год високосный: «вчера» для 1 марта это 29 февраля, а не 28.
PAST_ALARMS = [
    (ts("2024-02-20T03:00:00"), 4, 2),
    (ts("2024-02-25T23:00:00"), 2, 1),
    (ts("2024-02-29T10:00:00"), 7, 3),
]
FUTURE_ALARMS = [
    (ts("2024-03-01T00:00:00"), 9, 4),
    (ts("2024-03-01T15:00:00"), 5, 2),
    (ts("2024-03-10T06:00:00"), 8, 3),
]


def _connect(alarms: list[tuple[dt.datetime, int, int]]) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("PRAGMA threads=2")

    con.execute(
        "CREATE TABLE unit (object_id VARCHAR, gallery INTEGER, picket INTEGER, "
        "hour_from TIMESTAMP, hour_to TIMESTAMP, n_channels INTEGER)"
    )
    con.execute("INSERT INTO unit VALUES (?, ?, ?, ?, ?, ?)", [*UNIT, DAY_FROM, DAY_TO, 3])

    con.execute(
        "CREATE TABLE alarm (object_id VARCHAR, gallery INTEGER, picket INTEGER, "
        "hour TIMESTAMP, n_alarms INTEGER, n_moments INTEGER, n_channels INTEGER)"
    )
    con.execute(
        "CREATE TABLE armed (object_id VARCHAR, gallery INTEGER, picket INTEGER, "
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
        "picket INTEGER, completed_ts TIMESTAMP)"
    )
    return con


def _row(alarms: list[tuple[dt.datetime, int, int]]) -> dict[str, object]:
    con = _connect(alarms)
    panel_module.build_day_grid(con)
    panel_module.build_daily_counts(con)
    panel_module.build_panel(con, near=5, minutes=15)
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
    assert past_only["days_since_last_armed"] == 1
    assert past_only["disarm_hours_7d"] > 0


def test_future_rows_change_no_feature(
    past_only: dict[str, object], with_future: dict[str, object]
) -> None:
    """Тревоги на день расчёта и позже не меняют ни один признак."""
    skip = {"label", "naive", "object_id", "gallery", "picket", "day"}
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
    """Планка это «тревога была вчера». 29 февраля тревога была, значит 1 марта
    правило срабатывает."""
    assert past_only["naive"] == 1
