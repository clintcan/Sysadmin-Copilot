#!/usr/bin/env bash
# install.sh — Sysadmin Copilot installer
# Sets up a dedicated service account, installs the app, and configures sudoers.
# Must be run as root or with sudo.

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
WRAPPER="/usr/local/bin/sysadmin-copilot"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Services allowed to be restarted/stopped — must match safety.py ALLOWED_SERVICES
ALLOWED_SERVICES=(
    nginx apache2 httpd
    postgresql mysql mariadb
    docker redis memcached
    php-fpm php8.1-fpm gunicorn uwsgi
    cron postfix
)

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

header "=== Sysadmin Copilot Installer ==="
echo ""
echo "This will:"
echo "  • Create the '${SERVICE_USER}' system account"
echo "  • Install the app to ${INSTALL_DIR}"
echo "  • Configure sudoers for service management"
echo "  • Create a wrapper command at ${WRAPPER}"
echo ""
read -rp "$(echo -e "${YELLOW}Continue? [y/N]: ${RESET}")" confirm
[[ "${confirm,,}" == "y" || "${confirm,,}" == "yes" ]] || { echo "Aborted."; exit 0; }

require_cmd python3
require_cmd visudo

# Detect systemctl path (varies by distro)
SYSTEMCTL_PATH="$(command -v systemctl)" || die "systemctl not found — is this a systemd system?"

# ─── Step 1: LLM Provider ─────────────────────────────────────────────────────

header "Step 1: Choose your LLM provider"
echo ""
echo "  1) Ollama  — local/self-hosted (no API key needed)"
echo "  2) OpenAI  — GPT-4o-mini (requires OPENAI_API_KEY)"
echo "  3) Anthropic — Claude (requires ANTHROPIC_API_KEY)"
echo ""
read -rp "$(echo -e "${YELLOW}Provider [1/2/3, default: 1]: ${RESET}")" provider_choice
provider_choice="${provider_choice:-1}"

case "$provider_choice" in
    1)
        LLM_PROVIDER="ollama"
        PROVIDER_PKG="langchain-ollama>=0.2"
        API_KEY_VAR=""
        API_KEY_VAL=""
        ;;
    2)
        LLM_PROVIDER="openai"
        PROVIDER_PKG="langchain-openai>=0.2"
        API_KEY_VAR="OPENAI_API_KEY"
        read -rp "$(echo -e "${YELLOW}  Enter your OpenAI API key: ${RESET}")" API_KEY_VAL
        [[ -n "$API_KEY_VAL" ]] || die "API key cannot be empty."
        ;;
    3)
        LLM_PROVIDER="anthropic"
        PROVIDER_PKG="langchain-anthropic>=0.3"
        API_KEY_VAR="ANTHROPIC_API_KEY"
        read -rsp "$(echo -e "${YELLOW}  Enter your Anthropic API key: ${RESET}")" API_KEY_VAL
        echo ""
        [[ -n "$API_KEY_VAL" ]] || die "API key cannot be empty."
        ;;
    *)
        die "Invalid choice: $provider_choice"
        ;;
esac

success "Provider: ${LLM_PROVIDER}"

# ─── Step 2: Create service account ──────────────────────────────────────────

header "Step 2: Service account"

if id "$SERVICE_USER" &>/dev/null; then
    warn "User '${SERVICE_USER}' already exists — skipping creation."
else
    useradd --system --create-home --shell /bin/bash "$SERVICE_USER"
    success "Created user '${SERVICE_USER}'."
fi

# Group membership for log access
for group in systemd-journal adm; do
    if getent group "$group" &>/dev/null; then
        usermod -aG "$group" "$SERVICE_USER"
        success "Added '${SERVICE_USER}' to group '${group}'."
    else
        warn "Group '${group}' not found — skipping (may limit log access)."
    fi
done

# ─── Step 3: Install application ─────────────────────────────────────────────

header "Step 3: Install application"

if [[ -d "$INSTALL_DIR" ]]; then
    warn "${INSTALL_DIR} already exists — files will be overwritten."
else
    mkdir -p "$INSTALL_DIR"
fi

