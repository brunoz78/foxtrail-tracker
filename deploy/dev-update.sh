#!/usr/bin/env bash
# =============================================================================
# Foxtrail-Tracker - Test-LXC auf den Stand eines Branches bringen (ohne Release)
#
# Das community-script installiert immer das neueste GitHub-Release. Zum Testen
# VOR einem Release holt dieses Script den aktuellen Stand eines Branches
# (Standard: main) direkt nach /opt/foxtrail-tracker. Nur fuer die Testumgebung
# gedacht; "update" setzt spaeter wieder das neueste Release ein.
#
# Aufruf als root IM Container:
#     bash <(curl -fsSL https://raw.githubusercontent.com/brunoz78/foxtrail-tracker/main/deploy/dev-update.sh) [branch]
# oder, wenn das Script schon lokal liegt:
#     bash /opt/foxtrail-tracker/deploy/dev-update.sh [branch]
#
# Datenbank (/var/lib/foxtrail-tracker) und Konfiguration (/etc/foxtrail-tracker.env)
# bleiben unangetastet.
# =============================================================================
set -euo pipefail

BRANCH="${1:-main}"
REPO="${FOXTRAIL_REPO:-brunoz78/foxtrail-tracker}"
APP_DIR=/opt/foxtrail-tracker

[[ $EUID -eq 0 ]] || { echo "Bitte als root ausfuehren."; exit 1; }
[[ -d "$APP_DIR/.venv" ]] || { echo "Keine Installation unter $APP_DIR gefunden."; exit 1; }

echo "== $REPO @ $BRANCH -> $APP_DIR"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
curl -fsSL "https://github.com/$REPO/archive/refs/heads/$BRANCH.tar.gz" | tar -xz -C "$tmp" --strip-components=1
# Code drueberkopieren; .venv und alles ausserhalb von /opt bleiben erhalten
(cd "$tmp" && tar -cf - --exclude=.git .) | tar -xf - -C "$APP_DIR"

echo "== Python-Abhaengigkeiten"
if command -v uv >/dev/null 2>&1; then
  uv pip install -q --python "$APP_DIR/.venv" -r "$APP_DIR/requirements.txt"
else
  "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
fi

echo "== CLI, Schema-Migration, Neustart"
install -m 755 "$APP_DIR/deploy/foxtrailctl" /usr/local/bin/foxtrailctl
foxtrailctl init-db
foxtrailctl seed      # nur neue Trails und fehlende Seed-Werte (z. B. neu_seit), nichts wird ueberschrieben
systemctl restart foxtrail

commit="$(curl -fsSL "https://api.github.com/repos/$REPO/commits/$BRANCH" 2>/dev/null \
  | sed -n 's/^ *"sha": "\([0-9a-f]\{7\}\).*/\1/p' | head -n1 || true)"
echo "$BRANCH${commit:+@$commit}" >"$HOME/.foxtrail-tracker-dev"
echo
echo "== Fertig: $BRANCH${commit:+ (Commit $commit)}"
systemctl --no-pager --lines=0 status foxtrail | head -3
