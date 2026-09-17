# ml/access — направление «несанкционированный доступ»

Каркас модели прогнозирования для направления «несанкционированный доступ».
Файлы пока пустые. Задачи по их наполнению см. в `loop/BACKLOG.md`, этап 1–3.

## Файлы

| Файл | Что делает |
|---|---|
| `01_dataset.py` | Отбирает из `eda/out/events.parquet` каналы доступа и сводит их к единице «объект, галерея, пикет» по часам. Результат: `out/access_hourly.parquet`. |
| `features.py` | Строит признаки для модели на момент расчёта: частоту сработок, ночную долю, признаки времени и соседних каналов. |
| `train.py` | Обучает LightGBM с разбиением по времени, считает Precision, Recall, PR-AUC и SHAP. Результат: `out/model.txt`, `out/metrics.json`. |
| `out/` | Промежуточные и итоговые данные скриптов. В git не попадают, кроме `.gitkeep`. |

## Запуск

Все скрипты запускать через `.venv` корня репозитория:

```
.venv/Scripts/python.exe ml/access/01_dataset.py
.venv/Scripts/python.exe ml/access/features.py
.venv/Scripts/python.exe ml/access/train.py
```

Порядок запуска фиксирован: `01_dataset.py` перед `features.py`, `features.py`
перед `train.py`. Каждый следующий скрипт читает результат предыдущего из
`out/`.
