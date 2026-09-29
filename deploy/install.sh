#!/usr/bin/env bash
#
# Развёртывание АРМ диспетчера ОДС на Ubuntu 22.04.
#
# Ставит сервис целиком: Postgres и API в Docker, статику фронтенда, nginx с
# сертификатом. Путь /api/v1 проксируется на API, поэтому фронт и сервер живут
# на одном адресе и CORS не участвует.
#
# Бэкенд отключается строкой DEPLOY_BACKEND=no в config.sh: тогда собирается
# только фронтенд и работает на заглушках.
#
# Идемпотентен: запускать сколько угодно раз. Сам решает, что делать с TLS:
#   - A-запись $DOMAIN указывает на этот сервер -> сертификат на домен;
#   - записи нет или она чужая         -> запасной вариант, см. FALLBACK.
#
# Запускать НЕ от root, обычным пользователем с sudo:
#   bash deploy/install.sh
#
set -euo pipefail

# Настройки и общие помощники — в deploy/config.sh, они же используются update.sh.
DEPLOY_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
[ -f "$DEPLOY_DIR/config.sh" ] || { echo "Не найден $DEPLOY_DIR/config.sh" >&2; exit 1; }
# shellcheck source=config.sh
source "$DEPLOY_DIR/config.sh"
# shellcheck source=backend.sh
source "$DEPLOY_DIR/backend.sh"

# --- проверки окружения ------------------------------------------------

preflight

# --- кто мы снаружи ----------------------------------------------------

log "Определяю внешний адрес"
SERVER_IP=$(public_ip)
[ -n "$SERVER_IP" ] || die "Не удалось определить внешний IP."
echo "    внешний IP: $SERVER_IP"

# --- решаем, какой домен обслуживаем -----------------------------------

log "Проверяю DNS для $DOMAIN"
ensure_dig
A_APEX=$(a_record "$DOMAIN")
A_WWW=$(a_record "www.$DOMAIN")
echo "    $DOMAIN     -> ${A_APEX:-нет записи}"
echo "    www.$DOMAIN -> ${A_WWW:-нет записи}"

CERT_HOSTS=()
if [ "$A_APEX" = "$SERVER_IP" ]; then
    PRIMARY_HOST=$DOMAIN
    CERT_HOSTS+=("$DOMAIN")
    [ "$A_WWW" = "$SERVER_IP" ] && CERT_HOSTS+=("www.$DOMAIN")
    USE_TLS=yes
    echo "    домен указывает сюда — берём сертификат на него"
elif [ "$FALLBACK" = nipio ]; then
    PRIMARY_HOST="${SERVER_IP}.nip.io"
    CERT_HOSTS+=("$PRIMARY_HOST")
    USE_TLS=yes
    warn "Домен пока не направлен на сервер. Временно поднимаю HTTPS на $PRIMARY_HOST."
    warn "Направите DNS — запустите скрипт снова, он сам переедет на $DOMAIN."
else
    PRIMARY_HOST="$SERVER_IP"
    USE_TLS=no
    warn "Режим голого HTTP по IP."
    warn "Service worker в незащищённом контексте не регистрируется, поэтому заглушки"
    warn "не поднимутся и страница будет пустой. Каждому зрителю придётся внести"
    warn "http://$SERVER_IP в chrome://flags/#unsafely-treat-insecure-origin-as-secure."
fi

# --- пакеты ------------------------------------------------------------

log "Ставлю пакеты"
sudo apt-get update -qq
sudo apt-get install -y -qq nginx rsync
if [ "$USE_TLS" = yes ]; then
    sudo apt-get install -y -qq certbot python3-certbot-nginx
fi

# --- бэкенд ------------------------------------------------------------

# Поднимается до nginx: конфиг будет проксировать на него, и пустой upstream
# дал бы 502 на первом же запросе после перезагрузки конфига.
if [ "$DEPLOY_BACKEND" = yes ]; then
    ensure_docker
    ensure_backend_env
    publish_model
    backend_up
    backend_migrate
    backend_metrics
    backend_districts
    backend_seed
    backend_stream
else
    warn "DEPLOY_BACKEND=no: фронтенд будет работать на заглушках."
fi

# --- сборка ------------------------------------------------------------

if [ "$SERVE_MODE" = static ]; then
    log "Собираю фронтенд"
    ensure_node
    command -v node >/dev/null || die "Не найден node."
    NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]')
    [ "$NODE_MAJOR" -ge 20 ] || die "Vite 8 требует Node 20.19+, здесь $(node -v)."
    echo "    node $(node -v), npm $(npm -v)"

    cd "$REPO/frontend"

    # .env лежит в .gitignore, после клона его нет. Значения из него Vite
    # вшивает в бандл на этапе сборки, поэтому править его надо до npm run build.
    [ -f .env ] || { cp .env.example .env; echo "    создан .env из .env.example"; }
    frontend_env

    npm ci --no-audit --no-fund
    npm run build
    check_bundle

    log "Раскладываю в $WEBROOT"
    sudo mkdir -p "$WEBROOT"
    sudo rsync -a --delete-after --delay-updates dist/ "$WEBROOT/"
    sudo chown -R root:root "$WEBROOT"
    sudo find "$WEBROOT" -type d -exec chmod 755 {} +
    sudo find "$WEBROOT" -type f -exec chmod 644 {} +
