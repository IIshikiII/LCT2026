"""Границы уровней бэкенда и замер модели держат одни числа. Задача T41.

Числа жили в двух местах и разошлись. `ml/access/out/metrics.json` давал
`HIGH` 0,156089, а `app/meta/directions.py` — 0,163753 с калиброванной шкалы.
Граница `CRITICAL` при этом осталась от прежнего обучения и лежала ниже
`HIGH`, из-за чего верхний уровень был недостижим.

Соглашение такую пару не удержит. Держит тест.

Замер лежит вне образа бэкенда: `ml/` в него не входит. Файла нет значит
проверка пропускается, а не падает.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.meta import directions

METRICS = Path(__file__).resolve().parents[2] / "ml" / "access" / "out" / "metrics.json"


def _decision() -> dict[str, object]:
    if not METRICS.exists():
        pytest.skip(f"замер модели недоступен: {METRICS}")
    payload = json.loads(METRICS.read_text(encoding="utf-8"))
    decision: dict[str, object] = payload["decision"]
    return decision


def test_the_access_bands_equal_the_measured_levels() -> None:
    levels = _decision()["levels"]
    assert isinstance(levels, dict)

    bands = dict(directions.UNAUTHORIZED_ACCESS.level_thresholds)

    assert set(bands) == set(levels)
    for code, bound in bands.items():
        assert bound == pytest.approx(float(levels[code])), (
            f"граница {code} в directions.py равна {bound}, "
            f"а в metrics.json {levels[code]}"
        )


def test_the_measured_levels_grow() -> None:
    """Немонотонный набор делает верхний уровень недостижимым."""
    levels = _decision()["levels"]
    assert isinstance(levels, dict)

    order = ["MEDIUM", "HIGH", "CRITICAL"]
    values = [float(levels[code]) for code in order]

    assert values == sorted(values)
    assert len(set(values)) == len(values)


def test_the_order_threshold_equals_the_high_band() -> None:
    """Заявка появляется ровно там, где её назначил разбор цены ошибки."""
    decision = _decision()
    levels = decision["levels"]
    assert isinstance(levels, dict)

    assert float(decision["threshold"]) == pytest.approx(float(levels["HIGH"]))
    assert directions.UNAUTHORIZED_ACCESS.order_levels == ("HIGH", "CRITICAL")


def test_the_scale_is_named_and_matches_the_calibrator() -> None:
    """Шкала границ обязана совпадать со шкалой `prediction.probability`.

    Калибратор принят значит числа стоят на калиброванной шкале, отклонён
    значит на сырой. Расхождение здесь означает, что уровень на карточке
    считается не по той вероятности, которую карточка показывает.
    """
    if not METRICS.exists():
        pytest.skip(f"замер модели недоступен: {METRICS}")
    payload = json.loads(METRICS.read_text(encoding="utf-8"))

    scale = payload["decision"]["scale"]
    kept = payload["calibration"]["kept"]

    assert scale in {"raw", "calibrated"}
    assert scale == ("calibrated" if kept else "raw")
