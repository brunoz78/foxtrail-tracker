#!/usr/bin/env bash
# Engine comes from community-scripts/core; this repo only ships the scripts.
# A local core checkout wins (COMMUNITY_SCRIPTS_CORE_DIR, else a sibling ../core),
# so a fork or branch of core can be tested without editing this file.
_cs_boot="${COMMUNITY_SCRIPTS_CORE_DIR:-$(dirname "${BASH_SOURCE[0]}")/../../core}/core/build.func"
source "$_cs_boot" 2>/dev/null || source <(curl -fsSL "${COMMUNITY_SCRIPTS_CORE_URL:-https://raw.githubusercontent.com/community-scripts/core/main}/core/build.func")
# Copyright (c) 2021-2026 community-scripts ORG
# Author: brunoz78
# License: MIT | https://github.com/community-scripts/ProxmoxVED/raw/main/LICENSE
# Source: https://github.com/brunoz78/foxtrail-tracker

APP="Foxtrail-Tracker"
var_tags="${var_tags:-leisure;tracker}"
var_cpu="${var_cpu:-1}"
var_ram="${var_ram:-512}"
var_disk="${var_disk:-4}"
var_os="${var_os:-debian}"
var_version="${var_version:-13}"
#var_arm64="${var_arm64:-no}" # unset = ask the user; set yes/no only when verified
var_unprivileged="${var_unprivileged:-1}"

# Values the install script accepts up front (see json app_vars).
# Without the export they never reach the container.
export var_admin_user="${var_admin_user:-}"
export var_admin_pass="${var_admin_pass:-}"

header_info "$APP"
variables
color
catch_errors

function update_script() {
  header_info
  check_container_storage
  check_container_resources

  if [[ ! -d /opt/foxtrail-tracker ]]; then
    msg_error "No ${APP} Installation Found!"
    exit
  fi

  if check_for_gh_release "foxtrail-tracker" "brunoz78/foxtrail-tracker"; then
    msg_info "Stopping Services"
    systemctl stop foxtrail foxtrail-sync.timer
    msg_ok "Stopped Services"

    # Config and database live outside /opt and survive CLEAN_INSTALL;
    # the copy is a safety net in case the update aborts halfway.
    create_backup /etc/foxtrail-tracker.env /var/lib/foxtrail-tracker

    CLEAN_INSTALL=1 fetch_and_deploy_gh_release "foxtrail-tracker" "brunoz78/foxtrail-tracker" "tarball"

    restore_backup

    msg_info "Updating Python Environment"
    $STD uv venv --python 3.13 /opt/foxtrail-tracker/.venv
    $STD uv pip install --python /opt/foxtrail-tracker/.venv -r /opt/foxtrail-tracker/requirements.txt
    install -m 755 /opt/foxtrail-tracker/deploy/foxtrailctl /usr/local/bin/foxtrailctl
    # init-db applies schema migrations; seed only adds trails/values that are missing.
    $STD foxtrailctl init-db
    $STD foxtrailctl seed
    msg_ok "Updated Python Environment"

    msg_info "Starting Services"
    systemctl start foxtrail foxtrail-sync.timer
    msg_ok "Started Services"
    msg_ok "Updated successfully!"
  fi
  exit
}

start
build_container
description

msg_ok "Completed Successfully!\n"
echo -e "${CREATING}${GN}${APP} setup has been successfully initialized!${CL}"
echo -e "${INFO}${YW}Access it using the following URL:${CL}"
echo -e "${GATEWAY}${BGN}http://${IP}:8080${CL}"
echo -e "${INFO}${YW}Admin login (also stored in ~/foxtrail-tracker.creds inside the container):${CL}"
echo -e "${GATEWAY}${BGN}$(pct exec "$CTID" -- sed -n 's/^Username: //p' /root/foxtrail-tracker.creds) / $(pct exec "$CTID" -- sed -n 's/^Password: //p' /root/foxtrail-tracker.creds)${CL}"
