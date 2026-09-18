"""Круговой тест артефакта `out/model.joblib`. `backend/app/ml/tracking.py`
хранит и читает модель направления через `joblib.dump`/`joblib.load`, не
разбирая, какая библиотека её обучила. Тест подтверждает, что дамп бустера
`train.py` переживает этот путь: вероятность после `joblib.load` обязана
совпасть с вероятностью исходного бустера буква в букву.

Тест не читает `out/access_features.parquet`: панель признаков не нужна,
хватает случайной матрицы правильной ширины. Файлов модели нет значит
`train.py` не запускали, и тест пропускается, а не падает.
"""
import pathlib
import sys

import joblib
import lightgbm as lgb
import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from features import FEATURE_COLUMNS

OUT = pathlib.Path(__file__).resolve().parent / "out"
MODEL = OUT / "model.txt"
MODEL_JOBLIB = OUT / "model.joblib"


def _sample() -> np.ndarray:
    rng = np.random.default_rng(4217)
    return rng.normal(size=(50, len(FEATURE_COLUMNS))).astype(np.float32)


def test_joblib_round_trip_keeps_the_same_probability(tmp_path: pathlib.Path) -> None:
    if not MODEL.is_file():
        pytest.skip("out/model.txt отсутствует, train.py не запускали")

    booster = lgb.Booster(model_file=str(MODEL))
    path = tmp_path / "roundtrip.joblib"
    joblib.dump(booster, path)
    reloaded = joblib.load(path)

    x = _sample()
    assert np.array_equal(booster.predict(x), reloaded.predict(x))


def test_the_committed_artifact_matches_the_source_model() -> None:
    if not MODEL.is_file() or not MODEL_JOBLIB.is_file():
        pytest.skip("out/model.txt или out/model.joblib отсутствует")

    from_text = lgb.Booster(model_file=str(MODEL))
    from_joblib = joblib.load(MODEL_JOBLIB)

    x = _sample()
    assert np.array_equal(from_text.predict(x), from_joblib.predict(x))
