# Proxmox-Helper-Scripts

Diese drei Dateien erzeugen einen fertigen Debian-LXC mit installiertem
Foxtrail-Tracker. Sie folgen der Spezifikation von
[community-scripts](https://community-scripts.org) (`AGENTS.md` im Repo
[ProxmoxVED](https://github.com/community-scripts/ProxmoxVED)).

| Datei | Läuft wo | Aufgabe |
|---|---|---|
| `ct/foxtrail-tracker.sh` | Proxmox-Host | Legt den LXC an (1 CPU, 512 MB, 4 GB, Debian 13), enthält `update_script()` |
| `install/foxtrail-tracker-install.sh` | im Container | Python via `uv`, App nach `/opt/foxtrail-tracker`, Konfiguration, Admin, systemd |
| `json/foxtrail-tracker.json` | – | Metadaten für die Website (Kategorie, Port, Hinweise, `app_vars`) |

Kommentare und Ausgaben sind hier bewusst **englisch** – anders als im übrigen
Projekt. Die Dateien landen unverändert in einem englischsprachigen Fremd-Repo.

## Nutzung: Installation aus dem eigenen Fork

Das Framework (`community-scripts/core`) ist öffentlich und liest die
App-Dateien von der Adresse in `COMMUNITY_SCRIPTS_URL`. Damit laufen die
Scripts aus dem Fork [brunoz78/ProxmoxVED](https://github.com/brunoz78/ProxmoxVED),
ohne dass etwas eingereicht sein muss. Die drei Dateien liegen dort auf `main`
in `ct/`, `install/` und `json/`.

Auf der **Proxmox-Host-Shell**:

```bash
COMMUNITY_SCRIPTS_URL=https://raw.githubusercontent.com/brunoz78/ProxmoxVED/main bash -c "$(curl -fsSL https://raw.githubusercontent.com/brunoz78/ProxmoxVED/main/ct/foxtrail-tracker.sh)"
```

Eigene Zugangsdaten statt zufälligem Passwort:

```bash
var_admin_user=bruno var_admin_pass='geheim' COMMUNITY_SCRIPTS_URL=... bash -c "..."
```

Update im laufenden Container: `update` (ruft `update_script()` auf).

## Was im Container anders ist als bei `deploy/install.sh`

`deploy/install.sh` ist die Variante für einen von Hand eingerichteten LXC;
das community-script macht dasselbe, hält sich aber an dessen Konventionen:

- **Alles läuft als root**, kein Dienstbenutzer `foxtrail` (AGENTS.md,
  Anti-Pattern 9). `deploy/foxtrailctl` erkennt das und lässt `sudo` weg.
- **Python kommt von `uv`** (`setup_uv`, Python 3.13), nicht aus `apt`.
- **Code kommt aus dem neuesten GitHub-Release** (`fetch_and_deploy_gh_release`,
  Modus `tarball`), nie per `git pull`. Änderungen an der App wirken im LXC
  erst nach einem neuen Tag/Release:

  ```bash
  git tag -a v1.1.0 -m "..." && git push --tags && gh release create v1.1.0 --generate-notes
  ```

- Die systemd-Units werden im Install-Script erzeugt (Inhalt wie `deploy/`,
  nur `User=root`, ohne die Härtungs-Optionen).

Gleich bleiben die Pfade: `/opt/foxtrail-tracker`, `/var/lib/foxtrail-tracker`,
`/etc/foxtrail-tracker.env`, `/usr/local/bin/foxtrailctl`, Dienste `foxtrail`
und `foxtrail-sync.timer`.

## Pflege

**Dieser Ordner ist die Quelle, der Fork nur die Auslieferung.** Nach
Änderungen die drei Dateien in den Klon des Forks kopieren und pushen:

```bash
cp proxmox/ct/foxtrail-tracker.sh            ../ProxmoxVED/ct/
cp proxmox/install/foxtrail-tracker-install.sh ../ProxmoxVED/install/
cp proxmox/json/foxtrail-tracker.json        ../ProxmoxVED/json/
```

Verfügbare Framework-Funktionen immer im `core`-Repo prüfen
(`grep -n "^funktionsname() {" lib/*.func`), nicht in ProxmoxVE – die
Bibliotheken unterscheiden sich. Hintergründe zum Fork-Mechanismus und zu den
Aufnahmekriterien von community-scripts stehen ausführlich in
[wol-passkey/proxmox/README.md](https://github.com/brunoz78/wol-passkey/blob/main/proxmox/README.md).

## Bekannte Einschränkungen

- **Das Repo muss öffentlich sein.** Das Framework lädt das Release-Tarball
  ohne Token; bei einem privaten Repo bricht `fetch_and_deploy_gh_release` ab.
- **arm64 ist nicht getestet.** `var_arm64` ist deshalb auskommentiert; das
  Framework fragt beim Anlegen nach.
- **Das Admin-Passwort wird zufällig erzeugt** und am Ende angezeigt. Später
  nachschlagen vom Proxmox-Host aus:

  ```bash
  pct exec <CTID> -- cat /root/foxtrail-tracker.creds
  ```
