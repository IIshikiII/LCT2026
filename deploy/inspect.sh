#!/usr/bin/env bash
# Разведка сервера перед развёртыванием АРМ ОДС на utility-monitor.ru.
# Только чтение: ничего не устанавливает, не правит и не перезапускает.
# По выводу собирается install.sh под этот сервер.

DOMAIN=utility-monitor.ru

exec 2>&1
have() { command -v "$1" >/dev/null 2>&1; }
sec() { printf '\n===== %s =====\n' "$1"; }
ver() { if have "$1"; then printf '%-10s %s\n' "$1" "$("$@" 2>&1 | head -1)"; else printf '%-10s НЕТ\n' "$1"; fi; }

printf 'Отчёт собран: %s\n' "$(date -Is)"

sec "ОС и ядро"
grep -E '^(PRETTY_NAME|ID|VERSION_ID)=' /etc/os-release 2>/dev/null || echo "нет /etc/os-release"
uname -srm
have systemctl && echo "init: systemd" || echo "init: НЕ systemd (важно для автопродления)"

sec "Пользователь и права"
echo "пользователь: $(whoami)  ($(id -un):$(id -gn))"
if [ "$(id -u)" = 0 ]; then
  echo "sudo: не нужен, уже root"
elif sudo -n true 2>/dev/null; then
  echo "sudo: есть без пароля"
else
  echo "sudo: есть, но спросит пароль (или отсутствует)"
fi

sec "Ресурсы"
free -h 2>/dev/null | head -2
df -h / /var 2>/dev/null | sort -u
echo "ядер CPU: $(nproc 2>/dev/null)"

sec "Пакетный менеджер"
for pm in apt-get dnf yum apk pacman; do have $pm && echo "есть: $pm"; done

sec "Что уже установлено"
ver nginx nginx -v
ver certbot certbot --version
ver node node -v
ver npm npm -v
ver git git --version
ver curl curl --version
ver rsync rsync --version
ver snap snap version
ver docker docker --version
ver dig dig -v
ver openssl openssl version

sec "nginx: раскладка конфигов"
if have nginx; then
  for d in /etc/nginx/sites-available /etc/nginx/sites-enabled /etc/nginx/conf.d; do
    if [ -d "$d" ]; then echo "-- $d"; ls -1 "$d" 2>/dev/null | sed 's/^/   /'; fi
  done
  echo "-- nginx -t"
  if [ "$(id -u)" = 0 ]; then nginx -t; else sudo -n nginx -t 2>/dev/null || echo "   (нужен sudo с паролем, пропущено)"; fi
  echo "-- активен ли сервис"
  systemctl is-active nginx 2>/dev/null
  systemctl is-enabled nginx 2>/dev/null
else
  echo "nginx не установлен"
fi

sec "Занятые порты (ищем конфликт на 80/443)"
if have ss; then
  ( sudo -n ss -tlnp 2>/dev/null || ss -tln ) | awk 'NR==1 || /:80 |:443 |:5173 /'
  echo "-- все слушающие TCP"
  ( sudo -n ss -tlnp 2>/dev/null || ss -tln ) | tail -n +2
elif have netstat; then
  netstat -tln
else
  echo "нет ни ss, ни netstat"
fi

sec "Firewall"
if have ufw; then echo "-- ufw"; ( sudo -n ufw status verbose 2>/dev/null || ufw status 2>/dev/null || echo "   нужен sudo" ); fi
if have firewall-cmd; then echo "-- firewalld"; ( sudo -n firewall-cmd --list-all 2>/dev/null || echo "   нужен sudo" ); fi
if have nft; then echo "-- nftables (счётчик правил)"; ( sudo -n nft list ruleset 2>/dev/null | wc -l || echo "   нужен sudo" ); fi
if have iptables; then echo "-- iptables, правила про 80/443"; ( sudo -n iptables -S 2>/dev/null | grep -E 'dport (80|443)' || echo "   нет явных правил или нужен sudo" ); fi

sec "SELinux / AppArmor"
have getenforce && echo "SELinux: $(getenforce)" || echo "SELinux: не установлен"
have aa-status && ( sudo -n aa-status --enabled 2>/dev/null && echo "AppArmor: включён" || echo "AppArmor: есть, статус не прочитан" ) || echo "AppArmor: не установлен"

sec "Сеть: адреса сервера"
echo "локальные: $(hostname -I 2>/dev/null)"
echo "внешний (ipify):    $(curl -s --max-time 10 https://api.ipify.org 2>/dev/null)"
echo "внешний (ifconfig): $(curl -s --max-time 10 https://ifconfig.me 2>/dev/null)"

sec "DNS для $DOMAIN"
resolve() {
  local name=$1
  if have dig; then
    echo "$name A  -> $(dig +short A "$name" @1.1.1.1 2>/dev/null | tr '\n' ' ')"
    echo "$name AAAA-> $(dig +short AAAA "$name" @1.1.1.1 2>/dev/null | tr '\n' ' ')"
  elif have host; then
    host "$name" 1.1.1.1 2>/dev/null | sed 's/^/   /'
  elif have getent; then
    getent ahosts "$name" | sed 's/^/   /'
  else
    echo "   нечем резолвить (нет dig/host/getent)"
  fi
}
resolve "$DOMAIN"
resolve "www.$DOMAIN"

sec "Сходится ли DNS с внешним IP"
MYIP=$(curl -s --max-time 10 https://api.ipify.org 2>/dev/null)
if have dig && [ -n "$MYIP" ]; then
  DNSIP=$(dig +short A "$DOMAIN" @1.1.1.1 2>/dev/null | head -1)
  echo "внешний IP сервера: ${MYIP:-неизвестен}"
  echo "A-запись домена:    ${DNSIP:-нет записи}"
  if [ -n "$DNSIP" ] && [ "$MYIP" = "$DNSIP" ]; then
    echo "ВЕРДИКТ: совпадают, Let's Encrypt пройдёт"
  else
    echo "ВЕРДИКТ: НЕ совпадают — до правки DNS сертификат не выпустится"
  fi
else
  echo "проверка пропущена (нет dig или не определился внешний IP)"
fi

sec "Исходящий интернет (нужен для apt и ACME)"
for u in https://deb.debian.org https://acme-v02.api.letsencrypt.org/directory; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$u" 2>/dev/null)
  echo "$u -> ${code:-нет ответа}"
done

sec "Существующие сертификаты"
if [ -d /etc/letsencrypt/live ]; then
  ( sudo -n ls -1 /etc/letsencrypt/live 2>/dev/null || ls -1 /etc/letsencrypt/live 2>/dev/null ) | sed 's/^/   /'
  have certbot && ( sudo -n certbot certificates 2>/dev/null | grep -E 'Certificate Name|Domains|Expiry' | sed 's/^/   /' )
else
  echo "нет /etc/letsencrypt/live — сертификатов ещё не выпускалось"
fi

sec "Автопродление: что уже настроено"
systemctl list-timers 2>/dev/null | grep -Ei 'certbot|renew' || echo "таймеров certbot нет"
ls -1 /etc/cron.d 2>/dev/null | grep -i certbot || echo "cron-задач certbot нет"
have snap && ( snap list 2>/dev/null | grep -i certbot || true )

sec "Время (перекос ломает выпуск сертификата)"
( timedatectl 2>/dev/null | grep -Ei 'Time zone|synchronized|Local time' ) || date

sec "Есть ли проект на сервере"
ls -d ~/LCT2026 ~/lct2026 /srv/* /var/www/* 2>/dev/null | sed 's/^/   /' || true
echo "домашний каталог: $HOME"

printf '\n===== конец отчёта =====\n'
