#!/usr/bin/env bash
#
# Выкладка новой версии фронтенда на уже развёрнутый сервер.
#
# Быстрый путь: без apt и без certbot, только пересборка и подмена файлов.
# nginx при этом не перезапускается — он читает статику с диска на каждый
# запрос, перезапуск нужен только под смену конфига или сертификата.
#
# Разбирается со всеми состояниями сервера:
#   ничего не развёрнуто        -> передаёт управление install.sh;
#   работает на <IP>.nip.io     -> просто обновляет статику;
#   домен доехал до сервера     -> сам зовёт install.sh за сертификатом на домен;
#   работает на своём домене    -> просто обновляет статику;
#   режим proxy на dev-сервер   -> собирать нечего, объясняет почему.
#
# Использование:
#   bash deploy/update.sh              собрать и выложить
#   bash deploy/update.sh --pull       сначала git pull --ff-only
#   bash deploy/update.sh --reinstall  прогнать install.sh целиком
#   bash deploy/update.sh --rollback   вернуть предыдущую версию
#
set -euo pipefail

DEPLOY_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
[ -f "$DEPLOY_DIR/config.sh" ] || { echo "Не найден $DEPLOY_DIR/config.sh" >&2; exit 1; }
# shellcheck source=config.sh
source "$DEPLOY_DIR/config.sh"

DO_PULL=no
FORCE_INSTALL=no
DO_ROLLBACK=no
for arg in "$@"; do
    case "$arg" in
        --pull)      DO_PULL=yes ;;
        --reinstall) FORCE_INSTALL=yes ;;
        --rollback)  DO_ROLLBACK=yes ;;
        -h|--help)   sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
        *)           die "Неизвестный аргумент: $arg" ;;
    esac
done

preflight

PREV="${WEBROOT}.prev"

# --- откат -------------------------------------------------------------

if [ "$DO_ROLLBACK" = yes ]; then
    [ -d "$PREV" ] || die "Нет $PREV — откатывать не на что."
    log "Возвращаю предыдущую версию"
    sudo rsync -a --delete "$PREV/" "$WEBROOT/"
    log "Откат выполнен"
    exit 0
fi

# --- случаи, когда нужна не выкладка, а полная установка ----------------

hand_over_to_install() {
    warn "$1"
    log "Передаю управление install.sh"
    exec bash "$DEPLOY_DIR/install.sh"
}

[ "$FORCE_INSTALL" = no ] || hand_over_to_install "Запрошена полная переустановка."

site_is_installed || hand_over_to_install "Сайт ещё не развёрнут на этом сервере."

ensure_dig
SERVED_HOST=$(current_host)
SERVER_IP=$(public_ip)

# Домен направили на сервер, а отдаём мы всё ещё nip.io или голый IP —
# нужен новый сертификат, это работа install.sh.
if [ "$(a_record "$DOMAIN")" = "$SERVER_IP" ] && [ "$SERVED_HOST" != "$DOMAIN" ]; then
    hand_over_to_install "$DOMAIN теперь указывает на $SERVER_IP, а отдаём ${SERVED_HOST:-ничего}."
fi

# --- обновление исходников ---------------------------------------------

cd "$REPO"
if [ "$DO_PULL" = yes ]; then
    log "Забираю изменения из git"
    if ! git diff --quiet || ! git diff --cached --quiet; then
        DIRTY=$(git status --porcelain --untracked-files=no)
        # npm install переписывает package-lock.json, дописывая платформенные
        # пакеты под текущую ОС (npm ci так не делает). На сервере лок — вход
        # сборки, источник истины в репозитории, поэтому такую правку откатываем.
        # Любые другие изменения не трогаем: на сервере правят код через VS Code
        # Remote, и молча их выбросить было бы хуже, чем остановиться.
        if [ "$(printf '%s\n' "$DIRTY" | wc -l)" -eq 1 ] &&
           printf '%s' "$DIRTY" | grep -q 'frontend/package-lock\.json$'; then
            warn "package-lock.json разошёлся с репозиторием — его переписал npm install."
            warn "Возвращаю версию из git: на сервере лок только читается."
            git checkout -- frontend/package-lock.json
        else
            printf '%s\n' "$DIRTY" | sed 's/^/    /'
            die "В рабочем дереве есть незакоммиченные правки (список выше). Закоммитьте или уберите их."
        fi
    fi
    git pull --ff-only
