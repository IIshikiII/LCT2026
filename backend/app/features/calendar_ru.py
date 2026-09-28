"""Календарь России для признаков модели.

Повторяет `ml/access/calendar_ru.py` в части, которая нужна прогнозу. Обучение
строит таблицу на все даты сразу, сервису нужен ответ про одну дату, поэтому
здесь функции, а не таблица.

Правила обязаны совпадать с обучением до дня. Расхождение означает, что модель
училась на одном календаре, а работает на другом, и признак начинает врать.
Совпадение проверяет `tests/test_calendar_matches_training.py`.

Переносы выходных сюда не входят: правительство назначает их постановлением на
каждый год, и из правил они не выводятся. Ошибка — единицы дней в году, и она
одинакова в обучении и в работе.
"""

from __future__ import annotations

import datetime as dt

# Постоянные нерабочие дни: месяц и день.
FIXED_HOLIDAYS: frozenset[tuple[int, int]] = frozenset(
    {
        (1, 1),
        (1, 2),
        (1, 3),
        (1, 4),
        (1, 5),
        (1, 6),
        (1, 7),
        (1, 8),
        (2, 23),
        (3, 8),
        (5, 1),
        (5, 9),
        (6, 12),
        (11, 4),
    }
)

# Предел поиска цепочки нерабочих дней. Новогодние каникулы дают восемь дней,
# запас вдвое закрывает любой перенос.
CHAIN_LIMIT = 20


def is_holiday(day: dt.date) -> bool:
    return (day.month, day.day) in FIXED_HOLIDAYS


def is_weekend(day: dt.date) -> bool:
    return day.weekday() >= 5


def is_day_off(day: dt.date) -> bool:
    return is_holiday(day) or is_weekend(day)


def day_off_chain(day: dt.date) -> int:
    """Длина непрерывной цепочки нерабочих дней вокруг `day`. Ноль в будни."""
    if not is_day_off(day):
        return 0
    length = 1
    back = day - dt.timedelta(days=1)
    while is_day_off(back) and length < CHAIN_LIMIT:
        length += 1
        back -= dt.timedelta(days=1)
    forward = day + dt.timedelta(days=1)
    while is_day_off(forward) and length < CHAIN_LIMIT:
        length += 1
        forward += dt.timedelta(days=1)
    return length
