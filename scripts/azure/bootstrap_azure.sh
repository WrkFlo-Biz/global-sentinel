#!/usr/bin/env bash
# Global Sentinel V4 — Azure Bootstrap
# Creates resource group, Key Vault, and networking foundation.
# Run: bash scripts/azure/bootstrap_azure.sh
set -euo pipefail

# === Configuration (override via env or edit here) ===
RG="${AZURE_RESOURCE_GROUP:-global-sentinel-rg}"
LOCATION="${AZURE_LOCATION:-eastus2}"
KV_NAME="${AZURE_KEY_VAULT_NAME:-gs-keyvault}"
VNET_NAME="gs-vnet"
SUBNET_NAME="gs-subnet"
NSG_NAME="gs-nsg"

echo "=== Global Sentinel V4 — Azure Bootstrap ==="
echo "Resource Group: $RG"
echo "Location: $LOCATION"
echo "Key Vault: $KV_NAME"

# --- Resource Group ---
echo "[1/5] Creating resource group..."
az group create --name "$RG" --location "$LOCATION" --output table

# --- Key Vault ---
echo "[2/5] Creating Key Vault..."
az keyvault create \
  --name "$KV_NAME" \
  --resource-group "$RG" \
  --location "$LOCATION" \
  --enable-soft-delete true \
  --retention-days 90 \
  --output table

# --- Virtual Network + Subnet ---
echo "[3/5] Creating VNet and subnet..."
az network vnet create \
  --resource-group "$RG" \
  --name "$VNET_NAME" \
  --address-prefix 10.0.0.0/16 \
  --subnet-name "$SUBNET_NAME" \
  --subnet-prefix 10.0.1.0/24 \
  --output table

# --- Network Security Group ---
echo "[4/5] Creating NSG with hardened rules..."
az network nsg create \
  --resource-group "$RG" \
  --name "$NSG_NAME" \
  --output table

# Allow SSH from specific IP (replace YOUR_IP)
az network nsg rule create \
  --resource-group "$RG" \
  --nsg-name "$NSG_NAME" \
  --name AllowSSH \
  --priority 100 \
  --direction Inbound \
  --access Allow \
  --protocol Tcp \
  --source-address-prefixes "YOUR_IP/32" \
  --destination-port-ranges 22 \
  --output table

# Deny all other inbound
az network nsg rule create \
  --resource-group "$RG" \
  --nsg-name "$NSG_NAME" \
  --name DenyAllInbound \
  --priority 4096 \
  --direction Inbound \
  --access Deny \
  --protocol '*' \
  --source-address-prefixes '*' \
  --destination-port-ranges '*' \
  --output table

# Associate NSG with subnet
az network vnet subnet update \
  --resource-group "$RG" \
  --vnet-name "$VNET_NAME" \
  --name "$SUBNET_NAME" \
  --network-security-group "$NSG_NAME" \
  --output table

echo "[5/5] Bootstrap complete."
echo ""
echo "Next steps:"
echo "  1. Run: bash scripts/azure/provision_global_sentinel.sh"
echo "  2. SSH into VM and run: bash scripts/azure/hardening_postinstall.sh"
echo "  3. Clone repo, install services, start monitoring"