else
    log "Режим proxy: статика не собирается, nginx проксирует на 127.0.0.1:$DEV_PORT"
    ss -tln | grep -q ":$DEV_PORT " || warn "На $DEV_PORT никто не слушает. Запустите npm run dev в $REPO/frontend."
    warn "Vite отобьёт чужой Host. Добавьте в vite.config.ts:"
    warn "  server: { allowedHosts: ['$PRIMARY_HOST'] }"
fi

# --- basic-auth --------------------------------------------------------

AUTH_SNIPPET=""
if [ -n "$BASIC_AUTH_USER" ]; then
    log "Включаю basic-auth"
    [ -n "$BASIC_AUTH_PASS" ] || die "BASIC_AUTH_USER задан, а BASIC_AUTH_PASS пуст."
    printf '%s:%s\n' "$BASIC_AUTH_USER" "$(openssl passwd -apr1 "$BASIC_AUTH_PASS")" \
        | sudo tee /etc/nginx/.htpasswd >/dev/null
    sudo chmod 640 /etc/nginx/.htpasswd
    sudo chown root:www-data /etc/nginx/.htpasswd
    AUTH_SNIPPET='auth_basic "ARM ODS"; auth_basic_user_file /etc/nginx/.htpasswd;'
fi

# --- конфиг nginx ------------------------------------------------------

log "Пишу конфиг nginx"

# Проксирование API. Фронт ходит на /api/v1 того же адреса, поэтому CORS не
# участвует вовсе, а порт 8000 остаётся закрытым снаружи: он опубликован
# только на петле.
#
# basic-auth сюда не ставится намеренно. У сервиса свой вход с ролями и вторым
# фактором, и второе окно поверх него только мешает.
API_LOCATION=""
if [ "$DEPLOY_BACKEND" = yes ]; then
    read -r -d '' API_LOCATION <<NGINX || true

    location /api/v1 {
        proxy_pass http://127.0.0.1:$API_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        # Прогон конвейера через API не идёт, но выгрузка журнала бывает
        # долгой. Минуты хватает с запасом.
        proxy_read_timeout 60s;
    }

    location = /healthz {
        proxy_pass http://127.0.0.1:$API_PORT;
        access_log off;
    }
NGINX
fi

