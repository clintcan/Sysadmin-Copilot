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

header "Step 1: LLM provider configuration"

ENV_FILE="${INSTALL_DIR}/.env"
SKIP_ENV=false

# Check for existing .env and parse current values as defaults
EXISTING_PROVIDER=""
EXISTING_KEY_VAR=""
EXISTING_KEY_VAL=""

# Mask an API key for safe display: show first 4 and last 4 chars
mask_key() {
    local key="$1"
    local len=${#key}
    if [[ $len -le 12 ]]; then
        echo "${key:0:2}***${key: -2}"
    else
        echo "${key:0:8}...${key: -4}"
    fi
}

if [[ -f "$ENV_FILE" ]]; then
    echo ""
    warn "Existing .env found at ${ENV_FILE}"

    # Parse existing values
    EXISTING_PROVIDER="$(grep -oP '^LLM_PROVIDER=\K.*' "$ENV_FILE" 2>/dev/null || true)"
    if [[ -n "$EXISTING_PROVIDER" ]]; then
        # Detect which API key is set (mask the value for display)
        for key_name in OPENAI_API_KEY ANTHROPIC_API_KEY; do
            val="$(grep -oP "^${key_name}=\\K.*" "$ENV_FILE" 2>/dev/null || true)"
            if [[ -n "$val" ]]; then
                EXISTING_KEY_VAR="$key_name"
                EXISTING_KEY_VAL="$val"
                info "Current config: LLM_PROVIDER=${EXISTING_PROVIDER}, ${key_name}=$(mask_key "$val")"
                break
            fi
        done
        if [[ -z "$EXISTING_KEY_VAR" ]]; then
            info "Current config: LLM_PROVIDER=${EXISTING_PROVIDER} (no API key)"
        fi
    fi

    echo ""
    read -rp "$(echo -e "${YELLOW}Keep existing configuration? [Y/n]: ${RESET}")" keep_env
    keep_env="${keep_env:-y}"

    if [[ "${keep_env,,}" == "y" || "${keep_env,,}" == "yes" ]]; then
        SKIP_ENV=true
        LLM_PROVIDER="$EXISTING_PROVIDER"
        API_KEY_VAR="$EXISTING_KEY_VAR"
        API_KEY_VAL="$EXISTING_KEY_VAL"
        # Determine the provider package to install
        case "$LLM_PROVIDER" in
            ollama)    PROVIDER_PKG="langchain-ollama>=0.2" ;;
            openai)    PROVIDER_PKG="langchain-openai>=0.2" ;;
            anthropic) PROVIDER_PKG="langchain-anthropic>=0.3" ;;
            *)         die "Unknown provider in existing .env: $LLM_PROVIDER" ;;
        esac
        success "Keeping existing configuration (provider: ${LLM_PROVIDER})."
    fi
fi

if [[ "$SKIP_ENV" == false ]]; then
    # Map existing provider to default choice number
    default_choice="1"
    case "$EXISTING_PROVIDER" in
        ollama)    default_choice="1" ;;
        openai)    default_choice="2" ;;
        anthropic) default_choice="3" ;;
    esac

    echo ""
    echo "  1) Ollama  — local/self-hosted (no API key needed)"
    echo "  2) OpenAI  — GPT-4o-mini (requires OPENAI_API_KEY)"
    echo "  3) Anthropic — Claude (requires ANTHROPIC_API_KEY)"
    echo ""
    read -rp "$(echo -e "${YELLOW}Provider [1/2/3, default: ${default_choice}]: ${RESET}")" provider_choice
    provider_choice="${provider_choice:-$default_choice}"

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
            # Show masked existing key as default if switching from same provider
            if [[ "$EXISTING_KEY_VAR" == "OPENAI_API_KEY" && -n "$EXISTING_KEY_VAL" ]]; then
                masked="$(mask_key "$EXISTING_KEY_VAL")"
                read -rp "$(echo -e "${YELLOW}  Enter your OpenAI API key [${masked}]: ${RESET}")" API_KEY_VAL
                API_KEY_VAL="${API_KEY_VAL:-$EXISTING_KEY_VAL}"
            else
                read -rp "$(echo -e "${YELLOW}  Enter your OpenAI API key: ${RESET}")" API_KEY_VAL
            fi
            [[ -n "$API_KEY_VAL" ]] || die "API key cannot be empty."
            ;;
        3)
            LLM_PROVIDER="anthropic"
            PROVIDER_PKG="langchain-anthropic>=0.3"
            API_KEY_VAR="ANTHROPIC_API_KEY"
            if [[ "$EXISTING_KEY_VAR" == "ANTHROPIC_API_KEY" && -n "$EXISTING_KEY_VAL" ]]; then
                masked="$(mask_key "$EXISTING_KEY_VAL")"
                read -rp "$(echo -e "${YELLOW}  Enter your Anthropic API key [${masked}]: ${RESET}")" API_KEY_VAL
                API_KEY_VAL="${API_KEY_VAL:-$EXISTING_KEY_VAL}"
            else
                read -rsp "$(echo -e "${YELLOW}  Enter your Anthropic API key: ${RESET}")" API_KEY_VAL
                echo ""
            fi
            [[ -n "$API_KEY_VAL" ]] || die "API key cannot be empty."
            ;;
        *)
            die "Invalid choice: $provider_choice"
            ;;
    esac

    success "Provider: ${LLM_PROVIDER}"
