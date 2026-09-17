"""Тест утечки по времени для `build_features`. Строит признаки один раз на
синтетических данных, обрезанных по моменту расчёта `at`, затем добавляет
строки из будущего (позже `at`, включая сам час `at`) в каждый источник —
`alarm`, `unit`, `disarm_window`, `access_chain` — и считает признаки снова.
Ни один признак не имеет права измениться: иначе прогноз смотрит в будущее.

Данные синтетические и не читают `out/*.parquet`, чтобы тест не зависел от
выгрузки и шёл быстро в `pytest ml -q`.
"""
import pathlib
import sys

import duckdb

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from features import FEATURE_COLUMNS, build_features

OBJECT_ID = 1
GALLERY = "G1"
PICKET = 100
AT = "TIMESTAMP '2024-01-10 12:00:00'"


def _setup(con: duckdb.DuckDBPyConnection, include_future: bool) -> None:
    """Создаёт представления `alarm`, `unit`, `disarm_window`, `access_chain`
    и таблицу `points` с одной точкой на момент `AT`. `include_future=True`
    добавляет строки на час `AT` и позже него в каждый источник."""
    alarm_rows = [
        (OBJECT_ID, GALLERY, PICKET, "TIMESTAMP '2024-01-10 08:00:00'", 3, 2),
        (OBJECT_ID, GALLERY, PICKET, "TIMESTAMP '2024-01-09 23:00:00'", 1, 1),
        (OBJECT_ID, GALLERY, PICKET, "TIMESTAMP '2024-01-05 03:00:00'", 2, 1),
    ]
    if include_future:
        alarm_rows += [
            (OBJECT_ID, GALLERY, PICKET, AT, 9, 9),
            (OBJECT_ID, GALLERY, PICKET, "TIMESTAMP '2024-01-10 13:00:00'", 9, 9),
        ]
    alarm_values = ", ".join(
        f"({o}, '{g}', {p}, {h}, {m}, {c})" for o, g, p, h, m, c in alarm_rows
    )
    con.execute(
        f"CREATE OR REPLACE VIEW alarm AS "
        f"SELECT * FROM (VALUES {alarm_values}) "
        f"AS t(object_id, gallery, picket, hour, n_moments, n_channels)"
    )

    con.execute(
        f"CREATE OR REPLACE VIEW unit AS "
        f"SELECT * FROM (VALUES "
        f"({OBJECT_ID}, '{GALLERY}', {PICKET}, "
        f"TIMESTAMP '2023-01-01 00:00:00', TIMESTAMP '2024-12-31 23:00:00')) "
        f"AS t(object_id, gallery, picket, hour_from, hour_to)"
    )

    disarm_values = "(-1, TIMESTAMP '2000-01-01 00:00:00', TIMESTAMP '2000-01-01 01:00:00')"
    if include_future:
        disarm_values += (
            f", ({OBJECT_ID}, TIMESTAMP '2024-01-10 12:01:00', "
            f"TIMESTAMP '2024-01-10 18:00:00')"
        )
    con.execute(
        f"CREATE OR REPLACE VIEW disarm_window AS "
        f"SELECT * FROM (VALUES {disarm_values}) "
        f"AS t(object_id, t_from, t_to)"
    )

    chain_values = (
        f"({OBJECT_ID}, '{GALLERY}', {PICKET}, TIMESTAMP '2024-01-10 11:50:00')"
    )
    if include_future:
        chain_values += (
            f", ({OBJECT_ID}, '{GALLERY}', {PICKET}, {AT})"
        )
    con.execute(
        f"CREATE OR REPLACE VIEW access_chain AS "
        f"SELECT * FROM (VALUES {chain_values}) "
        f"AS t(object_id, gallery, picket, completed_ts)"
    )

    con.execute(
        f"CREATE OR REPLACE TABLE points AS "
        f"SELECT {OBJECT_ID} AS object_id, '{GALLERY}' AS gallery, "
        f"{PICKET} AS picket, {AT} AS at"
    )


def _feature_row(con: duckdb.DuckDBPyConnection, include_future: bool) -> dict:
    _setup(con, include_future)
    view = build_features(con)
    columns = ", ".join(FEATURE_COLUMNS)
    row = con.execute(f"SELECT {columns} FROM {view}").fetchone()
    return dict(zip(FEATURE_COLUMNS, row))


def test_features_ignore_future_rows() -> None:
    con = duckdb.connect()
    baseline = _feature_row(con, include_future=False)
    with_future = _feature_row(con, include_future=True)
    assert baseline == with_future


def test_features_see_past_rows() -> None:
    """Контроль на пустой тест: без будущих строк признаки не пусты и
    видят прошлое, иначе `test_features_ignore_future_rows` прошёл бы даже
    на сломанном запросе, который ничего не считает."""
    con = duckdb.connect()
    baseline = _feature_row(con, include_future=False)
    assert baseline["n_alarms_24h"] == 4
    assert baseline["n_alarms_720h"] == 6
    assert baseline["is_disarmed"] == 0
    assert baseline["has_access_sequence"] == 1
