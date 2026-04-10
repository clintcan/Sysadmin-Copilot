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

# OpenAI-compatible endpoint (LM Studio, vLLM, LocalAI, etc.)
LLM_PROVIDER=openai OPENAI_BASE_URL=http://localhost:1234/v1 OPENAI_MODEL=my-model OPENAI_API_KEY=not-needed python agent.py
```

Switching providers requires no code changes. The `get_llm()` function in `agent.py` reads `LLM_PROVIDER` and returns the appropriate LangChain chat model object. The rest of the code is provider-agnostic.

When `OPENAI_BASE_URL` is set, the startup message shows the endpoint: `Using OpenAI-compatible (my-model) at http://localhost:1234/v1`.

### Choosing a model — safety considerations

All three tested backends (gpt-4o-mini, llama3.1:8b, qwen3.5) score above 99% on the eval suite (see `tests/README.md`), but they behave differently at the boundary:

- **Larger/cloud models** (gpt-4o-mini, Anthropic) tend to self-censor — they refuse dangerous requests in text before calling a tool. This gives you defense from both the model and the safety layer.
- **Smaller/local models** (llama3.1:8b, qwen3.5) tend to attempt the action and rely on the safety layer to block it. The safety layer handles this correctly, but there is no model-level backup.

Both strategies are valid — the safety layer was designed to be the primary defense regardless of model. However, if you use a small or untested model, the safety layer and the service account's OS permissions become your only protection. Never bypass `wrap_tools()`, and always run as the least-privilege service account in production.

For a detailed analysis, see [Chapter 5 — Model Behavior and the Safety Layer](05-safety-layer.md#model-behavior-and-the-safety-layer).

---

## Environment Variables Reference

### LLM Provider

| Variable | Default | Purpose |
|----------|---------|---------|
| `LLM_PROVIDER` | `ollama` | Backend: `ollama`, `openai`, `anthropic` |
| `OLLAMA_MODEL` | `llama3.1:8b` | Ollama model name |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_NUM_CTX` | auto | Ollama context window in tokens. Auto-sized based on loaded tools; override if needed |
| `OPENAI_MODEL` | `gpt-4o-mini` | OpenAI model name |
| `OPENAI_BASE_URL` | — | OpenAI-compatible endpoint URL, e.g. `http://localhost:1234/v1` |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Anthropic model name |
| `MAX_OUTPUT_TOKENS` | per-provider | Maximum output tokens for LLM responses. Defaults: Ollama 4096, OpenAI 16384, Anthropic 8192. Overrides all providers when set |
| `MAX_HISTORY_CHARS` | auto | Max conversation history in chars. Auto-calculated from model context window; override to set manually |

### Safety & Permissions

| Variable | Default | Purpose |
|----------|---------|---------|
| `EXTRA_SERVICES` | — | Comma-separated services to add to `ALLOWED_SERVICES` at runtime |
| `EXTRA_COMMANDS` | — | Comma-separated commands to add to `ALLOWED_COMMANDS` at runtime |
| `LOG_PATHS` | `/var/log` | Comma-separated path prefixes allowed for `read_log_file` |

### Threat Intelligence API Keys

Plugins are lazy-loaded: only plugins whose required API keys are set will load at startup. This keeps the tool count low for small models with limited context windows.

| Variable | Free? | Plugin | Purpose |
|----------|-------|--------|---------|
| `VT_API_KEY` | Yes (free tier) | `threat_intel.py` | VirusTotal — hash/IP/domain lookups, IOC extraction |
| `HIBP_API_KEY` | Partially | `breach_check.py` | Have I Been Pwned — email/domain breach monitoring |
| `ABUSECH_AUTH_KEY` | Yes (free key) | `abuse_ch.py` | abuse.ch — URLhaus, MalwareBazaar, ThreatFox |
| `ABUSEIPDB_API_KEY` | Yes (1K/day) | `abuseipdb.py` | AbuseIPDB — IP reputation scoring and blacklists |
| `RANSOMWARE_LIVE_API_KEY` | Paid | `ransomware_tracker.py` | ransomware.live PRO — ransomware group/victim tracking |
| `LEAKCHECK_API_KEY` | Paid | `leakcheck.py` | LeakCheck Pro — detailed breach search by email, username, domain, phone |
| `DEHASHED_API_KEY` | Paid | `dehashed.py` | DeHashed — breach search with actual leaked passwords/hashes (~$0.03/query) |

`breach_check.py` and `leakcheck.py` always load because some of their tools work without a key (HIBP public endpoints and LeakCheck public API).

```bash
# Allow the copilot to restart 'myapp' and 'myworker'
EXTRA_SERVICES=myapp,myworker python agent.py

# Allow additional commands in run_command (e.g. for security scanning)
EXTRA_COMMANDS=nmap,tcpdump python agent.py

# Allow reading from custom log directories
LOG_PATHS=/var/log,/run/log,/home/myapp/logs python agent.py

# Enable threat intel plugins
VT_API_KEY=your-key ABUSEIPDB_API_KEY=your-key python agent.py
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
