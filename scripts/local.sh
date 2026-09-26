#!/usr/bin/env bash
#
# Локальный стенд. Поднимает интерфейс и, по запросу, настоящий бэкенд.
# Ничего не публикует наружу: все порты висят на петлевом интерфейсе,
# скрипт не трогает git и не ходит на удалённый сервер.
#
# Node на хосте не нужен: дев-сервер идёт в контейнере node:22-alpine.
#
#   scripts/local.sh front    интерфейс на заглушках, бэкенд не нужен
#   scripts/local.sh api      база, API, миграции, синтетика, прогон конвейера
#   scripts/local.sh full     api и поверх него интерфейс на настоящем API
#   scripts/local.sh status   что сейчас работает
#   scripts/local.sh logs     журнал API
#   scripts/local.sh down     остановить всё, данные базы остаются
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
FRONT_CONTAINER="arm-front"
FRONT_PORT="${FRONT_PORT:-5173}"
API_URL="http://127.0.0.1:8000"
NODE_IMAGE="node:22-alpine"

say() { printf '\n>> %s\n' "$*"; }

# Докер нужен и интерфейсу, и бэкенду. Демон может спать, поэтому скрипт
# будит его сам и ждёт готовности.
need_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "Докер не установлен. Поставьте Docker Desktop." >&2
    exit 1
  fi
  if docker info >/dev/null 2>&1; then return 0; fi

  say "Докер спит. Запускаю Docker Desktop"
  open -a Docker 2>/dev/null || {
    echo "Запустите демон Докера вручную." >&2
    exit 1
  }
  for _ in $(seq 1 60); do
    docker info >/dev/null 2>&1 && { say "Докер готов"; return 0; }
    sleep 2
  done
  echo "Демон Докера не поднялся за две минуты." >&2
  exit 1
}

# Дев-сервер Vite в контейнере. Зависимости лежат в именованном томе,
# поэтому папка node_modules хоста остаётся чистой.
start_front() {
  local use_mocks="$1" api_base="$2"

  docker rm -f "$FRONT_CONTAINER" >/dev/null 2>&1 || true
  say "Ставлю зависимости интерфейса. Первый раз это одна-две минуты"
  docker run --rm \
    -v "$ROOT/frontend:/srv" \
    -v arm-front-modules:/srv/node_modules \
    -w /srv "$NODE_IMAGE" \
    npm install --no-audit --no-fund

  say "Поднимаю интерфейс на http://localhost:$FRONT_PORT"
  docker run -d --name "$FRONT_CONTAINER" \
    -p "127.0.0.1:$FRONT_PORT:5173" \
    -v "$ROOT/frontend:/srv" \
    -v arm-front-modules:/srv/node_modules \
    -w /srv \
    -e "VITE_USE_MOCKS=$use_mocks" \
    -e "VITE_API_BASE_URL=$api_base" \
    -e "VITE_API_DOCS_URL=$api_base/docs" \
    "$NODE_IMAGE" \
    npm run dev -- --host 0.0.0.0 >/dev/null

  say "Интерфейс: http://localhost:$FRONT_PORT"
}

# База, API и данные, на которых видно прогнозы.
start_api() {
  say "Поднимаю базу и API"
  docker compose --project-directory "$BACKEND" up -d db api

  say "Применяю миграции"
  docker compose --project-directory "$BACKEND" run --rm pipeline \
    uv run --no-sync python -m app.cli migrate

  say "Сею синтетику: 40 объектов"
  docker compose --project-directory "$BACKEND" run --rm pipeline \
    uv run --no-sync python -m app.cli seed --facilities 40

  say "Прогоняю конвейер"
  docker compose --project-directory "$BACKEND" run --rm pipeline \
    uv run --no-sync python -m app.cli run-pipeline

  say "API: $API_URL/api/v1/docs"
}

case "${1:-front}" in
  front)
    need_docker
    start_front true /api/v1
    ;;
  api)
    need_docker
    start_api
    ;;
  full)
    need_docker
    start_api
    start_front false "$API_URL/api/v1"
    ;;
  status)
    need_docker
    docker compose --project-directory "$BACKEND" ps
    docker ps --filter "name=$FRONT_CONTAINER" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
    ;;
  logs)
    need_docker
    docker compose --project-directory "$BACKEND" logs --tail 80 -f api
    ;;
  down)
    need_docker
    docker rm -f "$FRONT_CONTAINER" >/dev/null 2>&1 || true
    docker compose --project-directory "$BACKEND" down
    say "Остановлено. Том базы на месте"
    ;;
  *)
    sed -n '3,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
