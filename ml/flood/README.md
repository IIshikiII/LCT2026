# ml/flood — направление «риск подтопления»

Модель прогнозирования для направления «риск подтопления». Путь тот же, что у
`ml/access/`: витрина, метка, признаки, модель, порог, SHAP. Отличие одно:
бюджет вариантов и однократное открытие отложенной выборки держит код
`06_train.py`, а не договорённость.

Решения по числам лежат в четырёх ADR:

- `backend/docs/adr/0008-flood-target.md` — первая метка, заменена;
- `backend/docs/adr/0009-flood-model.md` — первая модель, заменена;
- `backend/docs/adr/0010-flood-real-water.md` — как отличить воду от плановой
  проверки, одиннадцать критериев;
- `backend/docs/adr/0011-flood-model-real-water.md` — действующая модель:
  отложенный год, растущее окно, гипотезы, порог и уровни.

## Файлы

| Файл | Что делает |
|---|---|
| `01_dataset.py` | Отбирает тревоги «Затоплен» у насоса, «Не замкнут» у датчика затопления и «Работают все насосы в АНС». Сводит их к единице «объект, галерея, пикет» по часам. Результат: `out/flood_hourly.parquet`, `out/flood_units.parquet`. |
| `02_target.py` | Меряет три версии метки рядом: базу, наивную планку, долю по значениям, месяцы, отрезки и совпадение «все насосы работают» с затоплением. Результат: `out/target_stats.json`. |
| `03_pumps.py` | Сводит работу насосов к часам: смены состояния, минуты работы, недоступность, «все насосы работают», затопления. Насосы без пикета тоже входят. Результат: `out/pump_hourly.parquet`, `out/pump_channels.parquet`. |
| `04_panel.py` | Собирает часовую панель из 42 признаков. Суточная сетка это её строки на 00:00. Результат: `out/panel_hourly.parquet`, `out/panel_stats.json`. |
| `test_panel.py` | Тест утечки: события и работа насосов в час расчёта и позже не меняют ни один признак единицы, соседа по объекту и соседа по комплексу. |
| `05_scenario.py` | Меряет сценарий заказчика: лифт события после мигания и недоступности насоса. Отложенную выборку не читает. Результат: `out/scenario.json`. |
| `06_train.py` | Режим `variant` учит вариант по трём семенам и меряет на проверочном отрезке. Режим `final` один раз открывает отложенную выборку. Результат: `out/variants.json`, `out/metrics.json`, `out/model.txt`, `out/model.joblib`. |
| `07_export.py` | Кладёт модель и замер в `backend/artifacts/flood_risk/`, откуда их читает плагин. |
| `08_weather.py` | Скачивает архив погоды Москвы Open-Meteo и строит `out/weather_hourly.parquet`, `out/weather_daily.parquet`. |
| `09_hypotheses.py` | Проверяет одиннадцать критериев воды: ритм недели, погоду, работу насоса, объём. Результат: `out/hypotheses.json`. |
| `10_train.py` | Действующий протокол: отложенный год, три проверочных года растущим окном, бюджет вариантов в `out/variants_cv.json`. Режим `final` один раз открывает отложенный год и пишет `out/metrics.json`, `out/model.*`. |

Файл `06_train.py` и журнал `out/variants.json` описывают первую модель на
старой метке. Они оставлены как история ADR 0009.

## Запуск

Скрипты запускать из корня репозитория через `.venv`. Путь к интерпретатору на
Ubuntu и macOS — `.venv/bin/python`, на Windows — `.venv\Scripts\python.exe`.
Нужны `eda/out/events.parquet` и `notebooks/out/channel_features.parquet`.

```
.venv/Scripts/python.exe ml/flood/01_dataset.py
.venv/Scripts/python.exe ml/flood/02_target.py
.venv/Scripts/python.exe ml/flood/03_pumps.py
.venv/Scripts/python.exe ml/flood/04_panel.py
.venv/Scripts/python.exe ml/flood/05_scenario.py
.venv/Scripts/python.exe ml/flood/08_weather.py
.venv/Scripts/python.exe ml/flood/09_hypotheses.py
.venv/Scripts/python.exe ml/flood/10_train.py variant base --groups pump,evt,obj,cx,cal
.venv/Scripts/python.exe ml/flood/10_train.py final base
.venv/Scripts/python.exe ml/flood/07_export.py
.venv/Scripts/python.exe -m pytest ml/flood -q
```

Скрипты с первого по пятый идут меньше минуты вместе. Вариант на суточной
сетке учится около минуты, на часовой несколько минут.

Журнал вариантов `out/variants.json` лежит в git и держит бюджет. Новый
вариант с тем же именем скрипт не примет, девятый вариант тоже. Повторное
открытие отложенной выборки требует `--reopen "причина"`.

## Полученные числа

| Показатель | Значение |
|---|---|
| Единиц «объект, галерея, пикет» | 82 |
| Метка | «Затоплен» у насоса или «Не замкнут» у датчика затопления вне окна «будни 8:00–16:00», 24 ч |
| База, сутки | 1,09 % |
| Порог мигания | больше 14 смен за час |
| Вариантов использовано | 6 из 8 |
| Итог | суточная модель base, 37 признаков, 26 деревьев |
| Отложенный год, точность при полноте планки | 57,8 % против 29,1 % у планки |
| Порог заявки с проверочных лет | 0,065061 |
