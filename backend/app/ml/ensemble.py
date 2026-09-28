"""Среднее нескольких бустеров LightGBM на шкале логарифма шансов.

Модель подтопления (ADR 0012) это модели фолдов растущего окна, а не одна
модель, обученная заново на всех годах. Порог выбран на прогнозах тех же
моделей, поэтому шкала вероятности не сдвигается.

Среднее берётся по логиту, а не по вероятности. Тогда вклад признака в прогноз
ансамбля равен среднему вкладов SHAP его моделей, и объяснение остаётся
точным: сумма вкладов плюс фон равна логиту прогноза.

Класс держит тот же интерфейс, что бустер: `feature_name()` и `predict()`.
Метод `shap_values()` плагин зовёт вместо `shap.TreeExplainer`.
"""

from __future__ import annotations

from typing import Any


class MarginEnsemble:
    def __init__(self, boosters: list[Any]) -> None:
        if not boosters:
            raise ValueError("ансамбль без моделей")
        names = [list(b.feature_name()) for b in boosters]
        if any(n != names[0] for n in names):
            raise ValueError("модели ансамбля учились на разных признаках")
        self.boosters = boosters
        self._explainers: list[Any] | None = None

    def __getstate__(self) -> dict[str, Any]:
        # Объяснители SHAP строятся заново после загрузки: в файле им не место.
        return {"boosters": self.boosters}

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.boosters = state["boosters"]
        self._explainers = None

    def feature_name(self) -> list[str]:
        return list(self.boosters[0].feature_name())

    def margin(self, rows: Any) -> Any:
        import numpy as np

        return np.mean([b.predict(rows, raw_score=True) for b in self.boosters], axis=0)

    def predict(self, rows: Any) -> Any:
        import numpy as np

        return 1.0 / (1.0 + np.exp(-self.margin(rows)))

    @property
    def expected_value(self) -> float:
        self._build()
        assert self._explainers is not None
        return float(sum(float(e.expected_value) for e in self._explainers) / len(self._explainers))

    def shap_values(self, rows: Any) -> Any:
        """Вклады SHAP ансамбля: среднее вкладов его моделей."""
        import warnings

        import numpy as np

        self._build()
        assert self._explainers is not None
        parts = []
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="LightGBM binary classifier")
            for explainer in self._explainers:
                values = explainer.shap_values(np.asarray(rows, dtype=float))
                parts.append(values[-1] if isinstance(values, list) else values)
        return np.mean(parts, axis=0)

    def _build(self) -> None:
        if self._explainers is None:
            import shap

            self._explainers = [shap.TreeExplainer(b) for b in self.boosters]
