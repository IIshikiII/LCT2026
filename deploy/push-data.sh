#!/usr/bin/env bash
# Переносит загруженную выгрузку с машины разработчика на сервер. ADR 0017.
#
# Выгрузка весит 15,9 ГБ и на сервер не помещается. Загрузчик (`python -m
# app.cli ingest`) идёт на машине разработчика, а на сервер уезжает только
# результат: таблицы базы и отрезок потока.
#
# Условия:
#   1. Локально подняты база и выгрузка загружена командой `ingest`.
#   2. Сервер уже развёрнут `deploy/install.sh` и доступен по SSH.
#
# Запуск из корня репозитория на машине разработчика:
#   bash deploy/push-data.sh LCT
# где LCT это имя хоста из ~/.ssh/config.
#
# На сервере скрипт останавливает конвейер и заглушку, стирает прежние
# объекты, события, прогнозы и заявки, кладёт новые и поднимает службы.
# Учётные записи и прогоны конвейера остаются.

set -euo pipefail

HOST=${1:?"укажите хост SSH, например: bash deploy/push-data.sh LCT"}
REMOTE_REPO=${REMOTE_REPO:-/home/user1/LCT/LCT2026}
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DUMP=$(mktemp -t arm-data-XXXXXX.dump)
trap 'rm -f "$DUMP"' EXIT

# Таблицы, которые наполняет загрузчик, и готовые прогнозы с заявками: с ними
# серверу не нужен тяжёлый первый прогон на двух ядрах.
TABLES=(collector facility sensor alarm_event sensor_reading weather_hourly
        ingest_state flood_water_day maintenance_window prediction work_order)

log() { printf '\n\033[1;36m==>\033[0m %s\n' "$*"; }

[ -f "$ROOT/backend/data/stream/slice.json" ] ||
    { echo "нет backend/data/stream/slice.json: сначала загрузите выгрузку командой ingest" >&2; exit 1; }

log "Снимаю дамп таблиц"
args=()
for t in "${TABLES[@]}"; do args+=(--table-and-children="$t"); done
(cd "$ROOT/backend" && docker compose exec -T db \
    pg_dump -U arm -d arm --data-only -Fc "${args[@]}") >"$DUMP"
echo "    $(du -h "$DUMP" | cut -f1)"

log "Копирую дамп и отрезок потока на $HOST"
ssh "$HOST" "mkdir -p '$REMOTE_REPO/backend/data/stream'"
scp -q "$DUMP" "$HOST:/tmp/arm-data.dump"
scp -q "$ROOT/backend/data/stream/"* "$HOST:$REMOTE_REPO/backend/data/stream/"

log "Заменяю данные на сервере"
# Скрипт уходит на сервер файлом, а не через ввод ssh: `docker compose exec -T`
# читает ввод и съел бы остаток скрипта.
REMOTE_SCRIPT=$(mktemp -t arm-restore-XXXXXX.sh)
trap 'rm -f "$DUMP" "$REMOTE_SCRIPT"' EXIT
cat >"$REMOTE_SCRIPT" <<REMOTE
set -euo pipefail
cd "$REMOTE_REPO/backend"
sudo docker compose --profile stream stop scheduler smvu-stub </dev/null 2>/dev/null || true
sudo docker compose exec -T db psql -U arm -d arm -v ON_ERROR_STOP=1 -c \
  "TRUNCATE action_log, work_order, prediction, flood_water_day, alarm_event,
   sensor_reading, maintenance_window, sensor, facility, collector, weather_hourly,
   ingest_state RESTART IDENTITY CASCADE" </dev/null
sudo docker compose exec -T db pg_restore -U arm -d arm --data-only --disable-triggers \
  </tmp/arm-data.dump
sudo docker compose exec -T db psql -U arm -d arm -v ON_ERROR_STOP=1 -At -c \
  "SELECT setval(pg_get_serial_sequence('alarm_event', 'id'), coalesce(max(id), 1)) FROM alarm_event;
   SELECT setval(pg_get_serial_sequence('sensor_reading', 'id'), coalesce(max(id), 1)) FROM sensor_reading;
   SELECT setval(pg_get_serial_sequence('maintenance_window', 'id'), coalesce(max(id), 1)) FROM maintenance_window;
   SELECT 'событий: ' || count(*) FROM alarm_event;" </dev/null
rm -f /tmp/arm-data.dump
sudo docker compose --profile stream up -d --build scheduler smvu-stub </dev/null
REMOTE
scp -q "$REMOTE_SCRIPT" "$HOST:/tmp/arm-restore.sh"
ssh "$HOST" "bash /tmp/arm-restore.sh; status=\$?; rm -f /tmp/arm-restore.sh; exit \$status"

log "Готово: данные на сервере, поток и расписание подняты"
