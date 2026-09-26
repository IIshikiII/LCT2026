"""Предиктор направления «пожарный риск». Экспертные правила, ADR 0016.

Модели у направления нет: она не обогнала правило «событие было вчера»
(ADR 0015), а подтверждённых пожаров, на которых можно учить модель пожара,
в выгрузке нет. Уровень ставят правила `app/ml/fire_rules.py` по совпадению
признаков, похожих на настоящий пожар. Факты эпизода собирает
`app/features/fire.py`.

## Что значат числа прогноза

- **Уровень** ставят правила напрямую, через хук `level`. Порогов по
  вероятности у направления нет.
- **Вероятность** это доля эпизодов того же уровня, после которых на пикете
  назавтра пришёл сигнал пожарной сигнализации вне пачки. История 2019–2025
  годов. Это не вероятность пожара.
- **Точность** правил не измерена. Её считают отметки бригад при закрытии
  заявок, и дашборд говорит это прямо.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.features import fire, fire_live
from app.ml.fire_rules import (
    NEXT_DAY_SIGNAL_RATE,
    QUIET_NEXT_DAY_RATE,
    TEMP_RISE_C,
    Facts,
    assess,
)
from app.ml.protocol import Block, FeatureContext, FeatureVector, Window
from app.ml.registry import register

DIRECTION = "FIRE_RISK"

ACCURACY_NOTE = (
    "Точность правил не измерена: подтверждённых пожаров в выгрузке нет. "
    "Её посчитают отметки бригад при закрытии заявок."
)
_FACTOR_NOTE = "Полоса вправо усиливает тревогу о пожаре, влево ослабляет её."

# Экспертный вес признака для блока `factors`, от −1 до 1.
WEIGHTS: dict[str, tuple[str, float]] = {
    "heat_independent": ("Дым подтверждён тепловым сигналом другого датчика", 0.8),
    "spread": ("Сигнал перешёл на соседний пикет", 0.6),
    "temp_rise": ("Температура коллектора выше нормы", 0.6),
    "gas": ("Метан вне окна плановых работ", 0.6),
    "off_hours_no_walk": ("Ночь или выходной, обхода в объекте не было", 0.3),
    "smoke": ("Сигнал датчика дыма", 0.2),
    "power_off": ("Обесточена фаза в комплексе рядом по времени, справка", 0.0),
    "cooling": ("Датчик или объект в периоде охлаждения", -0.4),
    "burst_only": ("Сигналы шли пачкой: обход или сбой линии", -0.6),
}


def _active(facts: Facts) -> dict[str, float]:
    """Какие признаки эпизода горят и с каким значением для карточки."""
    found: dict[str, float] = {}
    if facts.heat_independent:
        found["heat_independent"] = 1.0
    if facts.spread:
        found["spread"] = 1.0
    if facts.temp_rise_c >= TEMP_RISE_C:
        found["temp_rise"] = facts.temp_rise_c
    if facts.gas_pct >= 1.0:
        found["gas"] = facts.gas_pct
    if facts.off_hours_no_walk:
        found["off_hours_no_walk"] = 1.0
    if facts.smoke:
        found["smoke"] = 1.0
    if facts.power_off:
        found["power_off"] = 1.0
    if facts.sensor_cooling or facts.object_cooling:
        found["cooling"] = float(facts.cooling_days_left)
    if facts.burst_only:
        found["burst_only"] = 1.0
    return found


def factors_block(facts: Facts) -> Block:
    found = _active(facts)
    items: list[dict[str, Any]] = [
        {"label": WEIGHTS[name][0], "weight": WEIGHTS[name][1], "value": f"{value:g}"}
        for name, value in sorted(found.items(), key=lambda kv: -abs(WEIGHTS[kv[0]][1]))
    ]
    if not items:
        items = [{"label": "Сигналов пожарной сигнализации за сутки нет", "weight": 0.0}]
    return Block(
        type="factors",
        title="Какие признаки совпали",
        data={"items": items, "note": _FACTOR_NOTE},
    )


def rules_block(features: FeatureVector, facts: Facts, level: str) -> Block:
    result = assess(facts)
    probability = FireRisk.probability_of(facts, level)
    items = [
        {"label": "Уровень", "value": level},
        {"label": "Почему", "value": "; ".join(result.reasons) or "признаков нет"},
        {"label": "Сигналов за 24 ч", "value": f"{features.get('signals_24h', 0.0):g}"},
        {"label": "Из них в пачке", "value": f"{features.get('burst_signals_24h', 0.0):g}"},
        {
            "label": "Сигнал назавтра на истории",
            "value": f"{probability:.1%} эпизодов уровня {level}, 2019–2025",
        },
        {"label": "Точность правил", "value": ACCURACY_NOTE},
    ]
    return Block(type="keyvalue", title="Как поставлен уровень", data={"items": items})


def timeseries_block(features: FeatureVector, at: datetime) -> Block:
    points = []
    for index in range(fire.BUCKETS):
        middle = at - timedelta(hours=(fire.BUCKETS - index - 0.5) * fire.BUCKET_HOURS)
        points.append(
            {
                "t": middle.isoformat(),
                "v": float(features.get(f"signals_bucket_{index}", 0.0)),
            }
        )
    return Block(
        type="timeseries",
        title="Сигналы пожарной сигнализации пикета за сутки",
        data={
            "series": [{"name": "Сигналы", "unit": f"за {fire.BUCKET_HOURS} ч", "points": points}],
            "markerAt": at.isoformat(),
        },
    )


def timeline_block(features: FeatureVector, facts: Facts, at: datetime) -> Block:
    events: list[dict[str, Any]] = []
    if features.get("signals_24h", 0.0) > 0:
        hours = float(features.get("hours_since_signal", 0.0))
        events.append(
            {
                "at": (at - timedelta(hours=hours)).isoformat(),
                "title": "Последний сигнал пожарной сигнализации",
                "kind": "alarm",
                "note": "в пачке: обход или сбой линии" if facts.burst_only else "вне пачки",
            }
        )
    if facts.sensor_cooling or facts.object_cooling:
        events.append(
            {
                "at": at.isoformat(),
                "title": "Период охлаждения",
                "kind": "info",
                "note": f"Осталось {facts.cooling_days_left} сут. Уровень понижен на ступень",
            }
        )
    events.append(
        {
            "at": at.isoformat(),
            "title": "Прогноз рассчитан",
            "kind": "forecast",
            "note": "Окно наблюдения 24 часа до расчёта",
        }
    )
    events.sort(key=lambda event: str(event["at"]), reverse=True)
    return Block(type="timeline", title="События пикета", data={"events": events})


class FireRisk:
    """Предиктор направления. Спецификация §7, ADR 0016."""

    code = DIRECTION

    @staticmethod
    def probability_of(facts: Facts, level: str) -> float:
        if not facts.signal and facts.gas_pct < 5.0:
            return QUIET_NEXT_DAY_RATE
        return NEXT_DAY_SIGNAL_RATE[level]

    def applies(self, conn: Connection, facility_id: str) -> bool:
        """Отвечает, стоит ли на участке пожарный или газовый датчик. ADR 0018.

        Прогноз ставится на участок. Пикет с родителем-участком свой прогноз
        не получает: его датчики считает участок.
        """
        parent = conn.execute(
            text("SELECT parent_id FROM facility WHERE id = :facility_id"),
            {"facility_id": facility_id},
        ).scalar()
        if parent is not None:
            return False
        row = conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM sensor WHERE facility_id = ANY(:unit)
                      AND sensor_type = ANY(:types)
                ) OR EXISTS (
                    SELECT 1 FROM alarm_event WHERE facility_id = ANY(:unit)
                      AND alarm_type = ANY(:signals)
                )
                """
            ),
            {
                "unit": fire.members(conn, facility_id),
                "types": [*fire.FIRE_SENSOR_TYPES, fire.SENSOR_METHANE],
                "signals": list(fire.SIGNAL_TYPES),
            },
        ).scalar()
        return bool(row)

    def build_features(self, ctx: FeatureContext) -> FeatureVector:
        return fire.to_vector(fire.episode(ctx.conn, ctx.facility_id, ctx.at))

    def incident(self, features: FeatureVector, at: datetime) -> datetime | None:
        """Начало эпизода: первый сигнал вне пачки в окне. ADR 0017.

        Пачка, газ без сигнала и тишина происшествием не считаются: их прогноз
        живёт одной карточкой на московские сутки.
        """
        del at
        epoch = features.get("first_signal_epoch") or 0.0
        return datetime.fromtimestamp(epoch, UTC) if epoch > 0 else None

    def live_blocks(self, conn: Connection, facility_id: str, at: datetime) -> list[Block]:
        """Датчики участка, температура и сигналы за сутки. ADR 0018."""
        return fire_live.blocks(conn, facility_id, at)

    def level(self, features: FeatureVector, probability: float) -> str:
        del probability
        return assess(fire.from_vector(features)).level

    def predict(self, features: FeatureVector) -> float:
        facts = fire.from_vector(features)
        return self.probability_of(facts, assess(facts).level)

    def summary(self, features: FeatureVector, probability: float) -> str:
        del probability
        facts = fire.from_vector(features)
        result = assess(facts)
        if not facts.signal and not result.reasons:
            return "Пожарный риск: сигналов за сутки нет"
        head = {
            "CRITICAL": "признаки пожара",
            "HIGH": "похоже на развитие пожара",
            "MEDIUM": "одиночный сигнал без обхода",
            "LOW": "фон",
        }[result.level]
        return f"Пожарный риск: {head}. " + "; ".join(result.reasons[:2])

    def explain(self, features: FeatureVector, at: datetime | None = None) -> list[Block]:
        moment = at if at is not None else datetime.now(UTC).replace(microsecond=0)
        facts = fire.from_vector(features)
        level = assess(facts).level
        return [
            factors_block(facts),
            rules_block(features, facts, level),
            timeseries_block(features, moment),
            timeline_block(features, facts, moment),
        ]

    def suggest_work_type(self, features: FeatureVector, probability: float) -> str:
        """Тип работ по признакам, а не по вероятности."""
        del probability
        facts = fire.from_vector(features)
        if facts.gas_pct >= 1.0:
            return "Проверка вентиляции"
        if facts.heat_independent or facts.temp_rise_c >= TEMP_RISE_C:
            return "Тепловизионное обследование"
        if facts.spread:
            return "Осмотр камеры и кабельных линий"
        return "Осмотр и чистка извещателя"

    def label_rule(self, conn: Connection, facility_id: str, window: Window) -> bool:
        """Метка ADR 0014: сигнал пожарной сигнализации вне пачки в окне.

        Окно открыто слева: момент расчёта в метку не входит. Метка участка это
        сигнал любого его пикета.
        """
        unit = fire.members(conn, facility_id)
        rows = conn.execute(
            text(
                """
                SELECT occurred_at FROM alarm_event
                WHERE facility_id = ANY(:unit) AND alarm_type = ANY(:types)
                  AND occurred_at > :start AND occurred_at <= :end
                ORDER BY occurred_at
                """
            ),
            {
                "unit": unit,
                "types": list(fire.SIGNAL_TYPES),
                "start": window.start,
                "end": window.end,
            },
        ).all()
        for (moment,) in rows:
            signals, _, _ = fire.signals_of(conn, facility_id, moment + timedelta(minutes=61))
            own = [s for s in signals if s.facility_id in unit and s.at == moment]
            if own and not fire.is_burst(own[0], signals):
                return True
        return False


register(FireRisk())
