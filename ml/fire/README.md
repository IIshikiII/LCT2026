# ml/fire — направление «пожарный риск»

Путь тот же, что у `ml/flood/`: витрина, метка, панель, модель. Решения лежат
в ADR бэкенда:

- `backend/docs/adr/0014-fire-target.md`: метка. Сигнал вне пачки: обход и
  сбой линии отделяются по форме, а не по часу. Классификатор проверок не
  прошёл приёмку;
- `backend/docs/adr/0015-fire-model.md`: модель не обогнала правило «событие
  было вчера»;
- `backend/docs/adr/0016-fire-expert-rules.md`: направление работает на
  экспертных правилах `backend/app/ml/fire_rules.py`, точность не измерена.

## Файлы

| Файл | Что делает |
|---|---|
| `01_dataset.py` | Отбирает сигналы метки: дым, «выше 40ºC», тепловой «Не замкнут». Результат: `out/fire_alarms.parquet`, `out/fire_hourly.parquet`, `out/fire_units.parquet`. |
| `02_target.py` | Меряет три версии метки и наивную планку по годам. Результат: `out/target_stats.json`. |
| `03_ppr.py` | Разбирает график ППР датчиков метана на 2026 год и сверяет его с журналом. Результат: `out/ppr_2026.parquet`, `out/ppr_check.json`. |
| `04_pu_label.py` | Классификатор «событие или проверка» по ADR 0012. Две попытки из трёх, журнал `out/pu_attempts.json`. Приёмку не прошёл. |
| `05_panel.py` | Суточная панель «пикет и сутки», 51 признак. Результат: `out/panel_daily.parquet`, `out/panel_stats.json`. |
| `test_fire_panel.py` | Тест утечки панели. |
| `06_train.py` | Протокол `ml/flood/12_train_pu.py` на панели пожара, скользящий бюджет. Журнал `out/variants_v2.json`, окно `out/rolling_budget_v2.json`. Журнал `out/variants.json` держит первый круг на метке «вне окна». |
| `07_weather.py` | Скачивает влажность, точку росы и давление Москвы. Результат: `out/humidity_daily.parquet`. |
| `09_external.py` | Скачивает ветер, облачность, почву, PM2.5, PM10, угарный газ, отмечает салюты. Результат: `out/external_daily.parquet`. |
| `10_hybrid.py` | Гибрид: модель на старых пикетах, правило на молодых. Результат: `out/hybrid.json`. |
| `11_commissioning.py` | Отложенный год без объекта в пусконаладке 5962. Результат: `out/commissioning.json`. |
| `12_monthly.py` | Ежемесячное дообучение на отложенном году, с весом свежих и без. Прогресс `out/monthly_progress.txt`, итог `out/monthly.json`. |
| `13_hypotheses.py` | Скрытые факторы: шлейф, процесс Хоукса, PCA и UMAP профиля пикета, PCA погоды, выученный граф пикетов, индекс Kp, фаза Луны как контроль, характер пикета, сложность Лемпеля–Зива, Isolation Forest, LambdaRank. Отбор на году 2021/22, подтверждение на проверочных годах. Результат: `out/hypotheses_screen.json`, `out/hypotheses_confirm.json`. |
| `14_burn_in.py` | Период охлаждения нового датчика и объекта по частоте событий от возраста. Результат: `out/burn_in.json`. |
| `15_expert_history.py` | Разметка истории экспертными правилами сервиса: нагрузка по уровням, сигнал назавтра, примеры. Результат: `out/expert_history.json`, `out/expert_examples.csv`, `out/expert_episodes.parquet`. |
| `08_schedule.py` | Сверяет график ТО АКМ и ДУ с журналом: проверки газа идут в месяцы ТО. Результат: `out/to_schedule.parquet`, `out/to_check.json`. |

## Запуск

Скрипты запускать из корня репозитория. Путь к интерпретатору на Ubuntu и
macOS `.venv/bin/python`, на Windows `.venv\Scripts\python.exe`. Нужны
`eda/out/events.parquet`, `notebooks/out/channel_features.parquet` и
`ml/flood/out/weather_daily.parquet`.

```
.venv/Scripts/python.exe ml/fire/01_dataset.py
.venv/Scripts/python.exe ml/fire/02_target.py
.venv/Scripts/python.exe ml/fire/03_ppr.py
.venv/Scripts/python.exe ml/fire/07_weather.py
.venv/Scripts/python.exe ml/fire/08_schedule.py
.venv/Scripts/python.exe ml/fire/05_panel.py
.venv/Scripts/python.exe -m pytest ml/fire -q
```

## Полученные числа

| Показатель | Значение |
|---|---|
| Метка | сигнал вне пачки обхода или сбоя, горизонт 24 ч |
| Событий на обучающих годах | 1 662 |
| База отложенного года | 0,087 % |
| Модель | нет: на проверочных годах +13 % попаданий, на отложенном году 105 против 107 у правила |
| Сервис | экспертные правила: CRITICAL 1,5, HIGH 35, MEDIUM 71 эпизод в год по истории 2019–2025 |
| Охлаждение | новый датчик 15 суток, новый объект 180 суток или отметка эксплуатации |
| Правило на отложенном году | precision 15,1 %, recall 14,3 %, 1,95 тревоги в сутки |

## Дальнейшие шаги развития проекта

Измерить точность правил по отметкам бригад и проверить их на данных после
2026-06-30. Получить у заказчика график огневых работ и загрузить окна ППР 2026
года в сервис.
