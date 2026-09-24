"""Реестр направлений прогнозирования.

Добавить направление — значит добавить сюда одну запись. Ни роутеры, ни схемы,
ни миграции при этом не меняются. Это правило спецификации §5 и предмет теста
`tests/test_meta_flexibility.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import config


@dataclass(frozen=True)
class Reason:
    """Фактическая причина. Диспетчер выбирает её при закрытии заявки."""

    code: str
    label: str


@dataclass(frozen=True)
class Direction:
    code: str
    label: str
    short_label: str
    accent: str
    min_horizon_hours: int
    reasons: tuple[Reason, ...]
    work_types: tuple[str, ...]
    # Нижние границы уровней риска этого направления, по возрастанию. Пустое
    # значение означает общие пороги `catalog.RISK_LEVELS`. ADR 0004.
    level_thresholds: tuple[tuple[str, float], ...] = ()
    # Порог автозаявки и множитель срока по умолчанию. Спецификация §8.
    order_levels: tuple[str, ...] = ("HIGH", "CRITICAL")
    due_factor: float = 0.5
    # Сколько отклонение держит объект от новых заявок того же уровня.
    reject_cooldown_hours: int = 168
    enabled_by_default: bool = True

    @property
    def reasons_ref(self) -> str:
        """Ключ справочника причин в `/meta`. Совпадает с кодом направления."""
        return self.code


SENSOR_FAILURE = Direction(
    code="SENSOR_FAILURE",
    label="Отказ датчика",
    short_label="ОД",
    accent="#6f8fb5",
    min_horizon_hours=24,
    reasons=(
        Reason("SENSOR_DEGRADED", "Деградация чувствительного элемента"),
        Reason("WIRING", "Обрыв или окисление шлейфа"),
        Reason("POWER", "Неисправность питания"),
        Reason("CONTAMINATION", "Загрязнение камеры датчика"),
        Reason("FALSE_POSITIVE", "Ложное срабатывание, датчик исправен"),
        Reason("NO_DEFECT", "Дефект не подтверждён"),
    ),
    work_types=(
        "Диагностика датчика",
        "Замена датчика",
        "Чистка и калибровка",
        "Ремонт шлейфа",
    ),
)

FIRE_RISK = Direction(
    code="FIRE_RISK",
    label="Пожарный риск",
    short_label="ПР",
    accent="#c96a3a",
    min_horizon_hours=24,
    reasons=(
        Reason("HOT_WORKS", "Нарушение при огневых работах"),
        Reason("CABLE_OVERHEAT", "Перегрев кабельной линии"),
        Reason("VENTILATION", "Недостаточная вентиляция участка"),
        Reason("DEBRIS", "Горючий мусор в камере"),
        Reason("FALSE_POSITIVE", "Ложное срабатывание, риска не было"),
        Reason("NO_DEFECT", "Дефект не подтверждён"),
    ),
    work_types=(
        "Осмотр камеры и кабельных линий",
        "Тепловизионное обследование",
        "Проверка вентиляции",
        "Уборка горючих материалов",
    ),
)

UNAUTHORIZED_ACCESS = Direction(
    code="UNAUTHORIZED_ACCESS",
    label="Несанкционированный доступ",
    short_label="НД",
    accent="#a884c0",
    min_horizon_hours=24,
    reasons=(
        Reason("INTRUSION", "Проникновение подтверждено"),
        Reason("NO_PERMIT_WORKS", "Работы без оформленного допуска"),
        Reason("LOCK_DAMAGED", "Повреждён запорный механизм"),
        Reason("SENSOR_NOISE", "Срабатывание из-за помех датчика"),
        Reason("FALSE_POSITIVE", "Ложное срабатывание"),
        Reason("NO_DEFECT", "Нарушений не выявлено"),
    ),
    work_types=(
        "Осмотр люка и запорного механизма",
        "Замена замка",
        "Проверка СКУД",
        "Выезд группы реагирования",
    ),
    # База события 0,944 %, поэтому общие пороги 0,3, 0,55 и 0,78 держат всё
    # направление в уровне LOW.
    #
    # Границы стоят на сырой шкале бустера. Калибратор отклонён: изотония
    # училась на проверочном отрезке с базой 1,2284 % и проигрывала на тесте с
    # базой 0,9438 % (счёт Брайера 0,009171 против 0,009127). Файла
    # `calibration.joblib` больше нет, `predict` отдаёт сырую вероятность.
    #
    # Источник чисел один: замер работающей модели рядом с ней,
    # `ARTIFACTS_DIR/unauthorized_access/metrics.json`, ключ `decision.levels`.
    # Ключ `decision.scale` называет шкалу, и она обязана совпадать с этой.
    # Сейчас работает суточная модель (`ml/access/out/daily_metrics.json`):
    # граница HIGH равна порогу равной полноты с наивной планкой, 3,41 наряда в
    # сутки при точности 0,298. Разбор — ADR 0002 и ADR 0004.
    level_thresholds=(("MEDIUM", 0.00937), ("HIGH", 0.202105), ("CRITICAL", 0.3)),
)

# Пятое направление. Выключено по умолчанию: это тест на гибкость с обеих
# сторон. Включается переменной ENABLED_DIRECTIONS.
FLOOD_RISK = Direction(
    code="FLOOD_RISK",
    label="Риск подтопления",
    short_label="РП",
    accent="#4a7fb5",
    min_horizon_hours=24,
    reasons=(
        Reason("GROUNDWATER", "Поступление грунтовых вод"),
        Reason("PUMP_FAILURE", "Отказ откачивающего насоса"),
        Reason("PIPE_LEAK", "Течь водопроводной сети"),
        Reason("NO_DEFECT", "Подтопления не выявлено"),
    ),
    work_types=("Проверка приямка и насоса", "Гидроизоляция", "Откачка воды"),
    enabled_by_default=False,
    # База события на проверочном отрезке 2,12 %, поэтому общие пороги 0,3,
    # 0,55 и 0,78 держали бы почти всё направление в уровне LOW.
    #
    # Границы стоят на сырой шкале суточного бустера, калибровки нет. HIGH это
    # порог равной полноты с наивной планкой, найденный на проверочном отрезке,
    # а не на отложенной выборке. Источник чисел один:
    # `ARTIFACTS_DIR/flood_risk/metrics.json`, ключ `decision.levels`.
    # Разбор — ADR 0009.
    level_thresholds=(("MEDIUM", 0.021203), ("HIGH", 0.143891), ("CRITICAL", 0.3)),
)

REGISTRY: tuple[Direction, ...] = (
    SENSOR_FAILURE,
    FIRE_RISK,
    UNAUTHORIZED_ACCESS,
    FLOOD_RISK,
)


def active() -> tuple[Direction, ...]:
    """Направления, включённые в этой установке.

    Переменная ENABLED_DIRECTIONS задаёт список кодов через запятую и
    перекрывает умолчания. Пустое значение означает умолчания.
    """
    chosen = config.enabled_directions
    if not chosen:
        return tuple(item for item in REGISTRY if item.enabled_by_default)
    return tuple(item for item in REGISTRY if item.code in chosen)


def by_code(code: str) -> Direction | None:
    return next((item for item in REGISTRY if item.code == code), None)


# Реестр не должен содержать двух записей с одним кодом.
_codes = [item.code for item in REGISTRY]
if len(set(_codes)) != len(_codes):
    raise ValueError(f"направления с одинаковым кодом: {_codes}")
