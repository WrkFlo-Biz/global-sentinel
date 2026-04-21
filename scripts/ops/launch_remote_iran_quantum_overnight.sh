#!/bin/zsh

set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

VM_HOST="${VM_HOST:-openclaw@20.124.180.8}"
REMOTE_ROOT="${REMOTE_ROOT:-/opt/global-sentinel}"
RESOURCE_GROUP="${RESOURCE_GROUP:-openclaw-rg}"
VM_NAME="${VM_NAME:-openclaw-gateway-vm}"
LOCAL_RUNNER="${LOCAL_RUNNER:-/Users/mosestut/global-sentinel/scripts/ops/iran_disruption_quantum_overnight.py}"
REMOTE_RUNNER="${REMOTE_RUNNER:-$REMOTE_ROOT/scripts/ops/iran_disruption_quantum_overnight.py}"
REMOTE_SERVICE_NAME="${REMOTE_SERVICE_NAME:-iran-disruption-quantum.service}"
REMOTE_SERVICE_PATH="${REMOTE_SERVICE_PATH:-/etc/systemd/system/$REMOTE_SERVICE_NAME}"
JOB_LOG="${JOB_LOG:-$REMOTE_ROOT/logs/iran_disruption_quantum_overnight.out}"
JOB_ERR_LOG="${JOB_ERR_LOG:-$REMOTE_ROOT/logs/iran_disruption_quantum_overnight.err}"
LAUNCHER_LOG="${LAUNCHER_LOG:-/Users/mosestut/global-sentinel/logs/iran_disruption_quantum_launcher.log}"
SLEEP_SECONDS="${SLEEP_SECONDS:-60}"

mkdir -p "$(dirname "$LAUNCHER_LOG")"

log() {
  printf '[%s] %s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$*" >>"$LAUNCHER_LOG"
}

service_unit() {
  cat <<EOF
[Unit]
Description=Iran Disruption Quantum Research Runner
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=openclaw
WorkingDirectory=${REMOTE_ROOT}
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 -u ${REMOTE_RUNNER} --repo-root ${REMOTE_ROOT} --iterations 0 --sleep 900 --mode full --retrain-every 6
Restart=always
RestartSec=30
TimeoutStopSec=30
StandardOutput=append:${JOB_LOG}
StandardError=append:${JOB_ERR_LOG}

[Install]
WantedBy=multi-user.target
EOF
}

log "launcher started"

while true; do
  if status_output="$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$VM_HOST" "sudo -n systemctl is-active ${REMOTE_SERVICE_NAME} 2>/dev/null || true" 2>&1)"; then
    if [[ "${status_output}" == "active" ]]; then
      log "remote service already active: ${status_output}"
      exit 0
    fi
  fi

  if output="$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$VM_HOST" "mkdir -p ${REMOTE_ROOT}/scripts/ops ${REMOTE_ROOT}/logs" 2>&1)"; then
    log "remote directories ready: ${output:-ok}"
  else
    log "remote directory setup failed: ${output}"
    goto_retry=1
  fi

  if [[ "${goto_retry:-0}" -eq 0 ]]; then
    if deploy_output="$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$VM_HOST" "cat > ${REMOTE_RUNNER} && chmod +x ${REMOTE_RUNNER}" < "$LOCAL_RUNNER" 2>&1)"; then
      log "remote runner deployed: ${deploy_output:-ok}"
    else
      log "remote runner deploy failed: ${deploy_output}"
      goto_retry=1
    fi
  fi

  if [[ "${goto_retry:-0}" -eq 0 ]]; then
    if unit_output="$(service_unit | ssh -o BatchMode=yes -o ConnectTimeout=8 "$VM_HOST" "cat > /tmp/${REMOTE_SERVICE_NAME}" 2>&1)"; then
      log "remote service unit staged: ${unit_output:-ok}"
    else
      log "remote service unit stage failed: ${unit_output}"
      goto_retry=1
    fi
  fi

  if [[ "${goto_retry:-0}" -eq 0 ]]; then
    if install_output="$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$VM_HOST" "sudo -n install -o root -g root -m 644 /tmp/${REMOTE_SERVICE_NAME} ${REMOTE_SERVICE_PATH} && sudo -n systemctl daemon-reload && sudo -n systemctl enable --now ${REMOTE_SERVICE_NAME} && sudo -n systemctl is-active ${REMOTE_SERVICE_NAME}" 2>&1)"; then
      log "remote service installed: ${install_output}"
      exit 0
    else
      log "remote service install/start failed: ${install_output}"
    fi
  fi

  unset goto_retry

  if vm_status="$(az vm get-instance-view -g "$RESOURCE_GROUP" -n "$VM_NAME" --query "instanceView.statuses[].displayStatus" -o tsv 2>&1)"; then
    log "vm status: ${vm_status//$'\n'/; }"
  else
    log "vm status lookup failed: ${vm_status}"
  fi

  log "retrying in ${SLEEP_SECONDS}s"
  sleep "$SLEEP_SECONDS"
done
