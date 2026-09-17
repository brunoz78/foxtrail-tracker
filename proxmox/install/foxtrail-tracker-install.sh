#!/usr/bin/env bash

# Copyright (c) 2021-2026 community-scripts ORG
# Author: brunoz78
# License: MIT | https://github.com/community-scripts/ProxmoxVED/raw/main/LICENSE
# Source: https://github.com/brunoz78/foxtrail-tracker

source /dev/stdin <<<"$FUNCTIONS_FILE_PATH"
color
verb_ip6
catch_errors
setting_up_container
network_check
update_os

UV_PYTHON="3.13" setup_uv

fetch_and_deploy_gh_release "foxtrail-tracker" "brunoz78/foxtrail-tracker" "tarball"

msg_info "Setting up Python Environment"
$STD uv venv --python 3.13 /opt/foxtrail-tracker/.venv
$STD uv pip install --python /opt/foxtrail-tracker/.venv -r /opt/foxtrail-tracker/requirements.txt
msg_ok "Set up Python Environment"

msg_info "Configuring Foxtrail-Tracker"
mkdir -p /var/lib/foxtrail-tracker
# The shipped example documents every setting; only the secret has to change.
sed "s|^SECRET_KEY=.*|SECRET_KEY=$(openssl rand -hex 32)|" \
  /opt/foxtrail-tracker/deploy/foxtrail-tracker.env.example >/etc/foxtrail-tracker.env
chmod 600 /etc/foxtrail-tracker.env
install -m 755 /opt/foxtrail-tracker/deploy/foxtrailctl /usr/local/bin/foxtrailctl
$STD foxtrailctl init-db
$STD foxtrailctl seed
msg_ok "Configured Foxtrail-Tracker"

msg_info "Creating Admin User"
ADMIN_USER="${var_admin_user:-admin}"
ADMIN_PASS="${var_admin_pass:-$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | cut -c1-16)}"
FOXTRAIL_PASSWORD="$ADMIN_PASS" $STD foxtrailctl create-user "$ADMIN_USER" --admin
{
  echo "Foxtrail-Tracker Admin Credentials"
  echo "URL: http://${LOCAL_IP}:8080"
  echo "Username: ${ADMIN_USER}"
  echo "Password: ${ADMIN_PASS}"
} >>~/foxtrail-tracker.creds
chmod 600 ~/foxtrail-tracker.creds
msg_ok "Created Admin User"

msg_info "Creating Services"
cat <<EOF >/etc/systemd/system/foxtrail.service
[Unit]
Description=Foxtrail-Tracker
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/foxtrail-tracker
EnvironmentFile=/etc/foxtrail-tracker.env
ExecStart=/opt/foxtrail-tracker/.venv/bin/gunicorn --bind \${BIND} --workers 2 --timeout 120 wsgi:app
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Weekly sync with foxtrail.ch; missed runs (container was off) are caught up.
cat <<EOF >/etc/systemd/system/foxtrail-sync.service
[Unit]
Description=Foxtrail-Tracker sync with foxtrail.ch
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=root
WorkingDirectory=/opt/foxtrail-tracker
EnvironmentFile=/etc/foxtrail-tracker.env
ExecStart=/opt/foxtrail-tracker/.venv/bin/python scripts/manage.py sync --ausloeser timer
EOF

cat <<EOF >/etc/systemd/system/foxtrail-sync.timer
[Unit]
Description=Weekly Foxtrail-Tracker sync with foxtrail.ch

[Timer]
OnCalendar=Mon *-*-* 04:30:00
RandomizedDelaySec=30min
Persistent=true

[Install]
WantedBy=timers.target
EOF
systemctl enable -q --now foxtrail foxtrail-sync.timer
msg_ok "Created Services"

motd_ssh
customize
cleanup_lxc