fi

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
    warn "${INSTALL_DIR} already exists — cleaning stale files."
    # Remove old app files but preserve .env, .venv, and audit logs
    # Use rsync --delete if available, otherwise manual cleanup
    if command -v rsync &>/dev/null; then
        rsync -a --delete \
            --exclude '.env' \
            --exclude '.venv' \
            --exclude '.sysadmin-copilot' \
            --exclude '__pycache__' \
            "${SCRIPT_DIR}/" "$INSTALL_DIR/"
    else
        # Manual: remove old app files then copy fresh
        # Preserve .env, .venv, .sysadmin-copilot (audit logs/reports)
        find "$INSTALL_DIR" -maxdepth 1 -name '*.py' -delete 2>/dev/null || true
        find "$INSTALL_DIR" -maxdepth 1 -name '*.sh' -delete 2>/dev/null || true
        find "$INSTALL_DIR" -maxdepth 1 -name '*.md' -delete 2>/dev/null || true
        find "$INSTALL_DIR" -maxdepth 1 -name '*.txt' -delete 2>/dev/null || true
        [[ -d "$INSTALL_DIR/tools_extra" ]] && rm -rf "$INSTALL_DIR/tools_extra"
        [[ -d "$INSTALL_DIR/docs" ]] && rm -rf "$INSTALL_DIR/docs"
        [[ -d "$INSTALL_DIR/__pycache__" ]] && rm -rf "$INSTALL_DIR/__pycache__"
        cp -r "${SCRIPT_DIR}/." "$INSTALL_DIR/"
    fi
else
    mkdir -p "$INSTALL_DIR"
    cp -r "${SCRIPT_DIR}/." "$INSTALL_DIR/"
fi

chown -R "${SERVICE_USER}:${SERVICE_USER}" "$INSTALL_DIR"
success "Copied app to ${INSTALL_DIR}."

info "Creating Python virtual environment..."
runuser -u "$SERVICE_USER" -- python3 -m venv "${INSTALL_DIR}/.venv"

info "Installing base dependencies..."
runuser -u "$SERVICE_USER" -- "${INSTALL_DIR}/.venv/bin/pip" install --quiet \
    "python-dotenv>=1.0" \
    "langchain>=1.2" \
    "langchain-core>=0.3" \
    "langgraph>=0.2"

info "Installing provider package: ${PROVIDER_PKG}..."
runuser -u "$SERVICE_USER" -- "${INSTALL_DIR}/.venv/bin/pip" install --quiet "${PROVIDER_PKG}"

success "Dependencies installed."

# ─── Step 4: Write .env file ─────────────────────────────────────────────────

header "Step 4: Environment configuration"

if [[ "$SKIP_ENV" == true ]]; then
    # Ensure permissions are correct on existing file
    chown "${SERVICE_USER}:${SERVICE_USER}" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    success "Kept existing ${ENV_FILE} (permissions verified)."
else
    # Preserve extra variables from existing .env (VT_API_KEY, EXTRA_SERVICES, etc.)
    EXTRA_LINES=""
    if [[ -f "$ENV_FILE" ]]; then
        EXTRA_LINES="$(grep -v '^LLM_PROVIDER=\|^OPENAI_API_KEY=\|^ANTHROPIC_API_KEY=\|^[[:space:]]*$\|^#' "$ENV_FILE" 2>/dev/null || true)"
    fi

    {
        echo "LLM_PROVIDER=${LLM_PROVIDER}"
        if [[ -n "$API_KEY_VAR" && -n "$API_KEY_VAL" ]]; then
            echo "${API_KEY_VAR}=${API_KEY_VAL}"
        fi
        if [[ -n "$EXTRA_LINES" ]]; then
            echo "$EXTRA_LINES"
        fi
    } > "$ENV_FILE"

    chown "${SERVICE_USER}:${SERVICE_USER}" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    success "Wrote ${ENV_FILE} (mode 600)."
fi

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
