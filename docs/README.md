# docs

Общие документы проекта. Документы сервера и интерфейса лежат рядом с кодом:
[`backend/docs/`](../backend/docs/README.md) и [`frontend/docs/`](../frontend/docs/README.md).

| Документ | О чём |
|---|---|
| [`architecture.md`](architecture.md) | Компоненты, службы Docker, стек, главные решения |
| [`api.md`](api.md) | REST API: вход, список ручек, где лежит полный контракт |
| [`ARM-ODS-lifecycle.md`](ARM-ODS-lifecycle.md) | Путь прогноза и заявки со стрелками, права ролей |
| [`ARM-ODS-backend-spec.md`](ARM-ODS-backend-spec.md) | Спецификация сервера. Пометка «Сейчас» отмечает, где код отстаёт |
| [`ARM-ODS-frontend-spec.md`](ARM-ODS-frontend-spec.md) | Спецификация интерфейса |
| [`roadmap-post-deployment.md`](roadmap-post-deployment.md) | Как сервис учится на своих ошибках после ввода в эксплуатацию |
| [`customer/`](customer/) | Разбор ответов заказчика: QA-сессия, доклад о СМВУ, письменные ответы |

Развёртывание описывают [`../README.md`](../README.md) (локально) и
[`../deploy/README.md`](../deploy/README.md) (сервер).

## Дальнейшие шаги развития проекта

Собрать из этих документов сопроводительный пакет в PDF: архитектура, условия и
ограничения, сборка и установка, список библиотек.
