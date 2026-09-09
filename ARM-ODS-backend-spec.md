# Спецификация бэкенда АРМ диспетчера ОДС (LCT2026, задача 8)

Документ для LLM, которая будет писать `backend/`. Схем и картинок нет намеренно:
только текст, который влияет на решения. Источник истины по контракту API —
`frontend/docs/02-api-contract.md` и `frontend/src/shared/api/schemas.ts`;
этот файл описывает, чем контракт наполняется со стороны сервера.

## 1. Что делает бэкенд

1. Принимает исторические выгрузки (xlsx/csv: логи СМВУ за 12+ лет, журналы ОДС,
   реестр оборудования ОЭ, данные АРМ-Контроль, графики ТО/ППР), нормализует их
   в реляционную схему.
2. Раз в N минут (по умолчанию 15) прогоняет конвейер прогнозирования по всем
   объектам и всем включённым направлениям, складывает прогнозы в БД.
3. Автоматически формирует заявки на превентивное обслуживание для прогнозов
   выше порога направления.
4. Отдаёт REST API, полностью совпадающий по формам с моками фронтенда.
5. Считает и отдаёт метрики качества (Precision/Recall) и здоровье конвейера.

Требования ТЗ, которые бэкенд обязан подтверждать данными: Precision > 0.7,
Recall > 0.5, горизонт ≥ 24 ч, время формирования прогноза < 5 мин
(`computeMs` на прогноз и `lastRunMs` на прогон).

## 2. Стек

Всё перечисленное — permissive open source (MIT / BSD / Apache-2.0), без
коммерческого лицензирования и без GPL/AGPL-заражения.

Обязательное:

- Python 3.12.
- FastAPI + Uvicorn (ASGI). Pydantic v2 для DTO и настроек (pydantic-settings).
- SQLAlchemy 2.0 (ORM, sync-сессии допустимы) + Alembic для миграций.
- PostgreSQL 16 как единственное хранилище. Расширения: PostGIS (координаты и
  bbox-запросы карты) и TimescaleDB — обе с permissive-лицензией на нужных нам
  возможностях; если Timescale ставить нежелательно, обычная таблица с
  BRIN-индексом по времени полностью достаточна на объёмах хакатона.
- psycopg (v3) как драйвер.
- APScheduler для периодического запуска конвейера внутри процесса. Celery и
  брокер не заводить: лишняя инфраструктура при одном воркере.
- pandas + openpyxl для разбора xlsx-выгрузок; polars допустим, если объём
  логов делает pandas медленным.
- scikit-learn (BSD) — базовые модели, метрики, калибровка, разбиение по времени.
- LightGBM (MIT) как основной градиентный бустинг; CatBoost (Apache-2.0)
  допустим для направлений с большим числом категориальных признаков.
- joblib для сериализации моделей; ONNX не требуется.
- structlog или стандартный logging с JSON-форматтером.

Инструменты разработки:

- uv (или pip-tools) для зависимостей; фиксировать lock-файл.
- ruff (линт + формат), mypy в нестрогом режиме.
- pytest + pytest-asyncio + httpx.AsyncClient для тестов API,
  testcontainers-python или отдельная тестовая БД для интеграционных.
- Docker + docker compose: сервисы `api`, `db`, опционально `worker`.

Чего не брать: Django (тяжёл для REST-only), Flask (нет валидации из коробки),
любые SaaS-зависимости и платные фичи (Timescale Cloud, Prophet-обвязки с
несвободными компонентами), MongoDB (данные реляционные), Kafka (нет потока).

## 3. Раскладка backend/

```
backend/
  pyproject.toml, uv.lock, Dockerfile, docker-compose.yml, .env.example
  alembic/                 миграции
  app/
    main.py                сборка FastAPI, CORS, роутеры, старт планировщика
    config.py              Settings (pydantic-settings), читает .env
    db.py                  engine, session-фабрика, зависимость get_db
    models/                SQLAlchemy-модели
    schemas/               pydantic-DTO ответов API (зеркало zod-схем фронта)
    api/                   роутеры: meta, predictions, facilities, orders, metrics, dashboard
    domain/                бизнес-логика: действия, автозаявки, пороги
    meta/                  реестр направлений, уровней, статусов, колонок, причин
    ingest/                парсеры выгрузок -> таблицы
    features/              построение признаков из сырых таблиц
    pipeline/              прогон прогнозирования, запись прогнозов, метрики
    ml/                    обучение и инференс по направлениям
  models/                  обученные артефакты (.joblib), в git через LFS или не в git
  data/                    исходные выгрузки, в git не хранить
  tests/
```

