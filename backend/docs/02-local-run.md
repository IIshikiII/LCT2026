# Запуск и проверки

Разработка идёт целиком в Docker. На хост-машину ставить Python не надо.

## Первый запуск

1. Скопируйте конфигурацию:
   ```
   cp .env.example .env
   ```
2. Поднимите базу, API и MLflow:
   ```
   docker compose up -d db api mlflow
   ```
3. Примените миграции:
   ```
   docker compose run --rm pipeline uv run --no-sync python -m app.cli migrate
   ```
4. Проверьте API:
   ```
   curl http://localhost:8000/healthz
   ```

Адреса: API — `http://localhost:8000`, OpenAPI — `http://localhost:8000/api/v1/docs`,
MLflow — `http://localhost:5000`.

## Синтетика на чистой базе

Настоящей выгрузки СМВУ в схеме `backend` нет. Чтобы конвейер и API работали
сразу после миграций, посейте объекты и события доступа:

```
docker compose run --rm pipeline uv run --no-sync python -m app.cli seed
```

Семя фиксировано (`app/synth/generate.py`), поэтому повтор команды не плодит
дублей: объекты и события те же. Наполняются только `collector`, `facility` и
`alarm_event` — этого достаточно для направления «несанкционированный доступ».

## Разведочный анализ

Набор `research` ни в один образ сервиса не входит. JupyterLab поднимается
отдельным сервисом:

```
docker compose --profile tools up research
```

Адрес — `http://localhost:8888`, токена нет, порт открыт только на петлевом
интерфейсе. Тетради лежат в `notebooks/`, выгрузки в `data/`, модели в
`artifacts/`. Правила работы описывает [08-ml-plugin.md](08-ml-plugin.md).

## Проверки перед коммитом

Все четыре команды обязаны пройти:

```
docker compose run --rm dev uv run --no-sync ruff check .
docker compose run --rm dev uv run --no-sync ruff format --check .
docker compose run --rm dev uv run --no-sync mypy app
docker compose run --rm dev uv run --no-sync pytest
```

Тесты работают на базе `arm_test`. Её создаёт скрипт
`scripts/init-databases.sh` при первом старте сервиса `db`.

## Две ловушки

### Вывод появляется только в конце

Команда `docker compose run` копит вывод и отдаёт его целиком после
завершения. Промежуточная проверка лог-файла показывает пустой файл. Это не
зависание.

### Окружение лежит вне /srv

Сервис `dev` монтирует весь каталог проекта в `/srv`. Монтирование закрыло бы
виртуальное окружение, созданное при сборке. Поэтому `Dockerfile.dev` ставит
`UV_PROJECT_ENVIRONMENT=/opt/venv`.

## Перезапуск API после правки кода

Каталог `app/` смонтирован в контейнер, но uvicorn работает без слежения за
файлами. Пакет `watchfiles` в набор `api` не входит. После правки кода
выполните:

```
docker compose restart api
```