if [ "$SERVE_MODE" = static ]; then
    read -r -d '' LOCATION_ROOT <<NGINX || true
    root $WEBROOT;
    index index.html;

    # Бандл крупный: handlers ~787 КБ, MapScreen ~1 МБ. Без gzip карта грузится долго.
    gzip on;
    gzip_comp_level 6;
    gzip_min_length 1024;
    gzip_types text/css application/javascript application/json image/svg+xml;

    # Имена с хешем — кэшируем навсегда.
    location /assets/ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    # nginx 1.18 не знает расширения .mjs и отдаёт его как
    # application/octet-stream. Браузер отказывается исполнять модульный
    # воркер с таким типом, а MapLibre разбирает тайлы именно в нём — карта
    # молча остаётся пустой. Пустой types{} плюс default_type переопределяют
    # тип для одного расширения, не трогая пакетный /etc/nginx/mime.types.
    # Регулярное расположение приоритетнее префиксного /assets/, поэтому
    # заголовки кэширования повторены здесь.
    location ~* \.mjs\$ {
        types { }
        default_type application/javascript;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    # Эти два обязаны обновляться сразу, иначе после передеплоя увидите старое.
    location = /mockServiceWorker.js { add_header Cache-Control "no-cache"; }
    location = /index.html          { add_header Cache-Control "no-cache"; }

    # Роуты /map, /journal, /orders — клиентские. Без fallback F5 на них даст 404.
    location / {
        $AUTH_SNIPPET
        try_files \$uri /index.html;
    }
$API_LOCATION
NGINX
else
    read -r -d '' LOCATION_ROOT <<NGINX || true
$API_LOCATION
    location / {
        $AUTH_SNIPPET
        proxy_pass http://127.0.0.1:$DEV_PORT;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;      # HMR ходит по websocket
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 1d;
    }
NGINX
fi

if [ "$USE_TLS" = yes ]; then
    SERVER_NAMES="${CERT_HOSTS[*]}"
    LISTEN_DIRECTIVE="listen 80;"
else
    SERVER_NAMES="_"
    LISTEN_DIRECTIVE="listen 80 default_server;"
fi

sudo tee "/etc/nginx/sites-available/$SITE" >/dev/null <<NGINX
# Сгенерирован deploy/install.sh. Правки затрутся при следующем запуске,
# кроме строк, которые допишет certbot.
server {
    $LISTEN_DIRECTIVE
    server_name $SERVER_NAMES;

$LOCATION_ROOT
}
NGINX

sudo ln -sfn "/etc/nginx/sites-available/$SITE" "/etc/nginx/sites-enabled/$SITE"
sudo rm -f /etc/nginx/sites-enabled/default
# Редирект с голого IP пересоздаётся ниже, только если сертификат выпустился.
# Иначе он столкнётся с нашим же listen 80 default_server в запасном режиме.
sudo rm -f /etc/nginx/sites-enabled/00-ip-redirect

sudo nginx -t
sudo systemctl enable --now nginx >/dev/null
sudo systemctl reload nginx

# --- фаервол -----------------------------------------------------------

# ufw специально не включаем: он неактивен, а включение по SSH рискует
# отрезать сессию. Если уже работает — просто открываем порты.
if command -v ufw >/dev/null && sudo ufw status | grep -q '^Status: active'; then
    log "ufw активен, открываю 80 и 443"
    sudo ufw allow 80,443/tcp >/dev/null
fi

# --- сертификат --------------------------------------------------------

if [ "$USE_TLS" = yes ]; then
    log "Выпускаю сертификат: ${CERT_HOSTS[*]}"
    CERT_ARGS=()
    for h in "${CERT_HOSTS[@]}"; do CERT_ARGS+=(-d "$h"); done

    if sudo certbot --nginx --non-interactive --agree-tos --no-eff-email \
            -m "$EMAIL" --keep-until-expiring --redirect "${CERT_ARGS[@]}"; then

        # Голый IP тоже должен работать: уводим его на имя с валидным сертификатом.
        sudo tee /etc/nginx/sites-available/00-ip-redirect >/dev/null <<NGINX
# Кто набрал адрес цифрами — отправляем на origin, где сертификат валиден.
server {
    listen 80 default_server;
    server_name _;
    return 301 https://$PRIMARY_HOST\$request_uri;
}
NGINX
        sudo ln -sfn /etc/nginx/sites-available/00-ip-redirect /etc/nginx/sites-enabled/00-ip-redirect
        sudo nginx -t && sudo systemctl reload nginx

        log "Проверяю автопродление"
        sudo certbot renew --dry-run
        systemctl list-timers certbot.timer --no-pager 2>/dev/null | head -2 || true
    else
        USE_TLS=no
        warn "Certbot не смог выпустить сертификат."
        warn "Чаще всего это закрытый снаружи порт 80 — проверьте фаервол провайдера."
        warn "Сайт остаётся доступен по http://$SERVER_IP, заглушки там работать не будут."
    fi
fi

# --- проверка ----------------------------------------------------------

log "Проверяю, что отдаётся"
# По HTTP после выпуска сертификата ответ всегда 301 — это редирект, который
# поставил certbot. Поэтому в режиме TLS стучимся сразу по https, --resolve
# заворачивает запрос на локальный nginx, но имя в сертификате всё равно
# проверяется по-настоящему.
CHECK_PATHS=(/)
if [ "$DEPLOY_BACKEND" = yes ]; then
    CHECK_PATHS+=(/healthz /api/v1/meta)
else
    CHECK_PATHS+=(/mockServiceWorker.js)
fi

for path in "${CHECK_PATHS[@]}"; do
    if [ "$USE_TLS" = yes ]; then
        code=$(curl -s -o /dev/null -w '%{http_code}' \
            --resolve "$PRIMARY_HOST:443:127.0.0.1" "https://$PRIMARY_HOST$path" || echo "---")
    else
        code=$(curl -s -o /dev/null -w '%{http_code}' \
            -H "Host: $SERVER_IP" "http://127.0.0.1$path" || echo "---")
    fi
    printf '    %-24s %s\n' "$path" "$code"
done

echo
if [ "$USE_TLS" = yes ]; then
    log "Готово: https://$PRIMARY_HOST"
    [ "$PRIMARY_HOST" != "$DOMAIN" ] && \
        echo "    Когда A-запись $DOMAIN укажет на $SERVER_IP — запустите скрипт снова."
    echo "    Продление автоматическое: таймер certbot будит клиент дважды в сутки"
    echo "    и обновляет сертификат за 30 дней до конца его 90-дневного срока."
else
    log "Готово: http://$SERVER_IP"
    warn "Без HTTPS заглушки не поднимутся. Чтобы посмотреть, внесите http://$SERVER_IP"
    warn "в chrome://flags/#unsafely-treat-insecure-origin-as-secure и перезапустите Chrome."
fi
echo
if [ "$DEPLOY_BACKEND" = yes ]; then
    backend_status
    echo
    echo "    Учётные записи стенда: панель справа на экране входа."
    echo "    Логи API:   cd $REPO/backend && sudo docker compose logs -f api"
    echo "    Сброс демо: cd $REPO/backend && sudo docker compose run --rm pipeline \\"
    echo "                  uv run --no-sync python -m app.cli reset-demo"
fi
echo
echo "    Логи nginx: sudo tail -f /var/log/nginx/{access,error}.log"
echo "    Передеплой после правок: bash deploy/update.sh --pull"
