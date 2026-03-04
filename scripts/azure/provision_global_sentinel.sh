#!/usr/bin/env bash
set -euo pipefail

# Global Sentinel V4 — Azure Provisioning (Shadow Mode Infrastructure)
# Idempotent bootstrap for a control-plane VM + optional worker VM.
#
# Prereqs:
#   az login
#   az account set --subscription "<SUBSCRIPTION_ID>"
#
# Usage:
#   bash scripts/azure/provision_global_sentinel.sh

LOCATION="${LOCATION:-eastus2}"
RESOURCE_GROUP="${RESOURCE_GROUP:-${AZURE_RESOURCE_GROUP:-global-sentinel-rg}}"
VM_NAME="${VM_NAME:-${AZURE_VM_NAME:-gs-control-01}}"
WORKER_VM_NAME="${WORKER_VM_NAME:-gs-worker-01}"
ADMIN_USERNAME="${ADMIN_USERNAME:-gsadmin}"
SSH_PUBLIC_KEY_PATH="${SSH_PUBLIC_KEY_PATH:-$HOME/.ssh/id_rsa.pub}"
VM_SIZE="${VM_SIZE:-Standard_D4s_v5}"
WORKER_VM_SIZE="${WORKER_VM_SIZE:-Standard_D4s_v5}"
DISK_GB="${DISK_GB:-128}"
CREATE_WORKER_VM="${CREATE_WORKER_VM:-false}"
TAGS="${TAGS:-project=global-sentinel env=prod mode=shadow owner=moses}"
IMAGE="${IMAGE:-Ubuntu2204}"
VNET_NAME="${VNET_NAME:-vnet-global-sentinel}"
SUBNET_NAME="${SUBNET_NAME:-subnet-control}"
NSG_NAME="${NSG_NAME:-nsg-global-sentinel}"
PUBLIC_IP_NAME="${PUBLIC_IP_NAME:-pip-global-sentinel}"
NIC_NAME="${NIC_NAME:-nic-global-sentinel}"
WORKER_SUBNET_NAME="${WORKER_SUBNET_NAME:-subnet-workers}"
WORKER_NIC_NAME="${WORKER_NIC_NAME:-nic-global-sentinel-worker}"
WORKER_PUBLIC_IP_NAME="${WORKER_PUBLIC_IP_NAME:-pip-global-sentinel-worker}"
ENABLE_PUBLIC_WORKER_IP="${ENABLE_PUBLIC_WORKER_IP:-false}"
KV_NAME="${AZURE_KEY_VAULT_NAME:-gs-keyvault}"

if [[ ! -f "$SSH_PUBLIC_KEY_PATH" ]]; then
  echo "ERROR: SSH public key not found at $SSH_PUBLIC_KEY_PATH"
  exit 1
fi

echo "==> Checking Azure CLI login/session"
az account show >/dev/null

SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
echo "Using subscription: $SUBSCRIPTION_ID"

echo "==> Creating resource group: $RESOURCE_GROUP ($LOCATION)"
az group create \
  --name "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --tags $TAGS >/dev/null

echo "==> Creating VNet and subnets"
if ! az network vnet show -g "$RESOURCE_GROUP" -n "$VNET_NAME" >/dev/null 2>&1; then
  az network vnet create \
    -g "$RESOURCE_GROUP" \
    -n "$VNET_NAME" \
    --address-prefixes 10.42.0.0/16 \
    --subnet-name "$SUBNET_NAME" \
    --subnet-prefixes 10.42.1.0/24 \
    --tags $TAGS >/dev/null
else
  echo "VNet already exists: $VNET_NAME"
fi

if ! az network vnet subnet show -g "$RESOURCE_GROUP" --vnet-name "$VNET_NAME" -n "$WORKER_SUBNET_NAME" >/dev/null 2>&1; then
  az network vnet subnet create \
    -g "$RESOURCE_GROUP" \
    --vnet-name "$VNET_NAME" \
    -n "$WORKER_SUBNET_NAME" \
    --address-prefixes 10.42.2.0/24 >/dev/null
fi

echo "==> Creating NSG and rules"
if ! az network nsg show -g "$RESOURCE_GROUP" -n "$NSG_NAME" >/dev/null 2>&1; then
  az network nsg create -g "$RESOURCE_GROUP" -n "$NSG_NAME" --tags $TAGS >/dev/null
fi

# SSH rule (restrict source to your IP ASAP)
if ! az network nsg rule show -g "$RESOURCE_GROUP" --nsg-name "$NSG_NAME" -n Allow-SSH >/dev/null 2>&1; then
  az network nsg rule create \
    -g "$RESOURCE_GROUP" \
    --nsg-name "$NSG_NAME" \
    -n Allow-SSH \
    --priority 100 \
    --access Allow \
    --direction Inbound \
    --protocol Tcp \
    --destination-port-ranges 22 \
    --source-address-prefixes Internet >/dev/null
fi

echo "==> Associating NSG to subnets"
az network vnet subnet update \
  -g "$RESOURCE_GROUP" \
  --vnet-name "$VNET_NAME" \
  -n "$SUBNET_NAME" \
  --network-security-group "$NSG_NAME" >/dev/null

az network vnet subnet update \
  -g "$RESOURCE_GROUP" \
  --vnet-name "$VNET_NAME" \
  -n "$WORKER_SUBNET_NAME" \
  --network-security-group "$NSG_NAME" >/dev/null

