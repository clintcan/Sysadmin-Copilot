#!/usr/bin/env bash
# sysadmin-copilot-configure.sh — Reconfigure Sysadmin Copilot
# Updates the LLM provider, API key, and sudoers after an initial install.
# Must be run as root or with sudo. Requires install.sh to have been run first.

set -euo pipefail

# ─── Colors ───────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

# ─── Config ───────────────────────────────────────────────────────────────────

SERVICE_USER="sysadmin-copilot"
INSTALL_DIR="/opt/sysadmin-copilot"
SUDOERS_FILE="/etc/sudoers.d/sysadmin-copilot"

# ─── Helpers ──────────────────────────────────────────────────────────────────

info()    { echo -e "${CYAN}  →${RESET} $*"; }
success() { echo -e "${GREEN}  ✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}  !${RESET} $*"; }
die()     { echo -e "${RED}  ✗ ERROR:${RESET} $*" >&2; exit 1; }
header()  { echo -e "\n${BOLD}$*${RESET}"; }

require_root() {
    [[ $EUID -eq 0 ]] || die "This script must be run as root (sudo $0)"
}

require_cmd() {
    command -v "$1" &>/dev/null || die "'$1' is required but not installed."
}

# ─── Preflight ────────────────────────────────────────────────────────────────

require_root

header "=== Sysadmin Copilot Configure ==="
echo ""
echo "This will:"
echo "  • Update the LLM provider and API key (.env)"
echo "  • Install the provider's Python package"
echo "  • Regenerate the sudoers file"
echo ""

# Verify install.sh was run first
id "$SERVICE_USER" &>/dev/null || die "Service user '${SERVICE_USER}' not found. Run install.sh first."
[[ -d "$INSTALL_DIR" ]] || die "Install directory '${INSTALL_DIR}' not found. Run install.sh first."
[[ -d "${INSTALL_DIR}/.venv" ]] || die "Python venv not found at ${INSTALL_DIR}/.venv. Run install.sh first."

read -rp "$(echo -e "${YELLOW}Continue? [y/N]: ${RESET}")" confirm
[[ "${confirm,,}" == "y" || "${confirm,,}" == "yes" ]] || { echo "Aborted."; exit 0; }

require_cmd python3

# ─── Read existing .env ──────────────────────────────────────────────────────

ENV_FILE="${INSTALL_DIR}/.env"
EXISTING_PROVIDER=""
EXISTING_OPENAI_KEY=""
EXISTING_ANTHROPIC_KEY=""

if [[ -f "$ENV_FILE" ]]; then
    info "Found existing config: ${ENV_FILE}"
    while IFS='=' read -r key val; do
        case "$key" in
            LLM_PROVIDER)       EXISTING_PROVIDER="$val" ;;
            OPENAI_API_KEY)     EXISTING_OPENAI_KEY="$val" ;;
            ANTHROPIC_API_KEY)  EXISTING_ANTHROPIC_KEY="$val" ;;
        esac
    done < "$ENV_FILE"
fi

# Map existing provider to a default choice number
default_choice="1"
case "$EXISTING_PROVIDER" in
    ollama)    default_choice="1" ;;
    openai)    default_choice="2" ;;
    anthropic) default_choice="3" ;;
esac

