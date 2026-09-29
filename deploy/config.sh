#!/usr/bin/env bash
#
# Общие настройки и помощники для deploy/install.sh и deploy/update.sh.
# Правьте значения здесь — оба скрипта подхватят.
#
# Сам по себе не запускается, только подключается через source.

############################## настройки ##############################

DOMAIN=utility-monitor.ru
EMAIL=drakon2034@gmail.com          # уведомления Let's Encrypt о проблемах с продлением
REPO=/home/user1/LCT/LCT2026
WEBROOT=/var/www/arm
SITE=arm-ods

# Что отдавать наружу:
#   static — собранный dist/, так и надо для показа;
#   proxy  — проксировать на dev-сервер Vite (порт ниже), удобно пока правите код.
SERVE_MODE=static
DEV_PORT=5173

# Что делать, когда домен ещё не направлен на сервер:
#   nipio — выпустить сертификат на <IP>.nip.io, получить рабочий HTTPS сразу;
#   plain — отдавать голый HTTP по IP. Моки при этом НЕ РАБОТАЮТ, см. предупреждение.
FALLBACK=nipio

# Непустой логин включает basic-auth на весь стенд.
BASIC_AUTH_USER=
BASIC_AUTH_PASS=

# Разворачивать ли бэкенд: Postgres и API в Docker плюс /api/v1 через nginx.
#   yes — полный сервис: настоящие прогнозы, вход, роли, заявки;
#   no  — только фронтенд на заглушках, как было до появления бэкенда.
DEPLOY_BACKEND=yes
API_PORT=8000

# Панель тестовых учёток на экране входа. Раздаёт логины и пароли любому, кто
# открыл страницу, поэтому включается осознанно и только на стенде для жюри.
TEST_STAND=true
SEED_PASSWORD=collector

# Сколько объектов сеять. 200 дают полсотни прогнозов и считаются за минуту.
SEED_FACILITIES=200
# Демонстрационная шкала уровней суточных направлений (ADR 0018). Пусто
# значит пороги моделей.
DEMO_LEVELS=true
# Журнал всех посчитанных прогнозов в backend/logs/forecasts.jsonl (ADR 0022).
# Поток пишет строки каждую минуту, файл растёт без ограничения. На маленьком
# сервере флаг выключен.
FORECAST_LOG=${FORECAST_LOG:-}

#######################################################################

NGINX_SITE_FILE="/etc/nginx/sites-available/$SITE"

log()  { printf '\n\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

# Обычные проверки на входе: не root, есть sudo, репозиторий на месте.
preflight() {
    [ "$(id -u)" != 0 ] || die "Запускать обычным пользователем, не root: sudo используется точечно."
    sudo -n true 2>/dev/null || sudo -v || die "Нужен sudo."
    [ -d "$REPO/frontend" ] || die "Не найден $REPO/frontend — поправьте REPO в deploy/config.sh."
    [ "$DEPLOY_BACKEND" != yes ] || [ -d "$REPO/backend" ]         || die "Не найден $REPO/backend, а DEPLOY_BACKEND=yes."
}

# Имя, которое сайт обслуживает снаружи. Нужно и конфигу nginx, и CORS.
# Пусто до того, как install.sh разберётся с DNS.
PRIMARY_HOST=${PRIMARY_HOST:-$DOMAIN}

ensure_dig() { command -v dig >/dev/null || sudo apt-get install -y -qq dnsutils; }

# Подходящий Node в PATH.
#
# nvm подключается из ~/.bashrc, а он при неинтерактивном входе не читается:
# `ssh host 'bash deploy/update.sh'` видит системный node (в Ubuntu 22.04 это
# v12) и сборка падает, хотя руками в терминале всё собирается. Берём самую
# свежую версию из nvm сами.
ensure_node() {
    if command -v node >/dev/null 2>&1; then
        local major
        major=$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)
        [ "${major:-0}" -ge 20 ] && return 0
    fi

    local newest
    newest=$(ls -d "$HOME"/.nvm/versions/node/v* 2>/dev/null | sort -V | tail -1)
    if [ -n "$newest" ] && [ -x "$newest/bin/node" ]; then
        PATH="$newest/bin:$PATH"
        export PATH
    fi
}

# Внешний адрес сервера. Без него не понять, указывает ли домен сюда.
public_ip() {
    local ip
    ip=$(curl -fsS --max-time 10 https://api.ipify.org 2>/dev/null || true)
    [ -n "$ip" ] || ip=$(hostname -I | awk '{print $1}')
    printf '%s' "$ip"
}

# A-запись имени через публичный резолвер, мимо локального кеша.
a_record() { dig +short A "$1" @1.1.1.1 2>/dev/null | head -1; }

# Какое имя обслуживает уже установленный сайт.
# Пусто — сайта нет; "_" — режим голого HTTP по IP.
current_host() {
    [ -f "$NGINX_SITE_FILE" ] || return 0
    grep -m1 -oP '^\s*server_name\s+\K[^;]+' "$NGINX_SITE_FILE" 2>/dev/null | awk '{print $1}'
}

# Развёрнут ли сайт вообще.
site_is_installed() {
    command -v nginx >/dev/null && [ -e "/etc/nginx/sites-enabled/$SITE" ]
}

# Код ответа с локального nginx. В режиме TLS ходим по https с --resolve:
# запрос заворачивается на петлю, но имя в сертификате проверяется по-настоящему.
probe() {
    local host=$1 path=$2
    if [ "$host" = "_" ] || [ -z "$host" ]; then
        curl -s -o /dev/null -w '%{http_code}' -H "Host: $(public_ip)" "http://127.0.0.1$path" || echo "---"
    else
        curl -s -o /dev/null -w '%{http_code}' \
            --resolve "$host:443:127.0.0.1" "https://$host$path" || echo "---"
    fi
}

# Готовит frontend/.env под выбранный режим.
#
# Значения Vite вшивает в бандл на этапе сборки, поэтому правка после
# `npm run build` ничего не меняет. Отсюда и место вызова: прямо перед сборкой.
#
# С бэкендом фронт ходит на /api/v1 того же адреса, и заглушки не поднимаются.
# Без бэкенда всё наоборот.
frontend_env() {
    local file="$REPO/frontend/.env"
    [ -f "$file" ] || cp "$REPO/frontend/.env.example" "$file"

    if [ "$DEPLOY_BACKEND" = yes ]; then
        set_env_line "$file" VITE_USE_MOCKS false
        set_env_line "$file" VITE_API_BASE_URL /api/v1
        set_env_line "$file" VITE_API_DOCS_URL /api/v1/docs
        echo "    сборка под настоящий бэкенд: VITE_USE_MOCKS=false"
    else
        set_env_line "$file" VITE_USE_MOCKS true
        echo "    сборка на заглушках: VITE_USE_MOCKS=true"
    fi
}

# Проверяет, что собралось то, что нужно режиму.
#
# Без бэкенда воркер заглушек обязателен: без него приложение молча остаётся
# без данных. С бэкендом он в бандле не нужен, и его отсутствие не ошибка.
check_bundle() {
    [ -f "$REPO/frontend/dist/index.html" ] || die "В dist/ нет index.html — сборка не удалась."
    if [ "$DEPLOY_BACKEND" != yes ] && [ ! -f "$REPO/frontend/dist/mockServiceWorker.js" ]; then
        die "В dist/ нет mockServiceWorker.js — заглушки не поднимутся."
    fi
}