cp -r "${SCRIPT_DIR}/." "$INSTALL_DIR/"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "$INSTALL_DIR"
success "Copied app to ${INSTALL_DIR}."

info "Creating Python virtual environment..."
runuser -u "$SERVICE_USER" -- python3 -m venv "${INSTALL_DIR}/.venv"

info "Installing base dependencies..."
runuser -u "$SERVICE_USER" -- "${INSTALL_DIR}/.venv/bin/pip" install --quiet \
    "langchain-core>=0.3" \
    "langgraph>=0.2"

info "Installing provider package: ${PROVIDER_PKG}..."
runuser -u "$SERVICE_USER" -- "${INSTALL_DIR}/.venv/bin/pip" install --quiet "${PROVIDER_PKG}"

success "Dependencies installed."

# ─── Step 4: Write .env file ─────────────────────────────────────────────────

header "Step 4: Environment configuration"

ENV_FILE="${INSTALL_DIR}/.env"

{
    echo "LLM_PROVIDER=${LLM_PROVIDER}"
    if [[ -n "$API_KEY_VAR" && -n "$API_KEY_VAL" ]]; then
        echo "${API_KEY_VAR}=${API_KEY_VAL}"
    fi
} > "$ENV_FILE"

chown "${SERVICE_USER}:${SERVICE_USER}" "$ENV_FILE"
chmod 600 "$ENV_FILE"
success "Wrote ${ENV_FILE} (mode 600)."

# ─── Step 5: Sudoers ─────────────────────────────────────────────────────────

header "Step 5: Sudoers configuration"

# Build Cmnd_Alias entries for each allowed service
build_alias() {
    local alias_name="$1"
    local action="$2"
    local entries=()
    for svc in "${ALLOWED_SERVICES[@]}"; do
        entries+=("    ${SYSTEMCTL_PATH} ${action} ${svc}")
    done
    echo "Cmnd_Alias ${alias_name} = \\"
    # Join with comma-backslash, no trailing backslash on last line
    local count="${#entries[@]}"
    local i=0
    for entry in "${entries[@]}"; do
        i=$((i + 1))
        if [[ $i -lt $count ]]; then
            echo "${entry}, \\"
        else
            echo "${entry}"
        fi
    done
}

cat > "$SUDOERS_FILE" <<SUDOERS
# Sysadmin Copilot — generated by install.sh
# Mirrors ALLOWED_SERVICES in safety.py. Keep both in sync.
# systemctl path: ${SYSTEMCTL_PATH}

$(build_alias COPILOT_RESTART restart)

$(build_alias COPILOT_STOP stop)

Cmnd_Alias COPILOT_UPDATE = \\
    /usr/bin/apt-get update, \\
    /usr/bin/apt-get upgrade -y, \\
    /usr/bin/dnf upgrade -y, \\
    /usr/bin/yum update -y, \\
    /usr/bin/snap refresh, \\
    /usr/bin/flatpak update -y

${SERVICE_USER} ALL=(ALL) NOPASSWD: COPILOT_RESTART, COPILOT_STOP, COPILOT_UPDATE
SUDOERS

chmod 440 "$SUDOERS_FILE"

# Validate the file before leaving it in place
if visudo -c -f "$SUDOERS_FILE" &>/dev/null; then
    success "Sudoers file written and validated: ${SUDOERS_FILE}"
else
    rm -f "$SUDOERS_FILE"
    die "Sudoers syntax check failed — file removed. Check ${SYSTEMCTL_PATH} path."
fi

# ─── Step 6: Wrapper command ─────────────────────────────────────────────────

header "Step 6: Wrapper command"

cat > "$WRAPPER" <<WRAPPER
#!/usr/bin/env bash
# Run Sysadmin Copilot as the ${SERVICE_USER} service account.
exec sudo -u ${SERVICE_USER} bash -c '
    set -a
    source ${INSTALL_DIR}/.env
    set +a
    exec ${INSTALL_DIR}/.venv/bin/python ${INSTALL_DIR}/agent.py
'
WRAPPER

chmod 755 "$WRAPPER"
success "Created wrapper: ${WRAPPER}"

# ─── Done ─────────────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}${BOLD}Installation complete!${RESET}"
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
