"""Справочники интерфейса: уровни риска, статусы, районы, колонки, виджеты.

Состав экрана приходит из `/meta`, а не из фронта. Менять состав экрана —
значит менять этот файл. Спецификация §5 правило 4.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.meta.directions import REGISTRY, Direction, Reason

PREDICTION_SCOPE = "prediction"
ORDER_SCOPE = "order"


@dataclass(frozen=True)
class RiskLevel:
    code: str
    label: str
    color_var: str
    order: int
    # Нижняя граница вероятности. Верхнюю задаёт следующий уровень.
    min_probability: float


@dataclass(frozen=True)
class Status:
    code: str
    label: str
    scope: str
    color_var: str
    terminal: bool = False


@dataclass(frozen=True)
class District:
    code: str
    label: str


# Пороги совпадают с моками фронта. Спецификация §6.
RISK_LEVELS: tuple[RiskLevel, ...] = (
    RiskLevel("LOW", "Низкий", "--risk-low", 1, 0.0),
    RiskLevel("MEDIUM", "Средний", "--risk-medium", 2, 0.3),
    RiskLevel("HIGH", "Высокий", "--risk-high", 3, 0.55),
    RiskLevel("CRITICAL", "Критический", "--risk-critical", 4, 0.78),
)

# Цвет статуса назначает сервер. Гамма читается по стадии работы: внимание —
# ждёт диспетчера, прогресс — идёт, done — закончено, muted — снято. Брать
# токен из палитры состояний, а не из палитры риска.
STATUSES: tuple[Status, ...] = (
    Status("NEW", "Новый", PREDICTION_SCOPE, "--state-attention"),
    Status("IN_REVIEW", "На рассмотрении", PREDICTION_SCOPE, "--state-progress"),
    Status("ORDER_CONFIRMED", "Заявка подтверждена", PREDICTION_SCOPE, "--state-progress"),
    Status("REJECTED", "Отклонён", PREDICTION_SCOPE, "--state-muted", terminal=True),
    Status("CLOSED", "Закрыт", PREDICTION_SCOPE, "--state-done", terminal=True),
    Status("AUTO_CREATED", "Создана автоматически", ORDER_SCOPE, "--state-attention"),
    Status("CONFIRMED", "Подтверждена", ORDER_SCOPE, "--state-progress"),
    Status("IN_PROGRESS", "В работе", ORDER_SCOPE, "--state-progress"),
    Status("REJECTED", "Отклонена", ORDER_SCOPE, "--state-muted", terminal=True),
    Status("DONE", "Выполнена", ORDER_SCOPE, "--state-done", terminal=True),
)

DISTRICTS: tuple[District, ...] = (
    District("CAO", "Центральный"),
    District("SAO", "Северный"),
    District("SVAO", "Северо-Восточный"),
    District("SZAO", "Северо-Западный"),
    District("VAO", "Восточный"),
    District("YUAO", "Южный"),
    District("YUVAO", "Юго-Восточный"),
    District("YUZAO", "Юго-Западный"),
    District("ZAO", "Западный"),
)

JOURNAL_COLUMNS: tuple[str, ...] = (
    "risk",
    "computedAt",
    "direction",
    "facility",
    "summary",
    "probability",
    "horizon",
    "status",
    "order",
)

ORDER_COLUMNS: tuple[str, ...] = (
    "number",
    "facility",
    "workType",
    "dueAt",
    "orderStatus",
    "prediction",
)

DASHBOARD_WIDGETS: tuple[str, ...] = (
    "risk-counters",
    "model-metrics",
    "pipeline-health",
    "direction-split",
    "top-risks",
    "order-counters",
)

REJECTION_REASONS_REF = "rejection"

REJECTION_REASONS: tuple[Reason, ...] = (
    Reason("KNOWN_ISSUE", "Известная особенность объекта"),
    Reason("PLANNED_WORKS", "На объекте идут плановые работы"),
    Reason("DUPLICATE", "Дубль ранее обработанного прогноза"),
    Reason("LOW_PRIORITY", "Низкий приоритет, отложено"),
    Reason("MODEL_ERROR", "Ошибка модели"),
)


# Сколько держится мьют после отклонения, по причине отклонения. Диспетчер
# может назвать свой срок, эти числа — только подсказка по умолчанию.
# Длина следует из смысла причины: дубль живёт сутки, особенность объекта —
# месяц, потому что она никуда не денется.
REJECTION_MUTE_HOURS: dict[str, int] = {
    "KNOWN_ISSUE": 720,
    "PLANNED_WORKS": 168,
    "DUPLICATE": 24,
    "LOW_PRIORITY": 168,
    "MODEL_ERROR": 168,
}


def mute_hours(reason: str | None, fallback: int) -> int:
    """Отдаёт длину мьюта по причине отклонения."""
    if reason is None:
        return fallback
    return REJECTION_MUTE_HOURS.get(reason, fallback)


def level_for(probability: float, direction: Direction | None = None) -> str:
    """Отдаёт код уровня риска по вероятности.

    Направление названо и держит свои границы значит счёт идёт по ним.
    Иначе счёт идёт по общим порогам `RISK_LEVELS`. Разбор — ADR 0004.

    Границы направления стоят на той же шкале, что и `prediction.probability`.
    Направление с базой события ниже процента не достаёт до общих порогов ни
    одним прогнозом, и шкала уровней на нём сжимается в один уровень `LOW`.
    """
    bands = direction.level_thresholds if direction is not None else ()
    if not bands:
        bands = tuple((level.code, level.min_probability) for level in RISK_LEVELS)

    code = RISK_LEVELS[0].code
    for level_code, lower_bound in bands:
        if probability >= lower_bound:
            code = level_code
    return code


def level_order(code: str) -> int:
    """Отдаёт порядковый номер уровня. Неизвестный уровень считается низшим."""
    return next((item.order for item in RISK_LEVELS if item.code == code), 0)


# Границы направления обязаны называть уровень из `RISK_LEVELS` и идти по
# возрастанию. Опечатка в границах даёт диспетчеру уровень, которого нет в
# `/meta`, поэтому она обязана ронять сервис на старте, а не на прогнозе.
for _direction in REGISTRY:
    _known = {level.code for level in RISK_LEVELS}
    _unknown = [code for code, _ in _direction.level_thresholds if code not in _known]
    if _unknown:
        raise ValueError(f"направление {_direction.code} называет уровни вне RISK_LEVELS: {_unknown}")
    _bounds = [value for _, value in _direction.level_thresholds]
    if _bounds != sorted(_bounds):
        raise ValueError(f"границы уровней направления {_direction.code} не возрастают: {_bounds}")


def statuses_for(scope: str) -> tuple[Status, ...]:
    return tuple(status for status in STATUSES if status.scope == scope)


def is_terminal(scope: str, code: str) -> bool:
    return any(s.terminal for s in STATUSES if s.scope == scope and s.code == code)
