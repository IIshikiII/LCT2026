# backend

REST API и конвейер прогнозирования аварий инженерных коллекторов.

Разработка идёт целиком в Docker. Быстрый старт:

```
cp .env.example .env
docker compose up -d db api mlflow
docker compose run --rm pipeline uv run --no-sync python -m app.cli migrate
curl http://localhost:8000/healthz
```

| Документ | Отвечает на вопрос |
|---|---|
| [`../ARM-ODS-backend-spec.md`](../ARM-ODS-backend-spec.md) | Каким сервис должен стать |
| [`docs/01-dependencies.md`](docs/01-dependencies.md) | Из чего собран сервис, почему взята каждая зависимость |
| [`docs/02-local-run.md`](docs/02-local-run.md) | Как поднять сервис и прогнать проверки |
| [`docs/03-database.md`](docs/03-database.md) | Как устроена схема и как добавить миграцию |
| [`docs/08-ml-plugin.md`](docs/08-ml-plugin.md) | Как подключить модель и где вести разведочный анализ |

Образы: `api` слушает порт, `pipeline` считает прогнозы, `mlflow` ведёт версии
моделей, `research` держит JupyterLab для разведочного анализа, `dev` гоняет
тесты и линт. Образ `api` не содержит ни scikit-learn, ни openpyxl, ни mlflow,
ни одного бустинга.

Разведочный анализ: `docker compose --profile tools up research`, затем
`http://localhost:8888`.
