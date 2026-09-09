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
}

ensure_dig() { command -v dig >/dev/null || sudo apt-get install -y -qq dnsutils; }

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
