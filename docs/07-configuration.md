# Chapter 7 — Configuration & Installation

## LLM Provider Selection

The copilot supports three LLM backends, selected via the `LLM_PROVIDER` environment variable. The default is Ollama for fully local, self-hosted usage.

```bash
# Ollama (local) — default
python agent.py

# OpenAI
LLM_PROVIDER=openai OPENAI_API_KEY=sk-... python agent.py

# Anthropic
LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=sk-ant-... python agent.py
```

Switching providers requires no code changes. The `get_llm()` function in `agent.py` reads `LLM_PROVIDER` and returns the appropriate LangChain chat model object. The rest of the code is provider-agnostic.

---

## Environment Variables Reference

| Variable | Default | Purpose |
|----------|---------|---------|
| `LLM_PROVIDER` | `ollama` | Backend: `ollama`, `openai`, `anthropic` |
| `OLLAMA_MODEL` | `llama3.1:8b` | Ollama model name |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `OPENAI_MODEL` | `gpt-4o-mini` | OpenAI model name |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Anthropic model name |
| `EXTRA_SERVICES` | — | Comma-separated services to add to `ALLOWED_SERVICES` at runtime |
| `LOG_PATHS` | `/var/log` | Comma-separated path prefixes allowed for `read_log_file` |

`EXTRA_SERVICES` and `LOG_PATHS` let you extend the allowlists without editing Python source:

```bash
# Allow the copilot to restart 'myapp' and 'myworker'
EXTRA_SERVICES=myapp,myworker python agent.py

# Allow reading from custom log directories
LOG_PATHS=/var/log,/run/log,/home/myapp/logs python agent.py
```

---

## Service Account Design

Running the copilot as a dedicated system account (`sysadmin-copilot`) follows the principle of least privilege:

- The account has **no home directory write access** outside `~/.sysadmin-copilot/`
- It belongs to the `systemd-journal` and `adm` groups for read-only log access
- It can only `sudo systemctl restart/stop` the services in the sudoers allowlist
- No interactive shell password; operators `su` or `ssh` into it explicitly

This means a compromised session (via prompt injection, a bug, or operator error) has a contained blast radius. It cannot install packages, modify system files, or restart arbitrary services.

---

## `install.sh` Walkthrough

The installer runs six steps. Here's what each does:

**Step 1: Choose LLM provider**
Interactive prompt. Writes `LLM_PROVIDER` and the API key to `${INSTALL_DIR}/.env`.

**Step 2: Create service account**
```bash
useradd --system --create-home --shell /bin/bash sysadmin-copilot
usermod -aG systemd-journal sysadmin-copilot
usermod -aG adm sysadmin-copilot
```
`--system` creates a system account (UID < 1000, no aging). The `systemd-journal` group grants `journalctl` access; the `adm` group grants `/var/log` and `dmesg` access.

**Step 3: Install application**
```bash
cp -r "${SCRIPT_DIR}/." "$INSTALL_DIR/"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "$INSTALL_DIR"
runuser -u "$SERVICE_USER" -- python3 -m venv "${INSTALL_DIR}/.venv"
runuser -u "$SERVICE_USER" -- "${INSTALL_DIR}/.venv/bin/pip" install ...
```
The venv is owned by the service account, so the account can install packages at its own risk without touching system Python.

**Step 4: Write `.env` file**
```bash
chown "${SERVICE_USER}:${SERVICE_USER}" "$ENV_FILE"
chmod 600 "$ENV_FILE"
```
Mode 600 — readable only by the service account owner. API keys are stored here.

**Step 5: Generate and validate sudoers**

The installer calls `build_alias()` to generate the `Cmnd_Alias` blocks for every service in `ALLOWED_SERVICES`, then validates with `visudo -c -f` before placing the file. If validation fails, the temp file is removed and the installer aborts. A bad sudoers file could lock you out, so this check is non-negotiable.

