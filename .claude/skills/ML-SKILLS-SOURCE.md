# Сторонние навыки ML

Папки `ml-scikit-learn`, `ml-shap`, `ml-statsmodels` и `ml-aeon` взяты без правок из
<https://github.com/tungcorn/claude-ml-skills>, коммит
`a5707a5b5a66fd99d858803fd017d1743b603f43`, каталог `scientific-skills/`.
Автор навыков K-Dense Inc. Лицензии BSD-3-Clause (scikit-learn, statsmodels,
aeon) и MIT (shap) указаны в заголовке каждого `SKILL.md`.

Навык `scikit-survival` не взят: он под GPL-3.0, а спецификация бэкенда §2
разрешает только MIT, BSD и Apache-2.0.

Правила проекта сильнее примеров из навыков. Шаблоны навыка scikit-learn
делят выборку `train_test_split` случайно. Здесь выборка делится только по
времени: спецификация бэкенда §9, `ml/flood/10_train.py`.
