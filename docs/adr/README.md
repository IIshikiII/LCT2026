# Принятые решения (ADR)

ADR это запись одного архитектурного решения: контекст, выбор, числа и способ
проверки. Решения сервера и моделей лежат в `backend/`, решения интерфейса в
`frontend/`. Нумерация у двух папок своя, поэтому в тексте рядом с номером
стоит область.

## Сервер и модели

| Решение | О чём |
|---|---|
| [0001-access-target.md](backend/0001-access-target.md) | Метка направления «несанкционированный доступ» и её планка |
| [0002-access-threshold.md](backend/0002-access-threshold.md) | Порог того же направления и цена ошибки в нарядах |
| [0003-pipeline-duration.md](backend/0003-pipeline-duration.md) | Сколько длится прогон конвейера и как он укладывается в ТЗ |
| [0004-level-thresholds-per-direction.md](backend/0004-level-thresholds-per-direction.md) | Почему границы уровней риска задаёт направление |
| [0005-test-database.md](backend/0005-test-database.md) | Почему тесты работают только с базой на суффиксе `_test` |
| [0006-prediction-lifecycle.md](backend/0006-prediction-lifecycle.md) | Статусы прогноза и заявки, решение диспетчера отдельными полями |
| [0007-roles-and-auth.md](backend/0007-roles-and-auth.md) | Роли, вход в систему, второй фактор, журнал действий |
| [0008-flood-target.md](backend/0008-flood-target.md) | Первая метка направления «риск подтопления» |
| [0009-flood-model.md](backend/0009-flood-model.md) | Первая модель подтопления. Заменена |
| [0010-flood-real-water.md](backend/0010-flood-real-water.md) | Вода и плановые проверки по маске времени. Заменено ADR 0012 |
| [0011-flood-model-real-water.md](backend/0011-flood-model-real-water.md) | Протокол: отложенный год и растущее окно. Модель заменена |
| [0012-flood-pu-label.md](backend/0012-flood-pu-label.md) | Метка по признакам события, ансамбль, скользящий бюджет тревог |
| [0013-flood-model-in-service.md](backend/0013-flood-model-in-service.md) | Модель ADR 0012 в сервисе: разметка суток, погода, бюджет в конвейере |
| [0014-fire-target.md](backend/0014-fire-target.md) | Метка пожара: сигнал вне пачки обхода или сбоя линии. Графики ППР и ТО |
| [0015-fire-model.md](backend/0015-fire-model.md) | Модель пожара не обогнала правило «событие было вчера» |
| [0016-fire-expert-rules.md](backend/0016-fire-expert-rules.md) | Пожар на экспертных правилах: уровни, охлаждение, точность не измерена |
| [0017-ingest-and-stream.md](backend/0017-ingest-and-stream.md) | Загрузчик выгрузки, сдвиг времени на целые недели, заглушка СМВУ, ритм направлений, карточка на происшествие, задержка потока |
| [0018-fire-sections-and-live-cards.md](backend/0018-fire-sections-and-live-cards.md) | Пожар на участке, живые графики карточек, демонстрационный режим, порядок журнала |
| [0019-critical-alerts.md](backend/0019-critical-alerts.md) | Уведомление о критических инцидентах, которые никто не взял в работу |
| [0020-order-follows-prediction.md](backend/0020-order-follows-prediction.md) | Автозаявка идёт за свежим прогнозом объекта или отклоняется, когда риск упал |
| [0021-finished-card-freezes.md](backend/0021-finished-card-freezes.md) | Законченная карточка замораживается, новые данные идут в её чистую копию |
| [0022-one-open-card.md](backend/0022-one-open-card.md) | Одна открытая карточка и одна открытая заявка на объект, журнал всех прогнозов в файл |

## Интерфейс

| Решение | О чём |
|---|---|
| [0001-no-global-store.md](frontend/0001-no-global-store.md) | Состояние UI в URL, глобального стора нет |
| [0002-direction-is-a-string.md](frontend/0002-direction-is-a-string.md) | Направление, уровень и статус — строки, а не union |
| [0003-blocks-not-layouts.md](frontend/0003-blocks-not-layouts.md) | Тело карточки — массив блоков из API, а не вёрстка |
| [0004-single-action-endpoint.md](frontend/0004-single-action-endpoint.md) | Один эндпоинт действий вместо ручки на кнопку |
| [0005-polling-not-websocket.md](frontend/0005-polling-not-websocket.md) | Опрос раз в минуту вместо WebSocket |
| [0006-tolerant-parsing.md](frontend/0006-tolerant-parsing.md) | Разбор ответов не бросает исключений |
| [0007-mocks-as-a-backend-stub.md](frontend/0007-mocks-as-a-backend-stub.md) | Заглушки — это стенд-ин бэкенда, а не тестовые фикстуры |
| [0008-offline-map-style.md](frontend/0008-offline-map-style.md) | Карта по умолчанию не ходит в интернет |
| [0009-light-theme.md](frontend/0009-light-theme.md) | Светлая тема и переключатель |
| [0010-status-colour-from-meta.md](frontend/0010-status-colour-from-meta.md) | Цвет статуса приходит из меты, а не из шкалы риска |
| [0011-shap-block.md](frontend/0011-shap-block.md) | SHAP показывает блок `factors`, а не новый тип блока |
