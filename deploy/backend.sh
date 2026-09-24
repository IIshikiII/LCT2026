#!/usr/bin/env bash
#
# Развёртывание бэкенда: Postgres и API в Docker, модель, миграции, посев.
#
# Сам по себе не запускается, только подключается через source из install.sh и
# update.sh. Все настройки — в deploy/config.sh.
#
# Что здесь происходит по порядку:
#   1. Docker ставится из репозиториев Ubuntu, без сторонних источников.
#   2. backend/.env собирается один раз и переживает выкладки: в нём лежит
#      ключ подписи токенов, и перегенерация ключа снимала бы все сессии.
#   3. Обученная модель переносится из ml/access/out в ARTIFACTS_DIR, откуда
#      её читает плагин направления.
#   4. Поднимаются две службы: db и api. Реестр MLflow на стенде не нужен,
#      модель берётся файлом.
#   5. Миграции, посев, публикация замера, прогон конвейера.
#
# Порт API наружу не открывается: он публикуется на петле, а снаружи виден
# только через nginx по пути /api/v1. Правило 2 из CLAUDE.md.

COMPOSE_DIR="$REPO/backend"

# Куда плагин направления «несанкционированный доступ» ходит за моделью.
MODEL_SOURCE="$REPO/ml/access/out"
MODEL_TARGET="$COMPOSE_DIR/artifacts/unauthorized_access"

# Короткое имя для docker compose. Вызывается из каталога backend.
dc() { (cd "$COMPOSE_DIR" && sudo docker compose "$@"); }

# --- Docker ------------------------------------------------------------

ensure_docker() {
    if command -v docker >/dev/null && sudo docker compose version >/dev/null 2>&1; then
        echo "    docker $(docker --version | awk '{print $3}' | tr -d ,), compose есть"
        return 0
    fi

    log "Ставлю Docker"
    # Пакеты берутся из universe самой Ubuntu. Сторонний репозиторий и
    # `curl | sh` сюда не приходят: это чужой ключ и чужой скрипт в корне.
    sudo apt-get update -qq
    sudo apt-get install -y -qq docker.io docker-compose-v2
    sudo systemctl enable --now docker

    # Членство в группе docker действует со следующего входа, поэтому все
    # вызовы здесь идут через sudo. Добавляем на будущее, для рук человека.
    sudo usermod -aG docker "$USER" || true
    echo "    docker поставлен, для работы без sudo перезайдите в систему"
}

# --- окружение бэкенда -------------------------------------------------

# Собирает backend/.env, если его ещё нет, и дописывает недостающие строки.
#
# Файл в .gitignore и живёт только на сервере. Ключ подписи токенов создаётся
# один раз: новый ключ при каждой выкладке снимал бы сессии у всех, кто уже
# вошёл.
ensure_backend_env() {
    local env_file="$COMPOSE_DIR/.env"

    if [ ! -f "$env_file" ]; then
        log "Создаю backend/.env"
        cp "$COMPOSE_DIR/.env.example" "$env_file"
    fi

    set_env_line "$env_file" AUTH_SECRET "$(read_env "$env_file" AUTH_SECRET)"
    if [ -z "$(read_env "$env_file" AUTH_SECRET)" ]; then
        set_env_line "$env_file" AUTH_SECRET "$(openssl rand -base64 32 | tr -d '\n=' )"
        echo "    ключ подписи токенов создан"
    fi

    set_env_line "$env_file" TEST_STAND "$TEST_STAND"
    set_env_line "$env_file" SEED_PASSWORD "$SEED_PASSWORD"
    set_env_line "$env_file" LOG_LEVEL "${LOG_LEVEL:-INFO}"
    # Запросы приходят через nginx с того же адреса, поэтому CORS не нужен.
    # Строка остаётся на случай обращения к API напрямую с машины разработчика.
    set_env_line "$env_file" CORS_ORIGINS "https://$PRIMARY_HOST,http://$PRIMARY_HOST"
    # Пусто значит реестр MLflow не поднят и модель читается файлом. Без этой
    # строки compose подставит адрес несуществующей службы.
    set_env_line "$env_file" MLFLOW_TRACKING_URI ""
}

read_env() {
    sed -n "s/^$2=//p" "$1" 2>/dev/null | head -1
}

# Ставит переменную в .env: правит строку, если она есть, иначе дописывает.
set_env_line() {
    local file=$1 key=$2 value=$3
    if grep -q "^$key=" "$file"; then
        # Разделитель | вместо /: в значениях бывают адреса со слешами.
        sed -i "s|^$key=.*|$key=$value|" "$file"
    else
        printf '%s=%s\n' "$key" "$value" >>"$file"
    fi
}

