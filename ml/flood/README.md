# ml/flood — направление «риск подтопления»

Модель прогнозирования для направления «риск подтопления». Путь тот же, что у
`ml/access/`: витрина, метка, признаки, модель, порог, SHAP. Отличие одно:
бюджет вариантов и открытие отложенного года держит код, а не договорённость.

Решения по числам лежат в ADR бэкенда:

- `docs/adr/backend/0008-flood-target.md` — первая метка, заменена;
- `docs/adr/backend/0009-flood-model.md` — первая модель, заменена;
- `docs/adr/backend/0010-flood-real-water.md` — маска времени, заменена;
- `docs/adr/backend/0011-flood-model-real-water.md` — протокол: отложенный
  год и растущее окно. Модель заменена;
- `docs/adr/backend/0012-flood-pu-label.md` — действующая метка и модель:
  классификатор «вода или проверка», ансамбль, скользящий бюджет тревог;
- `docs/adr/backend/0013-flood-model-in-service.md` — та же модель в сервисе.

## Файлы

| Файл | Что делает |
|---|---|
| `01_dataset.py` | Отбирает тревоги «Затоплен» у насоса, «Не замкнут» у датчика затопления и «Работают все насосы в АНС». Сводит их к единице «объект, галерея, пикет» по часам. Результат: `out/flood_hourly.parquet`, `out/flood_units.parquet`. |
| `02_target.py` | Меряет версии метки рядом: базу, наивную планку, доли по значениям, месяцам и отрезкам. Результат: `out/target_stats.json`. |
| `03_pumps.py` | Сводит работу насосов к часам: смены состояния, минуты работы, недоступность, «все насосы работают», затопления. Результат: `out/pump_hourly.parquet`, `out/pump_channels.parquet`. |
| `04_panel.py` | Собирает часовую панель признаков. Метка берётся из `out/pu_events.parquet`. Суточная сетка это её строки на 00:00. Результат: `out/panel_hourly.parquet`, `out/panel_stats.json`. |
| `test_flood_panel.py` | Тест утечки панели. |
| `05_scenario.py` | Меряет сценарий заказчика: лифт события после мигания и недоступности насоса. Результат: `out/scenario.json`. |
| `06_train.py` | Первая модель на старой метке, история ADR 0009. |
| `07_export.py` | Кладёт ансамбль, классификатор воды и замер в `backend/artifacts/flood_risk/`. Пишет в замер точку скользящего бюджета. |
| `08_weather.py` | Скачивает архив погоды Москвы Open-Meteo и строит `out/weather_hourly.parquet`, `out/weather_daily.parquet`. |
| `09_hypotheses.py` | Критерии воды ADR 0010, история. |
| `10_train.py` | Протокол ADR 0011: отложенный год, три проверочных года растущим окном. Его функции зовёт `12_train_pu.py`. |
| `11_pu_label.py` | Метка ADR 0012: классификатор «вода или проверка». Бюджет три попытки, журнал `out/pu_attempts.json`. Режим `export hourly` выгружает классификатор в `out/pu_classifier.joblib`. |
| `12_train_pu.py` | Ансамбль моделей фолдов на метке ADR 0012, журнал `out/variants_pu.json`. Результат: `out/model.joblib`, `out/metrics.json`. |
| `13_alert_budget.py` | Правило равного числа тревог в сутки. Выигрыша нет. |
| `14_rolling_budget.py` | Скользящий бюджет тревог на проверочных годах, выбор окна. Результат: `out/rolling_budget.json`. |
| `15_holdout_budget.py` | Скользящий бюджет на отложенном году. Результат: `out/holdout_budget.json`. |

## Запуск

Скрипты запускать из корня репозитория через `.venv`. Путь к интерпретатору на
Ubuntu и macOS — `.venv/bin/python`, на Windows — `.venv\Scripts\python.exe`.
Нужны `eda/out/events.parquet` и `notebooks/out/channel_features.parquet`.

```
.venv/Scripts/python.exe ml/flood/01_dataset.py
.venv/Scripts/python.exe ml/flood/03_pumps.py
.venv/Scripts/python.exe ml/flood/08_weather.py
.venv/Scripts/python.exe ml/flood/11_pu_label.py export hourly
.venv/Scripts/python.exe ml/flood/04_panel.py
.venv/Scripts/python.exe ml/flood/07_export.py
.venv/Scripts/python.exe -m pytest ml/flood -q
```

Журналы `out/pu_attempts.json`, `out/variants_pu.json` лежат в git и держат
бюджет. Скрипт `15_holdout_budget.py` отказывает на третьем запуске.

## Полученные числа

| Показатель | Значение |
|---|---|
| Метка | сутки пикета с сигналом, которые классификатор признал водой, горизонт 24 ч |
| Суток с водой на обучающих годах | 2 305 из 3 862 суток с сигналом |
| База на отложенном году | 1,96 % |
| Модель | ансамбль девяти бустеров `recent_reg2`, 52 признака |
| Тревоги | скользящий бюджет: за 30 суток столько же, сколько у правила «вода была вчера» |
| Отложенный год, precision | 38,5 % против 32,9 % у правила |
| Отложенный год, recall | 39,6 % против 32,6 % у правила |
| Запасной порог HIGH, пока бюджета нет | 0,092818 |

## Дальнейшие шаги развития проекта

Разобрать падение качества от года к году и проверить модель на данных после
2026-06-30. Добавить признак уровня Москвы-реки.