**Step 6: Create wrapper command**
```bash
cat > /usr/local/bin/sysadmin-copilot <<WRAPPER
#!/usr/bin/env bash
exec sudo -u sysadmin-copilot bash -c '
    set -a
    source /opt/sysadmin-copilot/.env
    set +a
    exec /opt/sysadmin-copilot/.venv/bin/python /opt/sysadmin-copilot/agent.py
'
WRAPPER
chmod 755 /usr/local/bin/sysadmin-copilot
```

Any sudoer can run `sysadmin-copilot`. The wrapper `sudo`s into the service account, sources the `.env` file, and launches the agent. `set -a` / `set +a` exports all sourced variables automatically.

---

## `sync-sudoers.sh` — Keeping Sudoers in Sync

When you add a service to `ALLOWED_SERVICES` in `safety.py`, you must also add it to the sudoers file — otherwise the Python layer will allow the action but `sudo systemctl restart newservice` will be rejected by the OS.

`sync-sudoers.sh` automates this:

```bash
sudo bash sync-sudoers.sh
```

It parses `ALLOWED_SERVICES` directly from `safety.py` using a small embedded Python script:

```bash
mapfile -t SERVICES < <(python3 - <<PYEOF
import re, sys

content = open("${SAFETY_PY}").read()
match = re.search(r'ALLOWED_SERVICES\s*=\s*\{([^}]+)\}', content, re.DOTALL)
if not match:
    sys.exit("Could not find ALLOWED_SERVICES block in safety.py")

services = sorted(re.findall(r'"([^"]+)"', match.group(1)))
if not services:
    sys.exit("ALLOWED_SERVICES appears to be empty — aborting")

print("\n".join(services))
PYEOF
)
```

Then it regenerates the sudoers file, shows a diff if one exists, validates with `visudo -c -f`, and atomically replaces the old file. If validation fails, nothing changes.

**Workflow when adding a service:**
1. Add the service name to `ALLOWED_SERVICES` in `safety.py`
2. Run `sudo bash sync-sudoers.sh`
3. Optionally run `sudo visudo -c` to confirm the full sudoers config is valid

---

## Manual Installation (Reference)

If you prefer to install by hand instead of using `install.sh`:

```bash
# 1. Create account
sudo useradd --system --create-home --shell /bin/bash sysadmin-copilot
sudo usermod -aG systemd-journal sysadmin-copilot
sudo usermod -aG adm sysadmin-copilot

# 2. Install app
sudo mkdir /opt/sysadmin-copilot
sudo cp -r . /opt/sysadmin-copilot/
sudo chown -R sysadmin-copilot:sysadmin-copilot /opt/sysadmin-copilot
sudo -u sysadmin-copilot python3 -m venv /opt/sysadmin-copilot/.venv
sudo -u sysadmin-copilot /opt/sysadmin-copilot/.venv/bin/pip install -r /opt/sysadmin-copilot/requirements.txt

# 3. Store API key
sudo -u sysadmin-copilot bash -c 'cat > /opt/sysadmin-copilot/.env' <<'EOF'
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
EOF
sudo chmod 600 /opt/sysadmin-copilot/.env

# 4. Configure sudoers (see CLAUDE.md for the full template)
sudo visudo -f /etc/sudoers.d/sysadmin-copilot

# 5. Run
sysadmin-copilot
```

---

## Audit Log Location

When running as the service account, logs land in the account's home directory:

```
/home/sysadmin-copilot/.sysadmin-copilot/logs/session_YYYYMMDD_HHMMSS.jsonl
```

When running as yourself during development:

```
~/.sysadmin-copilot/logs/session_YYYYMMDD_HHMMSS.jsonl
```

The `AuditLogger` constructor defaults to `~/.sysadmin-copilot/logs` and creates the directory if it doesn't exist.

---

Next: [Chapter 8 — Extending: Add Your Own Tool](08-extending.md)
