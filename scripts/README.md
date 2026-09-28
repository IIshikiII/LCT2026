# scripts

`local.sh` поднимает локальный стенд одной командой на Ubuntu и macOS. Скрипту
нужен только Docker: интерфейс идёт в контейнере `node:22-alpine`. Все порты
открыты только на `127.0.0.1`.

| Команда | Что делает |
|---|---|
| `scripts/local.sh front` | Интерфейс на заглушках, сервер не нужен |
| `scripts/local.sh api` | База, API, миграции, синтетика, прогон конвейера |
| `scripts/local.sh full` | Сервер и интерфейс на настоящем API |
| `scripts/local.sh status` | Что сейчас работает |
| `scripts/local.sh logs` | Журнал API |
| `scripts/local.sh down` | Остановить всё. Данные базы остаются |

На Windows выполните шаги из корневого [`README.md`](../README.md) вручную.