## 4. Контракт API — что реализовать буквально

База `/api/v1`. Все списки — конверт `{ items, page, pageSize, total }`.

- `GET  /meta`
- `GET  /predictions?direction&level&status&district&from&to&sort&page&pageSize`
- `GET  /predictions/{id}`
- `GET  /predictions/{id}/timeseries?from&to`
- `POST /predictions/{id}/actions/{code}`
- `GET  /facilities?bbox&direction&level` (GeoJSON FeatureCollection)
- `GET  /facilities/{id}`
- `GET  /orders?status&dueBefore&page&pageSize`
- `GET  /orders/{id}`
- `POST /orders/{id}/actions/{code}`
- `GET  /metrics/models`
- `GET  /metrics/pipeline`
- `GET  /dashboard/summary`
- `GET  /dashboard/top-risks?limit=10`

Правила параметров: `direction`, `level`, `status` — повторяемые
(`?direction=A&direction=B`), это мультивыбор, в FastAPI — `Query(default=[])`.
`sort` — строка `field:asc` / `field:desc`, поля белым списком.
`bbox` — `minLon,minLat,maxLon,maxLat`.
Все временные метки — ISO-8601 UTC со сдвигом (`2026-09-09T12:00:00Z`).
Ключи полей — camelCase (фронт ждёт именно их): в pydantic-моделях
`alias_generator=to_camel`, `populate_by_name=True`, ответы через
`model_dump(by_alias=True)`.

### Формы ответов

Повторяют `frontend/src/shared/api/schemas.ts` поле в поле. Кратко:

- `AppMeta`: `directions[{code,label,shortLabel,accent,minHorizonHours}]`,
  `riskLevels[{code,label,colorVar,order}]`,
  `statuses[{code,label,scope}]` (`scope` = `prediction` | `order`),
  `districts[{code,label}]`, `journalColumns[str]`, `orderColumns[str]`,
  `dashboardWidgets[str]`, `reasons: {ключ -> [{code,label}]}`.
- `FacilityRef`: `id, collector, section?, chamber?, device?, district, address,
  lat, lon`.
- `Prediction`: `id, direction, level, probability (0..1), horizonHours,
  computedAt, computeMs, status, facility, summary, orderId?`.
- `PredictionDetail` = `Prediction` + `blocks[CardBlock]` + `actions[ActionDef]`.
- `CardBlock`: `{ type, title, data }`, где `data` — произвольный JSON.
- `ActionDef`: `{ code, label, kind, confirm?, fields[FieldDef] }`;
  `FieldDef`: `{ name, label, type, required?, minLength?, optionsRef?,
  placeholder?, help? }`. `optionsRef` указывает на ключ в `meta.reasons`.
- `WorkOrder`: `id, number, predictionId, facility, workType, dueAt, status,
  createdAt, actions[], outcome?`;
  `outcome`: `{ actualCause, predictionConfirmed, comment, closedAt }`.
- `ModelMetric`: `{ direction, precision, recall, targetPrecision,
  targetRecall, evaluatedAt }`.
- `PipelineHealth`: `{ lastRunAt, lastRunMs, freshnessMinutes, maxComputeMs,
  minHorizonHours, targetComputeMs, targetHorizonHours }`.
- `DashboardSummary`: `{ byLevel, byDirection, byStatus, byOrderStatus, total }`,
  где первые четыре — словари `код -> число`, а не массивы фиксированной формы.
- Таймсерии: `{ series: [{ name, unit?, points: [{t, v}] }], markerAt? }`.
- `/facilities` — GeoJSON `FeatureCollection`; в `properties` каждой точки:
  `facilityId, level, direction, predictionId, probability`. Допустимо
  дополнительное поле `lines` (GeoJSON трасс коллекторов) — фронт его переживёт.

### Типы блоков карточки, которые фронт умеет рисовать

`factors` (`{items:[{label,weight,value?}]}`, weight в -1..1),
`timeseries` (`{series,markerAt}`), `timeline` (`{events:[{at,title,kind,note?}]}`),
`table` (`{columns:[{key,header,align?}],rows:[{}]}`),
`keyvalue` (`{items:[{label,value}]}`).
Любой другой `type` фронт рисует запасным `GenericBlock` и не падает — можно
отдавать экспериментальные блоки без согласования.

## 5. Правила, наследуемые от фронтенда — не нарушать

