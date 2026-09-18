#!/bin/sh
# Start des Docker-Containers.
#   web (Standard)   Datenbank vorbereiten, Zeitplan-Prozess und Webserver starten
#   alles andere     wird als Befehl ausgefuehrt, z. B.  docker run ... foxtrailctl stats
set -eu

DATA_DIR=$(dirname "$FOXTRAIL_DB")

# Als root gestartet (Normalfall): Datenordner dem App-Benutzer geben und die Rechte abgeben.
# PUID/PGID passend zum Besitzer eines eingebundenen Host-Ordners setzen.
if [ "$(id -u)" = "0" ]; then
  mkdir -p "$DATA_DIR"
  find "$DATA_DIR" \( ! -user "$PUID" -o ! -group "$PGID" \) -exec chown "$PUID:$PGID" {} +
  exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$0" "$@"
fi
export HOME="$DATA_DIR"

# Session-Signatur: aus der Umgebung oder einmalig erzeugt und im Datenordner aufbewahrt,
# damit Anmeldungen einen Neustart ueberstehen.
if [ -z "${SECRET_KEY:-}" ]; then
  if [ ! -s "$DATA_DIR/secret_key" ]; then
    (umask 077; python -c 'import secrets; print(secrets.token_hex(32))' > "$DATA_DIR/secret_key")
  fi
  SECRET_KEY=$(cat "$DATA_DIR/secret_key")
  export SECRET_KEY
fi

if [ "${1:-web}" != "web" ]; then
  exec "$@"
fi

foxtrailctl init-db
foxtrailctl seed

if ! foxtrailctl list-users | grep -q ' admin '; then
  if [ -n "${FOXTRAIL_ADMIN_PASSWORD:-}" ]; then
    FOXTRAIL_PASSWORD="$FOXTRAIL_ADMIN_PASSWORD" foxtrailctl create-user admin --admin
  elif [ -z "$(foxtrailctl list-users)" ]; then
    echo "Noch kein Benutzer vorhanden. Administrator anlegen mit:"
    echo "   docker exec -it <container> foxtrailctl create-user admin --admin"
  fi
fi

# Woechentlicher Abgleich (ersetzt den systemd-Timer); startet neu, falls er abbricht
if [ "${FOXTRAIL_ZEITPLAN:-1}" = "1" ]; then
  (while true; do python /app/scripts/manage.py zeitplan || true; sleep 60; done) &
fi

exec gunicorn --bind "$BIND" --workers 2 --timeout 120 --access-logfile - wsgi:app