# --- модель ------------------------------------------------------------

# Переносит обученную модель туда, откуда её читает сервис.
#
# Каталог artifacts лежит в .gitignore: модель весит полтора мегабайта и в
# истории репозитория ей не место. В git лежит результат обучения,
# `ml/access/out/model.joblib`, и выкладка копирует его под тем именем,
# которое ищет `app.ml.tracking`.
publish_model() {
    if [ ! -f "$MODEL_SOURCE/model.joblib" ]; then
        warn "Нет $MODEL_SOURCE/model.joblib — направление доступа останется без модели."
        return 0
    fi

    log "Публикую модель направления «несанкционированный доступ»"
    mkdir -p "$MODEL_TARGET"
    cp "$MODEL_SOURCE/model.joblib" "$MODEL_TARGET/latest.joblib"
    cp "$MODEL_SOURCE/metrics.json" "$MODEL_TARGET/metrics.json"

    # Калибратор появляется, только когда выигрывает по счёту Брайера.
    if [ -f "$MODEL_SOURCE/calibration.joblib" ]; then
        cp "$MODEL_SOURCE/calibration.joblib" "$MODEL_TARGET/calibration.joblib"
        echo "    модель и калибратор на месте"
    else
        rm -f "$MODEL_TARGET/calibration.joblib"
        echo "    модель на месте, калибратора нет — шкала сырая"
    fi
}

# --- службы ------------------------------------------------------------

# Поднимает базу и API. Реестр MLflow не поднимается: на стенде он не нужен,
# а памяти на сервере немного.
backend_up() {
    log "Поднимаю базу и API"
    dc up -d --build db api
    wait_for_api
}

wait_for_api() {
    local tries=40
    while [ "$tries" -gt 0 ]; do
        if curl -fsS "http://127.0.0.1:$API_PORT/healthz" >/dev/null 2>&1; then
            echo "    API отвечает на 127.0.0.1:$API_PORT"
            return 0
        fi
        sleep 2
        tries=$((tries - 1))
    done
    dc logs --tail 40 api || true
    die "API не поднялся за 80 секунд."
}

# Миграции, учётные записи, замер модели. Идемпотентно: повторный запуск
# применяет только новые миграции и обновляет те же записи.
backend_migrate() {
    log "Применяю миграции"
    dc run --rm pipeline uv run --no-sync python -m app.cli migrate
}

# Посев синтетики. Делается один раз: повторный посев не плодит дублей, но и
# не нужен, а прогон конвейера после него долгий.
#
# RESEED=yes сносит прежнюю синтетику и сеет заново. Это нужно, когда меняется
# сама раскладка данных, а не их количество: обычный посев вставляет с
# `on_conflict_do_nothing` и старые строки оставляет как есть.
backend_seed() {
    local fresh=""

    if [ "${RESEED:-no}" = yes ]; then
        warn "Пересев: прежние объекты, прогнозы и заявки будут сняты."
        fresh="--fresh"
    elif [ "$(prediction_count)" -gt 0 ]; then
        echo "    в базе уже есть прогнозы, посев пропущен"
        return 0
    fi

    log "Сею объекты и события"
    dc run --rm pipeline uv run --no-sync python -m app.cli seed $fresh --facilities "$SEED_FACILITIES"

    log "Публикую замер модели"
    dc run --rm pipeline uv run --no-sync python -m app.cli publish-metrics

    log "Считаю прогнозы"
    dc run --rm pipeline uv run --no-sync python -m app.cli run-pipeline
}

prediction_count() {
    dc exec -T db psql -U "${POSTGRES_USER:-arm}" -d "${POSTGRES_DB:-arm}" -tAc \
        'SELECT count(*) FROM prediction' 2>/dev/null | tr -d '[:space:]' || echo 0
}

# Возвращает демонстрационные данные в исходное состояние. Прогнозы снова
# новые, следы работы диспетчеров и бригад сняты.
backend_reset() {
    log "Возвращаю демонстрационные данные в исходное состояние"
    dc run --rm pipeline uv run --no-sync python -m app.cli reset-demo
}

backend_status() {
    log "Состояние бэкенда"
    dc ps --format '    {{.Service}}: {{.State}}' 2>/dev/null || dc ps
    printf '    прогнозов в базе: %s\n' "$(prediction_count)"
}