1. **`direction`, `level`, `status` — свободные строки.** Никаких enum'ов в
   ответах и никаких `if/elif` по коду направления в слое API. Направление —
   строка в БД плюс запись в реестре `app/meta/`. Добавление пятого направления
   должно быть добавлением плагина и строки в `/meta`, без правок роутеров.
2. **Один эндпоинт действий.** `POST /{entity}/{id}/actions/{code}` с плоским
   телом из значений полей формы. Ответ — обновлённая сущность целиком.
   Отдельных ручек `/confirm`, `/reject`, `/close` не заводить.
3. **`actions` вычисляются сервером** от текущего статуса сущности и роли
   пользователя. Фронт кнопки не придумывает. Известные коды в моках:
   `confirm`, `reject`, `confirm_order` (прогноз), `start`, `close` (заявка).
   Действие `close` обязано принимать `outcome.predictionConfirmed` — это
   источник честных Precision/Recall.
4. **Состав UI приходит из `/meta`**: колонки журнала, виджеты дашборда,
   справочники причин. Менять состав экрана — значит менять `/meta`, а не фронт.
5. **Терпимость к неизвестному взаимна**: фронт разбирает ответы мягко, поэтому
   добавление поля в ответ безопасно, а переименование существующего — нет.
   Переименование поля = правка `schemas.ts` и мок-хендлера фронта в том же PR.

## 6. Модель данных (минимум)

- `facility` — объект: коллектор, участок, камера, устройство, район, адрес,
  координаты, дата ввода в эксплуатацию, тип.
- `sensor` — датчик: тип (контактный, объёмный, температурный, дымовой,
  газовый), привязка к facility, дата установки, история замен.
- `alarm_event` — сработка СМВУ: датчик, время, тип, результат проверки
  (истинная/ложная). Основная таблица, миллионы строк, партиционировать по
  времени и индексировать `(sensor_id, occurred_at)`.
- `fault_log` — журнал неисправностей датчиков ОДС.
- `repair` — история ремонтов и ТО (регламент и факт).
- `permit` — допуски и заявки на работы АРМ-Контроль (организация, тип работ,
  окно дат) — ключевой признак для «Пожарного риска» и «Несанкционированного
  доступа».
- `inspection` — визуальные обследования ОЭ (для «Износа»).
- `weather_hourly` — внешние факторы (температура, осадки), опционально.
- `prediction` — прогноз: направление, объект, вероятность, уровень, горизонт,
  `computed_at`, `compute_ms`, статус, `summary`, JSONB `blocks`, `model_version`.
- `work_order` — заявка: номер, прогноз, объект, тип работ, срок, статус,
  `outcome` (JSONB) с `prediction_confirmed`.
- `action_log` — аудит: кто, когда, какое действие, над чем, с каким телом.
- `pipeline_run` — прогон конвейера: начало, длительность, число прогнозов,
  версия моделей.
- `model_metric` — метрики по направлению и дате оценки.

Уровни риска считаются из вероятности порогами, задаваемыми на направление в
конфиге, а не хардкодом: `LOW < 0.3 ≤ MEDIUM < 0.55 ≤ HIGH < 0.78 ≤ CRITICAL`
— значения по умолчанию, совпадающие с моками фронта.

## 7. Конвейер прогнозирования

Один прогон: выбрать активные объекты → построить признаки на момент `now` →
для каждого направления вызвать его предиктор → откалибровать вероятность →
присвоить уровень → собрать `summary` и `blocks` → записать прогнозы одной
транзакцией → создать автозаявки → записать `pipeline_run`.

Требования:
- Горизонт каждого направления ≥ 24 ч, объявляется в `/meta`
  (`minHorizonHours`) и хранится в прогнозе (`horizonHours`).
- `compute_ms` измеряется на прогноз, `last_run_ms` на прогон; и то и другое
  обязано укладываться в 5 минут — это проверяемое требование ТЗ.
- Идемпотентность: повторный прогон в том же окне обновляет прогноз объекта, а
  не плодит дубли (уникальность `(facility_id, direction, computed_at_bucket)`).
- Прогноз без объяснения не публикуется: `blocks` обязаны содержать хотя бы
  `factors` (вклад признаков) и один `timeseries`, иначе диспетчер не может
  принять решение, а жюри — оценить модель.

Направления реализуются как плагины с единым протоколом:
`code`, `label`, `min_horizon_hours`, `build_features(ctx)`,
`predict(features) -> probability`, `explain(features) -> blocks`,
`suggest_work_type(prediction) -> str`. Регистрация — через реестр в
`app/ml/registry.py`; добавление направления не должно требовать правок
конвейера.

## 8. Автоматические заявки

