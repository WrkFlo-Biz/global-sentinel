#!/usr/bin/env bash
set -euo pipefail

# Global Sentinel V4 — GitHub Enterprise self-hosted runner bootstrap (Linux)
#
# Run on the Azure VM to install a self-hosted Actions runner for CI/CD.
#
# Prereqs:
#   gh installed, logged into GHES
#
# Usage:
#   GH_HOST=ghe.example.com \
#   GHE_ORG=my-org \
#   REPO_NAME=global-sentinel \
#   RUNNER_TOKEN=<registration-token> \
#   sudo bash scripts/github/bootstrap_runner.sh

GH_HOST="${GH_HOST:-}"
GHE_ORG="${GHE_ORG:-}"
REPO_NAME="${REPO_NAME:-global-sentinel}"
RUNNER_TOKEN="${RUNNER_TOKEN:-}"
RUNNER_NAME="${RUNNER_NAME:-$(hostname)-gs-runner}"
RUNNER_LABELS="${RUNNER_LABELS:-global-sentinel,shadow,azure}"
RUNNER_VERSION="${RUNNER_VERSION:-2.319.1}"
RUNNER_USER="${RUNNER_USER:-gsadmin}"
RUNNER_DIR="${RUNNER_DIR:-/opt/actions-runner}"

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (sudo)."
  exit 1
fi

if [[ -z "$GH_HOST" || -z "$GHE_ORG" || -z "$REPO_NAME" || -z "$RUNNER_TOKEN" ]]; then
  echo "ERROR: GH_HOST, GHE_ORG, REPO_NAME, and RUNNER_TOKEN are required."
  exit 1
fi

id -u "$RUNNER_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash "$RUNNER_USER"

mkdir -p "$RUNNER_DIR"
chown -R "$RUNNER_USER:$RUNNER_USER" "$RUNNER_DIR"

cd "$RUNNER_DIR"

if [[ ! -f "config.sh" ]]; then
  echo "==> Downloading GitHub Actions runner v${RUNNER_VERSION}"
  ARCHIVE="actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
  URL="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${ARCHIVE}"
  sudo -u "$RUNNER_USER" bash -c "curl -L -o ${ARCHIVE} ${URL}"
  sudo -u "$RUNNER_USER" bash -c "tar xzf ${ARCHIVE}"
fi

RUNNER_URL="https://${GH_HOST}/${GHE_ORG}/${REPO_NAME}"

if [[ -f ".runner" ]]; then
  echo "Runner already configured in $RUNNER_DIR"
else
  echo "==> Configuring runner for ${RUNNER_URL}"
  sudo -u "$RUNNER_USER" bash -c "./config.sh --url '${RUNNER_URL}' --token '${RUNNER_TOKEN}' --name '${RUNNER_NAME}' --labels '${RUNNER_LABELS}' --unattended --replace"
fi

echo "==> Installing runner service"
./svc.sh install "$RUNNER_USER"
./svc.sh start

echo "==> Runner status"
./svc.sh status || true
echo "Done. Runner registered at ${RUNNER_URL}"