# Helper: mask an API key for display (show first 8 + last 4 chars)
mask_key() {
    local key="$1"
    if [[ ${#key} -gt 12 ]]; then
        echo "${key:0:8}...${key: -4}"
    elif [[ -n "$key" ]]; then
        echo "${key:0:4}..."
    else
        echo ""
    fi
}

# ─── Step 1: LLM Provider ─────────────────────────────────────────────────────

header "Step 1: Choose your LLM provider"
echo ""
echo "  1) Ollama  — local/self-hosted (no API key needed)"
echo "  2) OpenAI  — GPT-4o-mini (requires OPENAI_API_KEY)"
echo "  3) Anthropic — Claude (requires ANTHROPIC_API_KEY)"
echo ""
if [[ -n "$EXISTING_PROVIDER" ]]; then
    info "Current provider: ${EXISTING_PROVIDER}"
fi
read -rp "$(echo -e "${YELLOW}Provider [1/2/3, default: ${default_choice}]: ${RESET}")" provider_choice
provider_choice="${provider_choice:-$default_choice}"

case "$provider_choice" in
    1)
        LLM_PROVIDER="ollama"
        PROVIDER_PKG="langchain-ollama>=0.2"
        ;;
    2)
        LLM_PROVIDER="openai"
        PROVIDER_PKG="langchain-openai>=0.2"
        masked="$(mask_key "$EXISTING_OPENAI_KEY")"
        if [[ -n "$masked" ]]; then
            read -rp "$(echo -e "${YELLOW}  OpenAI API key [Enter to keep ${masked}]: ${RESET}")" new_key
            EXISTING_OPENAI_KEY="${new_key:-$EXISTING_OPENAI_KEY}"
        else
            read -rp "$(echo -e "${YELLOW}  Enter your OpenAI API key: ${RESET}")" EXISTING_OPENAI_KEY
            [[ -n "$EXISTING_OPENAI_KEY" ]] || die "API key cannot be empty."
        fi
        ;;
    3)
        LLM_PROVIDER="anthropic"
        PROVIDER_PKG="langchain-anthropic>=0.3"
        masked="$(mask_key "$EXISTING_ANTHROPIC_KEY")"
        if [[ -n "$masked" ]]; then
            read -rsp "$(echo -e "${YELLOW}  Anthropic API key [Enter to keep ${masked}]: ${RESET}")" new_key
            echo ""
            EXISTING_ANTHROPIC_KEY="${new_key:-$EXISTING_ANTHROPIC_KEY}"
        else
            read -rsp "$(echo -e "${YELLOW}  Enter your Anthropic API key: ${RESET}")" EXISTING_ANTHROPIC_KEY
            echo ""
            [[ -n "$EXISTING_ANTHROPIC_KEY" ]] || die "API key cannot be empty."
        fi
        ;;
    *)
        die "Invalid choice: $provider_choice"
        ;;
esac

success "Provider: ${LLM_PROVIDER}"

# ─── Step 2: Install provider package ─────────────────────────────────────────

header "Step 2: Install provider package"

info "Installing ${PROVIDER_PKG}..."
runuser -u "$SERVICE_USER" -- "${INSTALL_DIR}/.venv/bin/pip" install --quiet "${PROVIDER_PKG}"
success "Provider package installed."

# ─── Step 3: Write .env file ─────────────────────────────────────────────────

header "Step 3: Environment configuration"

{
    echo "LLM_PROVIDER=${LLM_PROVIDER}"
    # Preserve all API keys so switching providers doesn't lose them
    if [[ -n "$EXISTING_OPENAI_KEY" ]]; then
        echo "OPENAI_API_KEY=${EXISTING_OPENAI_KEY}"
    fi
    if [[ -n "$EXISTING_ANTHROPIC_KEY" ]]; then
        echo "ANTHROPIC_API_KEY=${EXISTING_ANTHROPIC_KEY}"
    fi
} > "$ENV_FILE"

chown "${SERVICE_USER}:${SERVICE_USER}" "$ENV_FILE"
chmod 600 "$ENV_FILE"
success "Wrote ${ENV_FILE} (mode 600)."

# ─── Step 4: Sudoers ─────────────────────────────────────────────────────────

header "Step 4: Sudoers configuration"

# Delegate to sync-sudoers.sh which reads ALLOWED_SERVICES from safety.py
SYNC_SCRIPT="${INSTALL_DIR}/sync-sudoers.sh"
if [[ -x "$SYNC_SCRIPT" ]] || [[ -f "$SYNC_SCRIPT" ]]; then
    bash "$SYNC_SCRIPT"
else
    warn "sync-sudoers.sh not found at ${SYNC_SCRIPT} — skipping sudoers update."
    warn "Run 'sudo bash sync-sudoers.sh' manually to update sudoers."
fi

# ─── Done ─────────────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}${BOLD}Configuration complete!${RESET}"
echo ""
echo -e "  Run the copilot with:  ${BOLD}sysadmin-copilot${RESET}"
echo -e "  Audit logs:            ${BOLD}/home/${SERVICE_USER}/.sysadmin-copilot/logs/${RESET}"
echo -e "  Config:                ${BOLD}${INSTALL_DIR}/.env${RESET}"
echo -e "  Sudoers:               ${BOLD}${SUDOERS_FILE}${RESET}"
echo ""
if [[ "$LLM_PROVIDER" == "ollama" ]]; then
    echo -e "${YELLOW}  Reminder:${RESET} Make sure Ollama is running and the model is pulled:"
    echo -e "    ollama serve"
    echo -e "    ollama pull llama3.1:8b"
    echo ""
fi