Правило по умолчанию: прогноз с уровнем HIGH или CRITICAL и без открытой заявки
на том же объекте по тому же направлению порождает `work_order` со статусом
`AUTO_CREATED`, сроком `dueAt = computed_at + horizonHours * коэффициент` (по
умолчанию 0.5) и типом работ от плагина направления. Пороги, коэффициент и
запрет дублей — в конфиге направления.

Жизненный цикл заявки: `AUTO_CREATED → CONFIRMED → IN_PROGRESS → DONE`,
ветка `REJECTED`. Статусы прогноза: `NEW → IN_REVIEW → ORDER_CONFIRMED → CLOSED`,
ветка `REJECTED`. Названия статусов фронт берёт из `/meta`, менять их можно, но
только вместе с моками.

## 9. Метрики качества

`GET /metrics/models` считается по закрытым заявкам: `predictionConfirmed=true`
— TP, `false` — FP; пропуски (аварии без прогноза) берутся из сопоставления
`alarm_event` с фактическими инцидентами — FN. Пока закрытых заявок мало,
допустимо отдавать офлайн-оценку на отложенной по времени выборке, но поле
`evaluatedAt` обязано честно показывать, когда оценка получена, а способ оценки
— быть описан в `backend/docs/`.

Разбиение при обучении — только по времени (train на прошлом, test на будущем).
Случайный `train_test_split` на временных рядах даёт утечку и завышенные
Precision/Recall, что вскроется на защите.

## 10. Нефункциональные требования

- CORS для `http://localhost:5173`.
- OpenAPI отдаётся FastAPI автоматически, `/api/v1/docs`.
- Ошибки: JSON `{ "detail": ... }`, коды 400/404/409/422; фронт их переживает,
  но осмысленный текст нужен для отладки на защите.
- Логи в JSON, с `request_id`; каждый прогон конвейера логирует длительность.
- Сиды: команда `python -m app.cli seed`, поднимающая демо-набор данных, чтобы
  фронт с `VITE_USE_MOCKS=false` заработал сразу после `docker compose up`.
- Аутентификация в периметр хакатона не входит. Если понадобится — только
  заголовок с ролью, влияющий на состав `actions`, без OAuth-обвязки.

## 11. Порядок работы

Тот же, что во фронтенде: сначала документация, потом тест, потом код. Правка
контракта API без правки `frontend/docs/02-api-contract.md`, zod-схемы и
мок-хендлера фронта в том же PR считается незавершённой.

Проверки перед коммитом: `ruff check`, `ruff format --check`, `mypy app`,
`pytest`. Все зелёные.

Тест на гибкость, зеркальный фронтовому: добавление пятого направления
плагином в `app/ml/` и строкой в `/meta` обязано появиться в фильтрах, на карте
и на дашборде без единой правки роутеров, схем и фронтенда.

## 12. Скиллы и плагины Claude Code для работы над backend/

Проверить установленные: `claude plugin list`. Маркетплейс уже подключён как
`claude-plugins-official` (см. корневой `CLAUDE.md`). Полезны:

- `feature-dev` — уже включён в проекте, режим разработки фичи целиком.
- `skill-creator` — уже включён; им создавать проектные скиллы ниже.
- LSP-плагин для Python, если он есть в маркетплейсе (по аналогии с
  `typescript-lsp`). Точное имя проверять командой
  `claude plugin marketplace list claude-plugins-official`, не угадывать.

Встроенные скиллы, применимые без установки: `code-review`, `security-review`,
`run`, `init`.

Проектные скиллы, которые стоит завести в `.claude/skills/` по мере появления
кода (по образцу существующих `arm-*`):

- `arm-backend-api-contract` — как добавить или изменить ручку: pydantic-DTO,
  роутер, тест, и обязательная синхронная правка `endpoints.ts`, `schemas.ts`,
  `src/mocks/handlers.ts` во фронтенде.
- `arm-direction-plugin` — как добавить направление прогнозирования: протокол
  плагина, регистрация, запись в `/meta`, блоки объяснения, тест на гибкость.
- `arm-ingest` — правила парсеров выгрузок: имена листов и колонок,
  нормализация адресов и идентификаторов датчиков, обработка мусора в данных.
- `arm-ml-eval` — протокол оценки: временное разбиение, расчёт Precision/Recall,
  запись `model_metric`, запрет случайного сплита.

Существующие фронтовые скиллы (`arm-api-contract`, `arm-extension-registries`,
`arm-mock-data`, `arm-demo`) читать перед любой правкой контракта: они описывают
ту сторону, которую бэкенд обязан не сломать.
