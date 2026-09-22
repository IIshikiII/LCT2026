"""Реестр версий моделей. Единственный модуль, который знает про MLflow.

Спецификация §2 требует двух свойств, и оба держит этот файл.

1. Замена реестра стоит одного файла: ни один другой модуль не импортирует
   `mlflow`.
2. Конвейер работает, когда сервер трекинга недоступен. Обучение всегда кладёт
   копию модели в `ARTIFACTS_DIR`, а загрузка берёт её оттуда, если реестр не
   ответил. Прогноз важнее учёта версий.

Импорты `mlflow` и `joblib` отложены внутрь функций. Поэтому модуль читается и
тестируется там, где ни одна из библиотек не установлена.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.config import config

log = logging.getLogger(__name__)

SUFFIX = ".joblib"
CALIBRATION_FILENAME = "calibration.joblib"


def _local_dir(direction: str) -> Path:
    return Path(config.artifacts_dir) / direction.lower()


def _save_local(model: Any, direction: str, version: str) -> Path | None:
    """Кладёт копию модели рядом с сервисом. Отдаёт путь или None при отказе."""
    import joblib

    path = _local_dir(direction) / f"{version}{SUFFIX}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, path)
    except OSError as error:
        log.warning(
            "копию модели записать не удалось",
            extra={"direction": direction, "path": str(path), "error": str(error)},
        )
        return None
    return path


def _newest_local(direction: str) -> Path | None:
    """Отдаёт файл модели направления, когда версию не назвали.

    Порядок выбора детерминирован, потому что время правки двух файлов одной
    секунды не различает.

    1. Файл `latest`, если он есть. Обучение без номера версии пишет его и
       перезаписывает при каждом прогоне.
    2. Наибольший числовой номер версии.
    3. Самый свежий файл по времени правки.
    """
    folder = _local_dir(direction)
    if not folder.is_dir():
        return None

    files = list(folder.glob(f"*{SUFFIX}"))
    if not files:
        return None

    default = folder / f"latest{SUFFIX}"
    if default in files:
        return default

    numbered = [item for item in files if item.stem.isdigit()]
    if numbered:
        return max(numbered, key=lambda item: int(item.stem))

    return max(files, key=lambda item: (item.stat().st_mtime, item.name))


def _register(mlflow: Any, model: Any, direction: str) -> bool:
    """Кладёт модель в реестр версией с именем направления.

    Отдаёт True, когда версия зарегистрирована.

    Зачем вообще: `log_artifact` кладёт файл рядом с прогоном, но версии из
    него не делает. Поиск `search_model_versions` тогда всегда пуст, чтение
    `models:/<направление>/<номер>` всегда отказывает, и реестр остаётся
    только на бумаге. Обучение писало артефакт и молчало об этом.

    Тип модели решает, каким видом её записать. Вид нужен для того, чтобы
    чтение шло через `pyfunc` и не зависело от библиотеки обучения. Вид
    неизвестен значит модель остаётся артефактом, как раньше: реестр не имеет
    права ронять обучение.
    """
    module = type(model).__module__.split(".")[0]
    flavours = {"lightgbm": "lightgbm", "sklearn": "sklearn", "xgboost": "xgboost"}
    name = flavours.get(module)
    if name is None:
        log.info(
            "вид модели неизвестен реестру, версия не заведена",
            extra={"direction": direction, "module": module},
        )
        return False

    flavour = getattr(mlflow, name)
    flavour.log_model(model, name="model", registered_model_name=direction)
    return True


def log_run(
    direction: str,
    params: dict[str, Any],
    metrics: dict[str, float],
    model: Any = None,
    version: str | None = None,
) -> str | None:
    """Записывает обучение в реестр и сохраняет модель.

    Отдаёт идентификатор прогона MLflow. Отдаёт None, когда трекинг выключен
    или сервер не ответил: обучение от этого не падает.

    Копия модели пишется в `ARTIFACTS_DIR` всегда, до обращения к серверу.
    """
    label = version or "latest"
    path = _save_local(model, direction, label) if model is not None else None

    if not config.mlflow_tracking_uri:
        log.info("трекинг выключен, модель только в файле", extra={"direction": direction})
        return None

    try:
        import mlflow

        mlflow.set_tracking_uri(config.mlflow_tracking_uri)
        mlflow.set_experiment(direction)
        with mlflow.start_run() as run:
            mlflow.log_params(params)
            mlflow.log_metrics(metrics)
            if path is not None:
                mlflow.log_artifact(str(path))
            registered = _register(mlflow, model, direction) if model is not None else False
            run_id: str = run.info.run_id
    except Exception as error:  # noqa: BLE001 — реестр не имеет права ронять обучение
        log.warning(
            "реестр моделей недоступен, обучение записано только в файл",
            extra={"direction": direction, "error": str(error)},
        )
        return None

    log.info(
        "обучение записано в реестр",
        extra={"direction": direction, "runId": run_id, "registered": registered},
    )
    return run_id


def load_model(direction: str, version: str | None = None) -> Any | None:
    """Отдаёт модель направления. Отдаёт None, когда её нет нигде.

    Порядок источников: реестр MLflow, затем файл в `ARTIFACTS_DIR`. Пустой
    результат — законное состояние: направление без обученной модели прогнозов
    не даёт.
    """
    model = _from_registry(direction, version)
    if model is not None:
        return model
    return _from_file(direction, version)


def _from_registry(direction: str, version: str | None) -> Any | None:
    if not config.mlflow_tracking_uri:
        return None

    try:
        import mlflow

        mlflow.set_tracking_uri(config.mlflow_tracking_uri)
        number = version or _newest_version(direction)
        if number is None:
            return None
        # pyfunc читает модель любой библиотеки. Команда вправе сменить
        # scikit-learn на бустинг, не трогая этот файл.
        return mlflow.pyfunc.load_model(f"models:/{direction}/{number}")
    except Exception as error:  # noqa: BLE001 — падение реестра лечится файлом
        log.warning(
            "модель из реестра не прочитана",
            extra={"direction": direction, "error": str(error)},
        )
        return None


def _newest_version(direction: str) -> str | None:
    """Отдаёт номер последней зарегистрированной версии модели направления."""
    import mlflow

    client = mlflow.MlflowClient(tracking_uri=config.mlflow_tracking_uri)
    found = client.search_model_versions(f"name='{direction}'")
    if not found:
        return None
    return str(max(int(item.version) for item in found))


def _from_file(direction: str, version: str | None) -> Any | None:
    path = _local_dir(direction) / f"{version}{SUFFIX}" if version else _newest_local(direction)
    if path is None or not path.is_file():
        return None

    try:
        import joblib

        model = joblib.load(path)
    except Exception as error:  # noqa: BLE001 — битый файл не должен ронять прогон
        log.warning(
            "файл модели не прочитан",
            extra={"direction": direction, "path": str(path), "error": str(error)},
        )
        return None

    log.info("модель прочитана из файла", extra={"direction": direction, "path": str(path)})
    return model


def load_calibrator(direction: str) -> Any | None:
    """Отдаёт калибратор вероятности направления. Отдаёт None без файла.

    Калибратор не ходит в реестр MLflow и не версионируется: файл лежит
    рядом с моделью, `ARTIFACTS_DIR/<направление>/calibration.joblib`, и
    его кладёт туда обучение (`ml/access/05_calibrate.py`). Файла нет
    значит направление не откалибровано, и это законное состояние, а не
    отказ: вызывающий код обязан вернуться к сырой вероятности модели.
    """
    path = _local_dir(direction) / CALIBRATION_FILENAME
    if not path.is_file():
        return None

    try:
        import joblib

        calibrator = joblib.load(path)
    except Exception as error:  # noqa: BLE001 — битый файл не должен ронять прогон
        log.warning(
            "файл калибратора не прочитан",
            extra={"direction": direction, "path": str(path), "error": str(error)},
        )
        return None

    log.info("калибратор прочитан из файла", extra={"direction": direction, "path": str(path)})
    return calibrator
