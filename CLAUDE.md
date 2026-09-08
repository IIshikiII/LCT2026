# LCT2026 — сервис прогнозирования аварий инженерных коллекторов Москвы

Хакатон «Международный хакатон 2026», задача 8 (ЖКХ). Итоговый продукт по ТЗ:
предиктивная ML-модель + веб-интерфейс диспетчера ОДС (дашборд рисков, карта
объектов, журнал прогнозов) + модуль автоматического формирования заявок на
превентивное обслуживание + REST API.

Метрики ТЗ: Precision > 0.7, Recall > 0.5; горизонт прогноза ≥ 24 ч; время
формирования прогноза < 5 мин.

## Раскладка репозитория

| Путь | Что | Статус |
|---|---|---|
| `ARM-ODS-frontend-spec.md` | Полная спецификация фронтенда (v2). Источник истины для UI. | готово |
| `frontend/` | Веб-интерфейс диспетчера ОДС. Vite + React + TS. | среда готова, экраны не реализованы |
| `backend/` | REST API + модуль заявок. | не начато |
| `ml/` | Модели прогнозирования по 4 направлениям. | не начато |

Планируется 4 направления прогнозирования (пожар, несанкционированный доступ,
отказ датчика, износ). Все приводятся к одному формату: вероятность события на
объекте в окне N часов, N ≥ 24.

## frontend/

Стек: Vite 8 · React 19 · TypeScript 6 strict · React Router 7 (всё состояние UI
в URL, глобального стора нет) · TanStack Query 5 (опрос `refetchInterval: 60_000`)
· Tailwind v4 (токены в `src/styles/tokens.css`, через `@theme` в `src/index.css`)
· MapLibre GL · Recharts · zod + react-hook-form · MSW + faker (моки) · Vitest +
Testing Library · oxlint.

Скрипты: `npm run dev` · `build` · `typecheck` · `lint` · `test`.

Приложение обязано полностью работать на моках: `VITE_USE_MOCKS=true` в `.env`.

### Архитектурные правила (см. spec §2, §8, §13) — не нарушать

1. **`Direction`, `level`, `status` — это `string`, а не union.** Никаких
   `type Direction = 'FIRE_RISK' | ...`. Ни одного `switch` по коду направления.
2. **Тело карточки прогноза — массив блоков `{ type, title, data }`** из API,
   рендерится через реестр `type → компонент` + запасной `GenericBlock`.
3. **Действия приходят из API** и уходят в один эндпоинт
   `POST /predictions/{id}/actions/{code}`. Не заводить ручку под каждую кнопку.
4. **Неизвестное не роняет экран.** Разбор ответов через `zod` с `.passthrough()`
   и `safeParse`; лишнее поле — не ошибка.
5. Состав экранов, колонок журнала, виджетов дашборда, справочников причин —
   из `GET /meta`, с запасными конфигами в `src/shared/config/`.
6. Периметр — ровно 4 экрана (`/`, `/map`, `/journal`, `/orders`) + правая
   панель карточки. Не расширять, пока эти четыре не закончены.

Тест на гибкость: добавить в мок `/meta` пятое направление — оно должно само
появиться в фильтрах, на карте и на дашборде без правок кода.

## Скиллы Claude Code

Плагины включены в `.claude/settings.json`, маркетплейс `claude-plugins-official`:
`typescript-lsp`, `frontend-design`, `skill-creator`, `feature-dev`.

Первый запуск на новой машине (если плагины не подтянулись автоматически):
```
claude plugin marketplace add anthropics/claude-plugins-official
claude plugin install typescript-lsp@claude-plugins-official -y --scope project
claude plugin install frontend-design@claude-plugins-official -y --scope project
claude plugin install skill-creator@claude-plugins-official -y --scope project
claude plugin install feature-dev@claude-plugins-official -y --scope project
```
После установки — перезапустить сессию Claude Code (LSP активируется со следующей).

Проектные скиллы в `.claude/skills/`:
`arm-design-tokens`, `arm-api-contract`, `arm-extension-registries`,
`arm-new-screen`, `arm-mock-data`, `arm-demo`.