echo "==> Creating control-plane public IP and NIC"
if ! az network public-ip show -g "$RESOURCE_GROUP" -n "$PUBLIC_IP_NAME" >/dev/null 2>&1; then
  az network public-ip create \
    -g "$RESOURCE_GROUP" \
    -n "$PUBLIC_IP_NAME" \
    --sku Standard \
    --allocation-method Static \
    --version IPv4 \
    --tags $TAGS >/dev/null
fi

if ! az network nic show -g "$RESOURCE_GROUP" -n "$NIC_NAME" >/dev/null 2>&1; then
  az network nic create \
    -g "$RESOURCE_GROUP" \
    -n "$NIC_NAME" \
    --vnet-name "$VNET_NAME" \
    --subnet "$SUBNET_NAME" \
    --network-security-group "$NSG_NAME" \
    --public-ip-address "$PUBLIC_IP_NAME" \
    --tags $TAGS >/dev/null
fi

echo "==> Creating control-plane VM: $VM_NAME"
if ! az vm show -g "$RESOURCE_GROUP" -n "$VM_NAME" >/dev/null 2>&1; then
  az vm create \
    -g "$RESOURCE_GROUP" \
    -n "$VM_NAME" \
    --image "$IMAGE" \
    --size "$VM_SIZE" \
    --admin-username "$ADMIN_USERNAME" \
    --ssh-key-values "$SSH_PUBLIC_KEY_PATH" \
    --nics "$NIC_NAME" \
    --os-disk-size-gb "$DISK_GB" \
    --storage-sku Premium_LRS \
    --enable-agent true \
    --enable-auto-update true \
    --assign-identity \
    --tags $TAGS >/dev/null
else
  echo "VM already exists: $VM_NAME"
fi

echo "==> Enabling boot diagnostics"
az vm boot-diagnostics enable \
  -g "$RESOURCE_GROUP" \
  -n "$VM_NAME" >/dev/null || true

echo "==> Granting Key Vault access to VM managed identity"
CONTROL_MI_PRINCIPAL_ID="$(az vm show -g "$RESOURCE_GROUP" -n "$VM_NAME" --query identity.principalId -o tsv)"
az keyvault set-policy \
  --name "$KV_NAME" \
  --object-id "$CONTROL_MI_PRINCIPAL_ID" \
  --secret-permissions get list \
  >/dev/null 2>&1 || echo "Key Vault policy set skipped (vault may not exist yet)"
echo "Control VM Managed Identity principalId: $CONTROL_MI_PRINCIPAL_ID"

echo "==> (Optional) Creating worker VM"
if [[ "$CREATE_WORKER_VM" == "true" ]]; then
  if [[ "$ENABLE_PUBLIC_WORKER_IP" == "true" ]]; then
    if ! az network public-ip show -g "$RESOURCE_GROUP" -n "$WORKER_PUBLIC_IP_NAME" >/dev/null 2>&1; then
      az network public-ip create \
        -g "$RESOURCE_GROUP" \
        -n "$WORKER_PUBLIC_IP_NAME" \
        --sku Standard \
        --allocation-method Static \
        --version IPv4 \
        --tags $TAGS >/dev/null
    fi
  fi

  if ! az network nic show -g "$RESOURCE_GROUP" -n "$WORKER_NIC_NAME" >/dev/null 2>&1; then
    NIC_ARGS=(
      -g "$RESOURCE_GROUP"
      -n "$WORKER_NIC_NAME"
      --vnet-name "$VNET_NAME"
      --subnet "$WORKER_SUBNET_NAME"
      --network-security-group "$NSG_NAME"
      --tags $TAGS
    )
    if [[ "$ENABLE_PUBLIC_WORKER_IP" == "true" ]]; then
      NIC_ARGS+=(--public-ip-address "$WORKER_PUBLIC_IP_NAME")
    fi
    az network nic create "${NIC_ARGS[@]}" >/dev/null
  fi

  if ! az vm show -g "$RESOURCE_GROUP" -n "$WORKER_VM_NAME" >/dev/null 2>&1; then
    az vm create \
      -g "$RESOURCE_GROUP" \
      -n "$WORKER_VM_NAME" \
      --image "$IMAGE" \
      --size "$WORKER_VM_SIZE" \
      --admin-username "$ADMIN_USERNAME" \
      --ssh-key-values "$SSH_PUBLIC_KEY_PATH" \
      --nics "$WORKER_NIC_NAME" \
      --os-disk-size-gb "$DISK_GB" \
      --storage-sku Premium_LRS \
      --enable-agent true \
      --enable-auto-update true \
      --assign-identity \
      --tags $TAGS >/dev/null
  fi
fi

echo "==> Enabling Azure Monitor agent extension"
az vm extension set \
  --publisher Microsoft.Azure.Monitor \
  --name AzureMonitorLinuxAgent \
  -g "$RESOURCE_GROUP" \
  --vm-name "$VM_NAME" >/dev/null || true

echo "==> Summary"
CONTROL_PUBLIC_IP="$(az network public-ip show -g "$RESOURCE_GROUP" -n "$PUBLIC_IP_NAME" --query ipAddress -o tsv)"
echo "Control-plane VM: $VM_NAME"
echo "Public IP: $CONTROL_PUBLIC_IP"
echo "SSH: ssh ${ADMIN_USERNAME}@${CONTROL_PUBLIC_IP}"
echo
echo "Next steps:"
echo "1) SSH in and run: sudo bash scripts/azure/hardening_postinstall.sh"
echo "2) Clone repo and install systemd services"
echo "3) Store secrets in Azure Key Vault and render .env from secure source"
echo "4) Tighten NSG SSH source IP to your current IP"
