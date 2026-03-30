#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BLUEPRINT_DIR="${REPO_ROOT}/config/nemoclaw"
POLICY_TEMPLATE="${BLUEPRINT_DIR}/policies/gs-openclaw-wrapper.template.yaml"
OPENCLAW_USER="${OPENCLAW_USER:-openclaw}"
GATEWAY_NAME="${GATEWAY_NAME:-gs-nemoclaw}"
GATEWAY_PORT="${GATEWAY_PORT:-18080}"
PROFILE="${PROFILE:-gs-azure-mini}"
SANDBOX_NAME="${SANDBOX_NAME:-gs-wrapper}"

OPENCLAW_HOME="$(getent passwd "${OPENCLAW_USER}" | cut -d: -f6)"
LOCAL_BIN="${OPENCLAW_HOME}/.local/bin"
SANDBOX_IMAGE="ghcr.io/nvidia/openshell-community/sandboxes/openclaw:latest"
RENDERED_POLICY="$(mktemp /tmp/gs-openclaw-wrapper-policy.XXXXXX.yaml)"

cleanup() {
  rm -f "${RENDERED_POLICY}"
}
trap cleanup EXIT

need_file() {
  if [[ ! -f "$1" ]]; then
    echo "Missing required file: $1" >&2
    exit 1
  fi
}

need_file "${POLICY_TEMPLATE}"
need_file "${BLUEPRINT_DIR}/blueprint.yaml"

if ! sudo -n true 2>/dev/null; then
  echo "This script needs passwordless sudo to read /etc/openclaw/openclaw.env." >&2
  exit 1
fi

eval "$(
  sudo -n python3 - <<'PY'
from pathlib import Path
from shlex import quote

keys = {
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_DEPLOYMENT",
}
values = {}
for line in Path("/etc/openclaw/openclaw.env").read_text().splitlines():
    if not line.strip() or line.strip().startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    if key in keys:
        values[key] = value.strip()

for key in sorted(keys):
    print(f"export {key}={quote(values.get(key, ''))}")
PY
)"

if [[ -z "${AZURE_OPENAI_API_KEY:-}" || -z "${AZURE_OPENAI_ENDPOINT:-}" ]]; then
  echo "Azure OpenAI credentials are missing in /etc/openclaw/openclaw.env." >&2
  exit 1
fi

AZURE_OPENAI_HOST="$(python3 - <<'PY'
from urllib.parse import urlparse
import os
print(urlparse(os.environ["AZURE_OPENAI_ENDPOINT"]).hostname or "")
PY
)"

AZURE_OPENAI_BASE="${AZURE_OPENAI_ENDPOINT%/}/openai/v1"

sed "s/__AZURE_OPENAI_HOST__/${AZURE_OPENAI_HOST}/g" "${POLICY_TEMPLATE}" > "${RENDERED_POLICY}"

run_as_openclaw() {
  sudo -u "${OPENCLAW_USER}" -H -E env PATH="${LOCAL_BIN}:$PATH" bash -lc "$*"
}

export AZURE_OPENAI_API_KEY
export OPENAI_API_KEY="${AZURE_OPENAI_API_KEY}"

run_as_openclaw "openshell gateway start --name '${GATEWAY_NAME}' --port '${GATEWAY_PORT}' >/dev/null"

run_as_openclaw "openshell gateway select '${GATEWAY_NAME}' >/dev/null"

if ! run_as_openclaw "openshell sandbox list | awk 'NR > 1 {print \$1}' | grep -qx '${SANDBOX_NAME}'"; then
  run_as_openclaw "openshell sandbox create --from '${SANDBOX_IMAGE}' --name '${SANDBOX_NAME}' --forward '127.0.0.1:18790' --policy '${RENDERED_POLICY}' >/dev/null"
fi

if ! run_as_openclaw "openshell provider list | awk 'NR > 1 {print \$1}' | grep -qx 'gs-azure-openai'"; then
  run_as_openclaw "openshell provider create --name 'gs-azure-openai' --type openai --credential OPENAI_API_KEY --config OPENAI_BASE_URL='${AZURE_OPENAI_BASE}' >/dev/null"
fi

run_as_openclaw "openshell inference set --provider 'gs-azure-openai' --model '${AZURE_OPENAI_DEPLOYMENT:-gpt-5-mini}' --no-verify >/dev/null"

run_as_openclaw "openshell policy set --policy '${RENDERED_POLICY}' --wait '${SANDBOX_NAME}' >/dev/null"

sudo -u "${OPENCLAW_USER}" -H env \
  SANDBOX_NAME="${SANDBOX_NAME}" \
  PROVIDER_NAME="gs-azure-openai" \
  MODEL_NAME="${AZURE_OPENAI_DEPLOYMENT:-gpt-5-mini}" \
  python3 - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

registry_path = Path.home() / ".nemoclaw" / "sandboxes.json"
registry_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

if registry_path.exists():
    data = json.loads(registry_path.read_text())
else:
    data = {"sandboxes": {}, "defaultSandbox": None}

name = os.environ["SANDBOX_NAME"]
existing = data["sandboxes"].get(name, {})
data["sandboxes"][name] = {
    "name": name,
    "createdAt": existing.get("createdAt") or datetime.now(timezone.utc).isoformat(),
    "model": os.environ["MODEL_NAME"],
    "nimContainer": None,
    "provider": os.environ["PROVIDER_NAME"],
    "gpuEnabled": False,
    "policies": ["gs-openclaw-wrapper"],
}
if not data.get("defaultSandbox"):
    data["defaultSandbox"] = name

registry_path.write_text(json.dumps(data, indent=2))
os.chmod(registry_path, 0o600)
PY

echo "Staged NemoClaw wrapper ready."
echo "  gateway : ${GATEWAY_NAME} on ${GATEWAY_PORT}"
echo "  sandbox : ${SANDBOX_NAME}"
echo "  control : http://127.0.0.1:18790"
echo "  profile : ${PROFILE}"
echo
run_as_openclaw "openshell gateway info"
echo
run_as_openclaw "openshell sandbox list"