fi
echo "    версия: $(git -C "$REPO" log -1 --format='%h %s' 2>/dev/null || echo 'не git-репозиторий')"

# --- режим proxy: собирать нечего --------------------------------------

if grep -q 'proxy_pass' "$NGINX_SITE_FILE" 2>/dev/null; then
    log "Сайт настроен на проксирование к dev-серверу Vite"
    if ss -tln | grep -q ":$DEV_PORT "; then
        echo "    Vite слушает $DEV_PORT и подхватывает правки сам через HMR — выкладывать нечего."
    else
        warn "На $DEV_PORT никто не слушает. Запустите в $REPO/frontend: npm run dev"
    fi
    exit 0
fi

# --- сборка ------------------------------------------------------------

log "Собираю фронтенд"
ensure_node
command -v node >/dev/null || die "Не найден node."
NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]')
[ "$NODE_MAJOR" -ge 20 ] || die "Vite 8 требует Node 20.19+, здесь $(node -v)."

cd "$REPO/frontend"

# .env лежит в .gitignore. Без него VITE_USE_MOCKS не попадёт в бандл —
# соберётся молча, но приложение окажется без данных.
[ -f .env ] || { cp .env.example .env; echo "    создан .env из .env.example"; }

# npm ci долгий, гоняем его только когда правда менялись зависимости.
# npm сам кладёт node_modules/.package-lock.json при установке — сравниваем с ним.
if [ ! -d node_modules ] || [ package-lock.json -nt node_modules/.package-lock.json ]; then
    log "Зависимости изменились, ставлю заново"
    npm ci --no-audit --no-fund
else
    echo "    зависимости не менялись, npm ci пропущен"
fi

npm run build
[ -f dist/mockServiceWorker.js ] || die "В dist/ нет mockServiceWorker.js — заглушки не поднимутся."

# --- подмена статики ---------------------------------------------------

log "Выкладываю в $WEBROOT"

# Копия предыдущей версии — на случай, если новая окажется хуже.
if [ -d "$WEBROOT" ]; then
    sudo rm -rf "$PREV"
    sudo cp -a "$WEBROOT" "$PREV"
fi

# --delay-updates складывает всё во временные файлы и переименовывает их в
# конце, --delete-after убирает старое уже после переноса. Так момент, когда
# index.html ссылается на ещё не доехавшие assets, сжимается до минимума.
sudo mkdir -p "$WEBROOT"
sudo rsync -a --delete-after --delay-updates dist/ "$WEBROOT/"
sudo chown -R root:root "$WEBROOT"
sudo find "$WEBROOT" -type d -exec chmod 755 {} +
sudo find "$WEBROOT" -type f -exec chmod 644 {} +

# Конфиг не трогали, но перечитать его дёшево и страхует от ручных правок.
sudo nginx -t && sudo systemctl reload nginx

# --- проверка ----------------------------------------------------------

log "Проверяю, что отдаётся"
for path in / /mockServiceWorker.js; do
    printf '    %-24s %s\n' "$path" "$(probe "$SERVED_HOST" "$path")"
done

echo
if [ "$SERVED_HOST" = "_" ] || [ -z "$SERVED_HOST" ]; then
    log "Готово: http://$SERVER_IP"
else
    log "Готово: https://$SERVED_HOST"
fi
echo "    Откатиться на предыдущую версию: bash deploy/update.sh --rollback"
echo "    У зрителей обновится само: index.html и mockServiceWorker.js отдаются"
echo "    с no-cache, а имена файлов в assets/ содержат хеш содержимого."
