"""Предиктор направления «несанкционированный доступ». Спецификация §7.

Модель это бустер LightGBM из `ml/access/train.py`. Единица прогноза это
участок хода вокруг узла входа, окно прогноза это ближайшие сутки (ADR 0001).
Модель читается через `app.ml.tracking.load_model("UNAUTHORIZED_ACCESS")`.
Признаки считает реестр `app.features.registry`, построители лежат в
`app/features/access_daily.py`, правило события лежит в
`app/features/access.py`.

## Порядок столбцов берёт модель, а не список в коде

`build_features`, `predict` и `explain` читают `model.feature_name()`. Своего
списка имён у плагина нет. Новая модель на известных признаках подключается
заменой файла.

## Ленивый импорт lightgbm и shap

Реестр `app.ml.registry.load_plugins()` импортирует этот модуль при каждом
обращении к направлению, в том числе в тестах, которые ничего не предсказывают.
Модуль обязан импортироваться без LightGBM и SHAP, поэтому оба импорта отложены
внутрь функций.

## Модели нет значит прогноза нет

`load_model` отдаёт `None`, когда файла нет ни в реестре, ни на диске. Тогда
`predict` и `explain` поднимают `RuntimeError` раньше, чем дойдут до
`import lightgbm` или `import shap`. Решение пропустить направление принимает
конвейер.

## Объяснение строится из вектора признаков, а не из базы

`explain` получает вектор и момент расчёта, соединения с базой он не получает.
Конвейер кладёт вектор в `prediction.features`, поэтому объяснение повторяется
по сохранённому прогнозу без обращения к журналу тревог. Блоки с осью времени
ставят на неё то, что вектор называет смещением от момента расчёта.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.features import live_charts
from app.features.access import event_ctes, event_params
from app.ml.protocol import Block, FeatureContext, FeatureVector, Window
from app.ml.registry import register
from app.ml.tracking import load_calibrator, load_model

DIRECTION = "UNAUTHORIZED_ACCESS"
# Режим охраны не менялся дольше недели значит его перестали вести.
GUARD_SILENCE_HOURS = 7 * 24

# Человеческая подпись признака для блока `factors`. Признак без записи здесь
# показывается своим программным именем — это деградация, а не ошибка.
FEATURE_LABELS: dict[str, str] = {
    "n_alarms_7d": "Тревог доступа на участке за неделю",
    "alarm_day_share_7d": "Доля суток с тревогой за неделю",
    "alarm_day_share_365d": "Доля суток с тревогой за год",
    "days_since_last_alarm": "Суток с последней тревоги доступа",
    "hours_since_last_alarm": "Часов с последней тревоги доступа",
    "n_armed_7d": "Событий на участке за неделю",
    "n_armed_90d": "Событий на участке за квартал",
    "armed_day_share_30d": "Доля суток с событием за 30 суток",
    "days_since_last_armed": "Суток с последнего события",
    "hours_since_last_armed": "Часов с последнего события",
    "armed_same_weekday_1w": "Событие было ровно неделю назад",
    "n_channels": "Каналов доступа на участке",
    "unit_age_days": "Суток работы участка",
    "disarm_hours_7d": "Часов без охраны за неделю",
    "disarm_share_30d": "Доля времени без охраны за 30 суток",
    "hours_since_guard_change": "Часов со смены режима охраны",
    "obj_alarms_1d": "Тревог на объекте за сутки",
    "obj_alarms_7d": "Тревог на объекте за неделю",
    "obj_units_alarmed_7d": "Участков объекта с тревогой за неделю",
    "net_event_share_1d": "Доля участков сети с событием за сутки",
    "net_event_share_7d": "Доля участков сети с событием за неделю",
    "date_event_share_prev_years": "Доля событий в эту дату в прошлые годы",
    "day_of_week": "День недели",
    "day_of_month": "День месяца",
    "day_of_year": "День года",
    "week_of_year": "Неделя года",
    "is_day_off": "Нерабочий день",
    "day_off_chain": "Длина цепочки нерабочих дней",
}

_MODEL_MISSING = (
    "модель направления UNAUTHORIZED_ACCESS не найдена ни в реестре, ни в "
    "ARTIFACTS_DIR; прогнозов по направлению нет, пока модель не обучена"
)


_CACHE: dict[str, Any] = {}


def _model() -> Any | None:
    """Отдаёт модель направления, читая её с диска один раз на процесс.

    `load_model` ходит в реестр MLflow по сети и разбирает файл модели. Это
    стоит 47 мс, а конвейер зовёт `predict` и `explain` на каждом
    участке, то есть 7 894 раза за прогон. Замер T19a: на чтение модели
    уходило 46 % времени прогона.

    Пустой результат не кладётся в кэш. Модели нет значит она может появиться
    позже, и следующий вызов обязан её увидеть.
    """
    model = _CACHE.get("model")
    if model is None:
        model = load_model(DIRECTION)
        if model is not None:
            _CACHE["model"] = model
    return model


def _calibrator() -> Any | None:
    """Отдаёт калибратор вероятности направления, читая его с диска один раз.

    Файла `calibration.joblib` нет значит направление ещё не откалибровано
    (T28): `predict` тогда отдаёт сырую вероятность бустера, а не падает.
    Как и у модели, пустой результат не кладётся в кэш, чтобы калибратор,
    появившийся после старта процесса, подхватился на следующем вызове.
    """
    calibrator = _CACHE.get("calibrator")
    if calibrator is None:
        calibrator = load_calibrator(DIRECTION)
        if calibrator is not None:
            _CACHE["calibrator"] = calibrator
    return calibrator


def _explainer(model: Any) -> Any:
    """Отдаёт `shap.TreeExplainer` модели, строя его один раз на процесс.

    Построение обходит все деревья ансамбля и стоит 20 мс. Сам расчёт вкладов
    на одной строке стоит доли миллисекунды.
    """
    explainer = _CACHE.get("explainer")
    if explainer is None:
        import shap

        explainer = shap.TreeExplainer(model)
        _CACHE["explainer"] = explainer
    return explainer


def reset_cache() -> None:
    """Забывает модель, объяснитель и признаки сети. Нужен тесту, который
    подменяет модель или данные."""
    from app.features import access_daily

    _CACHE.clear()
    access_daily.reset_cache()


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
    строки, ни от переобучения модели.

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


_FACTOR_WEIGHT_FLOOR = 0.02
_FACTOR_NOTE = "Полоса вправо повышает риск, влево снижает, длина показывает силу влияния."


def _shown(value: float) -> str:
    """Значение признака для карточки. Пустой признак модель получает как NaN."""
    return "нет данных" if math.isnan(value) else f"{value:g}"


def factors_block(
    names: Sequence[str],
    contributions: Sequence[float],
    features: FeatureVector,
) -> Block:
    """Собирает блок `factors`. Порядок строк по модулю веса, сверху сильнейший.

    Строка с весом по модулю ниже `_FACTOR_WEIGHT_FLOOR` отбрасывается: такой
    фактор двигает вероятность меньше чем на 4 % и только шумит. Первые три
    строки остаются всегда, даже когда все веса слабые (ADR 0011).
    """
    ranked = sorted(zip(names, contributions, strict=True), key=lambda pair: -abs(pair[1]))
    items: list[dict[str, Any]] = [
        {
            "label": FEATURE_LABELS.get(name, name),
            "weight": round(shap_weight(float(value)), 6),
            "value": _shown(features.get(name, 0.0)),
        }
        for name, value in ranked
    ]
    kept = [
        item
        for index, item in enumerate(items)
        if index < 3 or abs(item["weight"]) >= _FACTOR_WEIGHT_FLOOR
    ]
    return Block(
        type="factors",
        title="Почему модель так решила",
        data={"items": kept, "note": _FACTOR_NOTE},
    )


def _rate(count: float, days: float) -> float:
    return round(max(0.0, count) / days, 3)


def timeseries_block(features: FeatureVector, at: datetime) -> Block:
    """Собирает блок `timeseries`: частота тревог по окнам наблюдения.

    Вектор держит накопленные счётчики вложенных окон. Разность соседних
    счётчиков даёт непересекающееся окно, деление на его длину даёт тревог в
    сутки. Точка ставится в середину окна.
    """
    week = float(features.get("obj_alarms_7d", 0.0))
    day = float(features.get("obj_alarms_1d", 0.0))
    return Block(
        type="timeseries",
        title="Тревоги доступа по окнам наблюдения",
        data={
            "series": [
                {
                    "name": "Тревоги на объекте",
                    "unit": "тревог в сутки",
                    "points": [
                        {"t": (at - timedelta(days=4)).isoformat(), "v": _rate(week - day, 6)},
                        {"t": (at - timedelta(hours=12)).isoformat(), "v": _rate(day, 1)},
                    ],
                },
                {
                    "name": "События на участке",
                    "unit": "событий в сутки",
                    "points": [
                        {
                            "t": (at - timedelta(days=3.5)).isoformat(),
                            "v": _rate(float(features.get("n_armed_7d", 0.0)), 7),
                        },
                    ],
                },
            ],
            "markerAt": at.isoformat(),
        },
    )


def _hours_back(features: FeatureVector, name: str) -> float | None:
    """Смещение события в часах. Пусто, если у участка такого события не было.

    Без истории признак `hours_since_*` равен возрасту участка плюс одни
    сутки (`app/features/access_daily.py`). Такое значение не событие, и
    лента его не показывает.
    """
    value = features.get(name)
    if value is None or math.isnan(value):
        return None
    age = features.get("unit_age_days")
    if age is not None and not math.isnan(age) and value >= 24 * (age + 1):
        return None
    return float(value)


def timeline_block(features: FeatureVector, at: datetime) -> Block:
    """Собирает блок `timeline`: события, которые вектор признаков ещё помнит."""
    events: list[dict[str, Any]] = []

    hours = _hours_back(features, "hours_since_last_alarm")
    if hours is not None:
        events.append(
            {
                "at": (at - timedelta(hours=hours)).isoformat(),
                "title": "Последняя тревога доступа",
                "kind": "alarm",
                "note": f"За неделю на объекте {features.get('obj_alarms_7d', 0.0):g}",
            }
        )

    hours = _hours_back(features, "hours_since_last_armed")
    if hours is not None:
        events.append(
            {
                "at": (at - timedelta(hours=hours)).isoformat(),
                "title": "Последнее событие на участке",
                "kind": "access",
                "note": "Тревога вне окна снятия с охраны, которую правило признало событием",
            }
        )

    hours = _hours_back(features, "hours_since_guard_change")
    if hours is not None:
        events.append(
            {
                "at": (at - timedelta(hours=hours)).isoformat(),
                "title": "Смена режима охраны",
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
    return Block(type="timeline", title="События участка", data={"events": events})


class UnauthorizedAccess:
    """Предиктор направления. Спецификация §7, `docs/08-ml-plugin.md`."""

    code = DIRECTION

    def applies(self, conn: Connection, facility_id: str) -> bool:
        """Отвечает, участок ли это доступа. ADR 0017.

        Загрузчик выгрузки пишет рядом участки, пикеты пожара и подтопления и
        объекты целиком. Модель училась только на участках. Синтетика вида
        единицы не знает, и направление считается на каждой её строке.
        """
        kind = conn.execute(
            text("SELECT kind FROM facility WHERE id = :facility_id"),
            {"facility_id": facility_id},
        ).scalar()
        if kind is None:
            return True
        if kind != "section":
            return False
        # Участок пожара без датчиков доступа модели не нужен (ADR 0018).
        return bool(
            conn.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM sensor WHERE facility_id = :facility_id "
                    "AND sensor_type IN ('CONTACT', 'MOTION'))"
                ),
                {"facility_id": facility_id},
            ).scalar()
        )

    def live_blocks(self, conn: Connection, facility_id: str, at: datetime) -> list[Block]:
        """Тревоги участка и режим охраны из свежих данных. ADR 0018."""
        return live_charts.access_blocks(conn, facility_id, at)

    def build_features(self, ctx: FeatureContext) -> FeatureVector:
        """Считает ровно те признаки, которые назвала модель.

        Список берётся у модели, а не задаётся здесь. Поэтому замена файла
        модели на модель с другим набором признаков не требует правки кода:
        достаточно, чтобы все её имена были в реестре `app.features.registry`.
        Модели нет значит признаки считать не для чего.
        """
        from app.features import registry

        model = _model()
        if model is None:
            return {}
        return registry.build(
            DIRECTION, ctx.conn, ctx.facility_id, ctx.at, list(model.feature_name())
        )

    def predict(self, features: FeatureVector) -> float:
        """Отдаёт вероятность события, откалиброванную, если калибратор есть.

        Бустер даёт монотонный, но не откалиброванный ранг (ADR 0002, T28).
        `calibration.joblib` чинит это изотонической регрессией. Файла нет
        значит калибровки нет, и метод отдаёт сырую вероятность бустера, а
        не падает: направление без калибратора работает, как работало.
        """
        model = _model()
        if model is None:
            raise RuntimeError(_MODEL_MISSING)
        raw = float(model.predict(_row(model, features))[0])
        calibrator = _calibrator()
        if calibrator is None:
            return raw
        return float(calibrator.predict([raw])[0])

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

        moment = at if at is not None else datetime.now(UTC).replace(microsecond=0)
        explainer = _explainer(model)
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

        Событие за последние сутки значит реагирует группа. Тревога за сутки
        без события значит сначала осмотр входа. Режим охраны не менялся
        дольше недели значит объект перестал вести режим, и проверять надо
        СКУД: так вели себя 12 объектов из 47 в 2026 году (ADR 0001). Всё
        остальное это плановая замена замка по накопленному риску.
        """
        armed = _hours_back(features, "hours_since_last_armed")
        if armed is not None and armed < 24:
            return "Выезд группы реагирования"
        alarm = _hours_back(features, "hours_since_last_alarm")
        if alarm is not None and alarm < 24:
            return "Осмотр люка и запорного механизма"
        guard = _hours_back(features, "hours_since_guard_change")
        if guard is not None and guard > GUARD_SILENCE_HOURS:
            return "Проверка СКУД"
        return "Замена замка"

    def label_rule(self, conn: Connection, facility_id: str, window: Window) -> bool:
        """Отвечает, было ли событие на участке в окне.

        Повторяет метку обучения: момент события по правилу
        `app/features/access.py`. Окно открыто слева и закрыто справа:
        `window.start` это момент расчёта, в метку он не входит.
        """
        row = conn.execute(
            text(
                f"WITH {event_ctes('e.facility_id = :facility_id')} "
                "SELECT 1 FROM event_moment WHERE ts > :start LIMIT 1"
            ),
            {
                **event_params(window.start, window.end + timedelta(microseconds=1)),
                "facility_id": facility_id,
                "start": window.start,
            },
        ).fetchone()
        return row is not None


register(UnauthorizedAccess())
