#!/usr/bin/env bash
# =============================================================================
# Foxtrail-Tracker - Installation in einem Debian/Ubuntu-LXC (Proxmox)
#
# Voraussetzung: frischer Debian-12/13- oder Ubuntu-24.04-Container mit Netz.
# Aufruf als root IM Container:
#     bash deploy/install.sh
# Idempotent: kann zum Aktualisieren erneut ausgefuehrt werden (git pull, dann
# install.sh) - Datenbank und Konfiguration bleiben erhalten.
#
# Ergebnis:
#   /opt/foxtrail-tracker        Code + venv          (Besitzer root, lesbar)
#   /var/lib/foxtrail-tracker    SQLite-DB            (Besitzer foxtrail)
#   /etc/foxtrail-tracker.env    Konfiguration        (root:foxtrail, 640)
#   systemd: foxtrail.service (Web), foxtrail-sync.timer (woechentlicher Abgleich)
# =============================================================================
set -euo pipefail

APP_DIR=/opt/foxtrail-tracker
DATA_DIR=/var/lib/foxtrail-tracker
ENV_FILE=/etc/foxtrail-tracker.env
SVC_USER=foxtrail
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

[[ $EUID -eq 0 ]] || { echo "Bitte als root ausfuehren."; exit 1; }

echo "== Pakete"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip sudo >/dev/null

echo "== Systembenutzer $SVC_USER"
id -u "$SVC_USER" >/dev/null 2>&1 || useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$SVC_USER"
install -d -o "$SVC_USER" -g "$SVC_USER" -m 750 "$DATA_DIR"

echo "== Code nach $APP_DIR"
install -d "$APP_DIR"
if [[ "$SRC_DIR" != "$APP_DIR" ]]; then
  tar -C "$SRC_DIR" --exclude=.git --exclude=.venv --exclude='__pycache__' --exclude='*.pyc' \
      --exclude='data/*.db' --exclude='data/*.db-*' -cf - . | tar -C "$APP_DIR" -xf -
fi
chown -R root:"$SVC_USER" "$APP_DIR"
chmod -R o-rwx "$APP_DIR"

echo "== Python-venv"
[[ -x "$APP_DIR/.venv/bin/python" ]] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "== Konfiguration $ENV_FILE"
if [[ ! -f "$ENV_FILE" ]]; then
  sed "s/^SECRET_KEY=.*/SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')/" \
      "$APP_DIR/deploy/foxtrail-tracker.env.example" > "$ENV_FILE"
  chown root:"$SVC_USER" "$ENV_FILE"; chmod 640 "$ENV_FILE"
  echo "   neu erzeugt (SECRET_KEY zufaellig gesetzt)"
else
  echo "   vorhanden, unveraendert"
fi

echo "== CLI-Wrapper /usr/local/bin/foxtrailctl"
install -m 755 "$APP_DIR/deploy/foxtrailctl" /usr/local/bin/foxtrailctl

echo "== Datenbank + Seed"
set -a; . "$ENV_FILE"; set +a
foxtrailctl init-db
foxtrailctl seed

echo "== systemd"
install -m 644 "$APP_DIR/deploy/foxtrail.service" /etc/systemd/system/
install -m 644 "$APP_DIR/deploy/foxtrail-sync.service" /etc/systemd/system/
install -m 644 "$APP_DIR/deploy/foxtrail-sync.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now foxtrail.service foxtrail-sync.timer
systemctl restart foxtrail.service

echo
echo "== Fertig. Status:"
systemctl --no-pager --lines=0 status foxtrail.service | head -3
echo
if ! foxtrailctl list-users | grep -q admin; then
  echo "Noch kein Administrator vorhanden. Jetzt anlegen:"
  echo "   foxtrailctl create-user admin --admin"
fi
echo "Web-Oberflaeche: http://$(hostname -I | awk '{print $1}'):${BIND##*:}/"
