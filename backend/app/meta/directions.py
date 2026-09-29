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
    # Направление без измеренной точности называет себя здесь. Ручка
    # `/metrics/models` отдаёт строку с этим пояснением вместо чисел, и
    # дашборд говорит «не измерена», а не молчит. ADR 0016.
    quality_note: str = ""
    quality_method: str = "offline_holdout"
    # Наивное правило, с которым дашборд сравнивает модель. Его точность и
    # полнота лежат в замере модели рядом с её числами.
    baseline_rule: str = ""
    # Ритм расчёта. ADR 0017. `daily`: модель училась на точке расчёта в
    # полночь, прогноз считается раз в московские сутки первым прогоном после
    # 00:00. `stream`: прогноз смотрит на окно до момента расчёта и
    # пересчитывается каждым прогоном.
    cadence: str = "daily"

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
        Reason("DETECTOR_DIRTY", "Извещатель загрязнён или отсырел"),
        Reason("COMMISSIONING", "Пусконаладка системы"),
        Reason("FALSE_POSITIVE", "Ложное срабатывание, риска не было"),
        Reason("NO_DEFECT", "Дефект не подтверждён"),
    ),
    work_types=(
        "Осмотр камеры и кабельных линий",
        "Тепловизионное обследование",
        "Проверка вентиляции",
        "Уборка горючих материалов",
        "Осмотр и чистка извещателя",
    ),
    # Уровень ставят экспертные правила напрямую (хук `level` плагина), а не
    # пороги по вероятности. Модели нет, ADR 0015 и ADR 0016.
    quality_note=(
        "Экспертные правила. Точность не измерена: подтверждённых пожаров в "
        "выгрузке нет. Её посчитают отметки бригад при закрытии заявок."
    ),
    quality_method="expert_rules",
    # Правила смотрят на 24 часа до момента расчёта, а не на сутки в полночь.
    cadence="stream",
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
    # База события на отложенной выборке 0,818 %, поэтому общие пороги 0,3,
    # 0,55 и 0,78 держат всё направление в уровне LOW.
    #
    # Границы стоят на сырой шкале бустера: калибратор отклонён, файла
    # `calibration.joblib` нет, `predict` отдаёт сырую вероятность.
    #
    # Источник чисел один: замер работающей модели рядом с ней,
    # `ARTIFACTS_DIR/unauthorized_access/metrics.json`, ключ `decision.levels`.
    # Ключ `decision.scale` называет шкалу, и она обязана совпадать с этой.
    # Работает модель участков (`ml/access/out/daily_metrics.json`): граница
    # MEDIUM равна базе, граница HIGH равна порогу равной полноты с наивной
    # планкой, 1,39 наряда в сутки при точности 0,266. Разбор в ADR 0001.
    level_thresholds=(("MEDIUM", 0.008183), ("HIGH", 0.140342), ("CRITICAL", 0.3)),
    baseline_rule="событие на участке было вчера",
)

# Работает по умолчанию с 24 сентября. Модель ADR 0012, в бэкенде с ADR 0013.
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
    # Событие это сутки с водой по разметке ADR 0012, база на отложенном году
    # 1,96 %. Общие пороги 0,3, 0,55 и 0,78 держали бы почти всё в уровне LOW.
    #
    # Границы стоят на сырой шкале ансамбля, калибровки нет. Это запасные
    # границы: в работе HIGH ставит скользящий бюджет тревог за 30 суток
    # (`FloodRisk.level_bands`, ADR 0013). Запасной HIGH это порог равного
    # числа тревог с наивной планкой на трёх проверочных годах. Источник чисел
    # один: `ARTIFACTS_DIR/flood_risk/metrics.json`, ключ `decision.levels`.
    level_thresholds=(("MEDIUM", 0.011471), ("HIGH", 0.092818), ("CRITICAL", 0.3)),
    baseline_rule="вода на пикете была вчера",
)

# Пятое направление. Выключено по умолчанию: это тест на гибкость с обеих
# сторон. Включается переменной ENABLED_DIRECTIONS. Роль перешла сюда от
# подтопления, когда у того появилась модель. Данные направление держат:
# «Температура ниже 3ºC» дала 8 841 запись на 383 каналах.
COLD_RISK = Direction(
    code="COLD_RISK",
    label="Переохлаждение участка",
    short_label="ПО",
    accent="#5aa6a6",
    min_horizon_hours=24,
    reasons=(
        Reason("VENTILATION", "Избыточная вентиляция вентшахты"),
        Reason("HATCH_OPEN", "Открытый люк или дверь"),
        Reason("HEATING_LOSS", "Потеря тепла теплотрассы"),
        Reason("NO_DEFECT", "Переохлаждения не выявлено"),
    ),
    work_types=("Проверка вентшахты", "Закрытие люка", "Осмотр теплотрассы"),
    enabled_by_default=False,
)

REGISTRY: tuple[Direction, ...] = (
    SENSOR_FAILURE,
    FIRE_RISK,
    UNAUTHORIZED_ACCESS,
    FLOOD_RISK,
    COLD_RISK,
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
