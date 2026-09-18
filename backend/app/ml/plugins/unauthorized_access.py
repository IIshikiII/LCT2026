"""Предиктор направления «несанкционированный доступ». Спецификация §7.

Модель — бустер LightGBM из `ml/access/train.py`, читается через
`app.ml.tracking.load_model("UNAUTHORIZED_ACCESS")`. Признаки строит
`app.features.access.build_features` (T12), имена и порядок совпадают с
`ml/access/features.py::FEATURE_COLUMNS` буква в букву.

## Порядок столбцов берёт модель, а не список в коде

`predict` и `explain` не держат свой список имён признаков: они читают
`model.feature_name()`, порядок, с которым бустер обучен (`lgb.Dataset(...,
feature_name=features.FEATURE_COLUMNS)` в `train.py`). Список признаков жил бы
тогда в трёх местах и разошёлся бы на первой же правке `app/features/access.py`.

## Ленивый импорт lightgbm и shap

Реестр `app.ml.registry.load_plugins()` импортирует этот модуль при каждом
обращении к направлению, в том числе в тестах, которые ничего не предсказывают
(`test_ml_registry.py`). `01-dependencies.md` держит LightGBM только в наборе
`research`, набор `ml` — на одном scikit-learn. Модуль обязан импортироваться
без LightGBM и SHAP, иначе регистрация плагина роняет весь реестр. Поэтому оба
импорта отложены внутрь функций, тем же приёмом, что `app/ml/tracking.py`
откладывает `joblib` и `mlflow`. Перенос LightGBM и SHAP в набор `ml` — отдельная
задача, `loop/BACKLOG.md`.

## Модели нет — предсказания нет

`load_model` отдаёт `None` без падения, когда файла нет ни в реестре, ни на
диске. `predict` и `explain` в этом случае поднимают `RuntimeError` раньше,
чем дойдут до `import lightgbm` или `import shap`: отсутствие модели обязано
дать понятный отказ, а не `ModuleNotFoundError` из пустого каталога
артефактов. Решение, пропускать ли направление молча при таком отказе, живёт
в конвейере (T17) — плагин обещает только чистое исключение, а не тихий
прогноз из ничего. Тест `test_plugin_access_no_model.py` гоняет этот путь без
подмены `load_model`, `lightgbm` и `shap`.

## Объяснение строится из вектора признаков, а не из базы

Протокол передаёт в `explain` вектор признаков и момент расчёта. Подключения к
базе он не передаёт, и это решение, а не недосмотр: конвейер кладёт вектор в
`prediction.features`, поэтому объяснение повторяется по сохранённому прогнозу
через полгода без обращения к журналу тревог.

Отсюда форма двух блоков с осью времени.

- `timeseries` показывает среднюю частоту тревог по четырём непересекающимся
  окнам. Вектор держит накопленные счётчики `n_alarms_1h`, `n_alarms_24h`,
  `n_alarms_168h` и `n_alarms_720h`, то есть вложенные окна. Разность соседних
  счётчиков даёт непересекающееся окно, деление на его длину даёт тревог в
  сутки. Точка ставится в середину окна.
- `timeline` показывает события, которые вектор называет смещением от момента
  расчёта: последнюю тревогу доступа, последнюю тревогу вне режима охраны,
  цепочку «дверь, объёмный датчик, движение» и снятие с охраны.

Почасовой истории в векторе нет, и блок её не рисует. Ложная детализация хуже
честной грубой шкалы.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.features.access import ACCESS_ALARM_TYPES, SECURITY_ARMED, SECURITY_DISARMED
from app.ml.protocol import Block, FeatureContext, FeatureVector, Window
from app.ml.registry import register
from app.ml.tracking import load_model

DIRECTION = "UNAUTHORIZED_ACCESS"

# Человеческая подпись признака для блока `factors`. Признак без записи здесь
# показывается своим программным именем — это деградация, а не ошибка.
FEATURE_LABELS: dict[str, str] = {
    "n_alarms_1h": "Срабатываний за последний час",
    "n_alarms_24h": "Срабатываний за 24 часа",
    "n_alarms_168h": "Срабатываний за 7 суток",
    "n_alarms_720h": "Срабатываний за 30 суток",
    "alarm_hour_share_720h": "Доля часов с тревогой за 30 суток",
    "hours_since_last_alarm": "Часов с последней тревоги",
    "night_share": "Доля тревог в ночное время",
    "hour_of_day": "Час суток",
    "day_of_week": "День недели",
    "month": "Месяц",
    "is_weekend": "Выходной день",
    "neighbor_channels_1h": "Соседних каналов пикета в тревоге",
    "is_disarmed": "Участок снят с охраны",
    "has_access_sequence": "Цепочка «дверь — датчик — движение»",
    "n_armed_alarms_24h": "Тревог вне режима охраны за 24 часа",
    "n_armed_alarms_168h": "Тревог вне режима охраны за 7 суток",
    "hours_since_last_armed_alarm": "Часов с последней тревоги вне режима охраны",
}

_MODEL_MISSING = (
    "модель направления UNAUTHORIZED_ACCESS не найдена ни в реестре, ни в "
    "ARTIFACTS_DIR; прогнозов по направлению нет, пока модель не обучена"
)


def _model() -> Any | None:
    return load_model(DIRECTION)


def _row(model: Any, features: FeatureVector) -> list[list[float]]:
    return [[features[name] for name in model.feature_name()]]


def shap_weight(contribution: float) -> float:
    """Приводит вклад SHAP к диапазону от минус 1 до 1.

    Модель даёт вклад в логарифме шансов, и он ничем не ограничен. Карточка
    диспетчера требует числа от минус 1 до 1. Правило приведения одно:

        r = exp(вклад)
        вес = (r - 1) / (r + 1)

    Величина `r` показывает, во сколько раз признак умножает шанс события.
    Признак удваивает шанс значит вес равен 0,33. Признак утраивает шанс
    значит вес равен 0,5. Признак делит шанс надвое значит вес равен минус
    0,33. Тождество `(e^x - 1) / (e^x + 1) = tanh(x / 2)` даёт ту же величину
    без переполнения на больших вкладах, поэтому в коде стоит `tanh`.

    Правило держит четыре свойства. Знак сохраняется. Порядок сохраняется,
    потому что функция строго возрастает. Значение лежит внутри диапазона.
    Масштаб один во всех карточках: он не зависит ни от набора признаков
    строки, ни от переобучения модели. Замер на отложенной выборке 2026 года
    показывает, что диапазон занят делом: у прогнозов выше порога 0,035251 вес
    главного фактора лежит между 0,51 и 0,96 при медиане 0,69, а наибольший
    вклад модели равен 4,0 логита, то есть весу 0,96.

    ## Почему сумма весов не равна вероятности

    Диспетчер складывает полосы глазом, и сумма не сойдётся с вероятностью
    прогноза. Это не ошибка блока. Причин две.

    1. Вклады складываются в логарифме шансов, а не в вероятности. Вероятность
       равна `сигмоида(фон + сумма вкладов)`. Фон это средний риск направления,
       и он в блоке не показан: это точка отсчёта, а не фактор.
    2. Вес это нелинейная функция вклада. Приведение к диапазону гнёт шкалу,
       поэтому веса не складываются даже между собой.

    Полосы отвечают на вопрос «что двигало прогноз и в какую сторону». На
    вопрос «из чего сложилось число» они не отвечают.
    """
    return math.tanh(contribution / 2.0)


def factors_block(
    names: Sequence[str],
    contributions: Sequence[float],
    features: FeatureVector,
) -> Block:
    """Собирает блок `factors`. Порядок строк по модулю веса, сверху сильнейший."""
    ranked = sorted(zip(names, contributions, strict=True), key=lambda pair: -abs(pair[1]))
    items = [
        {
            "label": FEATURE_LABELS.get(name, name),
            "weight": round(shap_weight(float(value)), 6),
            "value": f"{features.get(name, 0.0):g}",
        }
        for name, value in ranked
    ]
    return Block(type="factors", title="Почему модель так решила", data={"items": items})


def _rate_points(
    features: FeatureVector,
    at: datetime,
    column: str,
    windows: Sequence[tuple[int, int]],
) -> list[dict[str, Any]]:
    """Считает среднюю частоту тревог по непересекающимся окнам, тревог в сутки.

    Окно задаётся парой «часов назад»: `(720, 168)` это отрезок от 720 до 168
    часов назад. Накопленный счётчик окна `end` вычитается из счётчика окна
    `start`. Счётчик `0` часов равен нулю по определению.
    """
    points: list[dict[str, Any]] = []
    for start, end in windows:
        older = float(features.get(column.format(hours=start), 0.0))
        newer = float(features.get(column.format(hours=end), 0.0)) if end else 0.0
        count = max(0.0, older - newer)
        days = (start - end) / 24.0
        middle = at - timedelta(hours=(start + end) / 2.0)
        points.append({"t": middle.isoformat(), "v": round(count / days, 3)})
    return points


def timeseries_block(features: FeatureVector, at: datetime) -> Block:
    """Собирает блок `timeseries`: частота тревог по окнам наблюдения."""
    return Block(
        type="timeseries",
        title="Тревоги доступа по окнам наблюдения",
        data={
            "series": [
                {
                    "name": "Тревоги доступа",
                    "unit": "тревог в сутки",
                    "points": _rate_points(
                        features, at, "n_alarms_{hours}h", ((720, 168), (168, 24), (24, 1), (1, 0))
                    ),
                },
                {
                    "name": "Тревоги вне режима охраны",
                    "unit": "тревог в сутки",
                    "points": _rate_points(
                        features, at, "n_armed_alarms_{hours}h", ((168, 24), (24, 0))
                    ),
                },
            ],
            "markerAt": at.isoformat(),
        },
    )


def timeline_block(features: FeatureVector, at: datetime) -> Block:
    """Собирает блок `timeline`: события, которые вектор признаков ещё помнит.

    Единица без истории тревог получает `hours_since_last_alarm` равным нулю,
    как и единица с тревогой в текущий час (`app/features/access.py`). Одно от
    другого отличают счётчики окон: все нули значит истории нет, и события
    «последняя тревога» в ленте не будет.
    """
    events: list[dict[str, Any]] = []
    counters = ("n_alarms_1h", "n_alarms_24h", "n_alarms_168h", "n_alarms_720h")
    armed_counters = ("n_armed_alarms_24h", "n_armed_alarms_168h")

    if any(features.get(name, 0.0) > 0 for name in (*counters, "hours_since_last_alarm")):
        hours = float(features.get("hours_since_last_alarm", 0.0))
        events.append(
            {
                "at": (at - timedelta(hours=hours)).isoformat(),
                "title": "Последняя тревога доступа",
                "kind": "alarm",
                "note": (
                    f"За сутки {features.get('n_alarms_24h', 0.0):g}, "
                    f"за 30 суток {features.get('n_alarms_720h', 0.0):g}"
                ),
            }
        )

    if any(
        features.get(name, 0.0) > 0 for name in (*armed_counters, "hours_since_last_armed_alarm")
    ):
        hours = float(features.get("hours_since_last_armed_alarm", 0.0))
        events.append(
            {
                "at": (at - timedelta(hours=hours)).isoformat(),
                "title": "Последняя тревога вне режима охраны",
                "kind": "alarm",
                "note": "Такие тревоги весят в модели больше всех остальных признаков",
            }
        )

    if features.get("has_access_sequence", 0.0) >= 1.0:
        events.append(
            {
                "at": at.isoformat(),
                "title": "Цепочка «дверь, объёмный датчик, движение»",
                "kind": "access",
                "note": "Цепочка замкнулась в последние 15 минут",
            }
        )

    if features.get("is_disarmed", 0.0) >= 1.0:
        events.append(
            {
                "at": at.isoformat(),
                "title": "Участок снят с охраны",
                "kind": "access",
                "note": "Тревоги внутри окна снятия модель за событие не считает",
            }
        )

    events.append(
        {
            "at": at.isoformat(),
            "title": "Прогноз рассчитан",
            "kind": "forecast",
            "note": "Признаки построены на этот момент",
        }
    )

    events.sort(key=lambda event: str(event["at"]), reverse=True)
    return Block(type="timeline", title="События объекта", data={"events": events})


class UnauthorizedAccess:
    """Предиктор направления. Спецификация §7, `docs/08-ml-plugin.md`."""

    code = DIRECTION

    def build_features(self, ctx: FeatureContext) -> FeatureVector:
        from app.features.access import build_features

        return build_features(ctx)

    def predict(self, features: FeatureVector) -> float:
        model = _model()
        if model is None:
            raise RuntimeError(_MODEL_MISSING)
        return float(model.predict(_row(model, features))[0])

    def explain(self, features: FeatureVector, at: datetime | None = None) -> list[Block]:
        """Отдаёт три блока карточки: `factors`, `timeseries` и `timeline`.

        Вклад каждого признака считает `shap.TreeExplainer` на бустере. Вклад
        приходит в логарифме шансов, и `shap_weight` приводит его к диапазону
        от минус 1 до 1 по правилу «относительная поправка шанса». Правило и
        причина, по которой сумма весов не равна вероятности, записаны в
        докстринге `shap_weight`.

        Аргумент `at` называет момент, на который построены признаки. Блоки с
        осью времени ставят на неё абсолютные метки, а вектор признаков держит
        только смещения. Конвейер передаёт `ctx.at`, разбор старого прогноза
        передаёт `prediction.computed_at`. Пустое значение значит «сейчас», и
        тогда ось старого прогноза уезжает на его возраст.
        """
        model = _model()
        if model is None:
            raise RuntimeError(_MODEL_MISSING)

        import numpy as np
        import shap

        moment = at if at is not None else datetime.now(UTC).replace(microsecond=0)
        explainer = shap.TreeExplainer(model)
        # `shap_values` требует массив со свойством `shape`. Список списков он
        # не принимает: разбор входа читает `X.shape[1]` напрямую.
        raw = explainer.shap_values(np.asarray(_row(model, features), dtype=float))
        # Для двоичной задачи SHAP отдаёт либо матрицу вкладов, либо список
        # матриц по классу. Версия библиотеки меняла это дважды, поэтому
        # обрабатываются обе формы, а не та, что стоит сегодня.
        if isinstance(raw, list):
            raw = raw[-1]
        contributions = [float(value) for value in raw[0]]

        return [
            factors_block(model.feature_name(), contributions, features),
            timeseries_block(features, moment),
            timeline_block(features, moment),
        ]

    def suggest_work_type(self, features: FeatureVector, probability: float) -> str:
        """Называет тип работ по непосредственности сигнала, не по вероятности.

        Порядок проверок от самого прямого признака проникновения к самому
        общему. Цепочка «дверь — датчик — движение» это готовый сценарий
        прохода: реагирует группа. Тревога в последний час без цепочки — это
        ещё не подтверждённый проход, сначала смотрит осмотр. Снятая охрана
        без свежей тревоги — повод проверить исправность СКУД, а не выезжать.
        Всё остальное — плановая замена замка по накопленному риску.
        """
        if features.get("has_access_sequence", 0.0) >= 1.0:
            return "Выезд группы реагирования"
        if features.get("n_alarms_1h", 0.0) >= 1.0:
            return "Осмотр люка и запорного механизма"
        if features.get("is_disarmed", 0.0) >= 1.0:
            return "Проверка СКУД"
        return "Замена замка"

    def label_rule(self, conn: Connection, facility_id: str, window: Window) -> bool:
        """Отвечает, наступило ли проникновение на объекте в окне.

        Повторяет метку обучения (ADR 0001, версия Б, закреплена в T04):
        событие — тревога доступа (`ACCESS_ALARM_TYPES`) вне окна «Снято с
        охраны». Окно открыто слева и закрыто справа, как в определении
        метки: `window.start` — момент расчёта, в метку не входит.
        """
        row = conn.execute(
            text(
                """
                WITH toggle AS (
                    SELECT occurred_at AS ts, alarm_type,
                           lead(occurred_at) OVER (ORDER BY occurred_at) AS next_ts
                    FROM alarm_event
                    WHERE facility_id = :facility_id
                      AND alarm_type IN (:armed, :disarmed)
                      AND occurred_at <= :end
                ),
                window_ AS (
                    SELECT ts AS win_start, coalesce(next_ts, :end) AS win_end
                    FROM toggle
                    WHERE alarm_type = :disarmed
                )
                SELECT 1 FROM alarm_event a
                WHERE a.facility_id = :facility_id AND a.alarm_type = ANY(:types)
                  AND a.occurred_at > :start AND a.occurred_at <= :end
                  AND NOT EXISTS (
                      SELECT 1 FROM window_ w
                      WHERE a.occurred_at >= w.win_start AND a.occurred_at < w.win_end
                  )
                LIMIT 1
                """
            ),
            {
                "facility_id": facility_id,
                "types": list(ACCESS_ALARM_TYPES),
                "armed": SECURITY_ARMED,
                "disarmed": SECURITY_DISARMED,
                "start": window.start,
                "end": window.end,
            },
        ).fetchone()
        return row is not None


register(UnauthorizedAccess())
