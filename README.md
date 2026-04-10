# 🖥️ Sysadmin Copilot

An AI-powered Linux system administration assistant built with LangChain. Ask questions about your server in natural language and get expert-level investigation and responses.

```
❯ Why is the server running slow?

I checked your system and found a few things:

• **CPU Load** is high (load average 4.2 on a 4-core machine). The top consumer
  is a runaway `python3` process (PID 28341) using 98% CPU.
• **Memory** looks OK — 6.2 GB used of 16 GB, no swap pressure.
• **Disk** on `/` is at 87% — getting close. Most of the usage is under `/var/log`
  (12 GB). You might want to rotate those logs.

Recommendation: Investigate PID 28341, and consider cleaning up old logs.
```

## Installation

### Automated install (recommended)

The installer creates a dedicated service account, installs the app, configures sudoers, and sets up a wrapper command:

```bash
sudo bash install.sh
```

The interactive installer will:
1. Ask you to choose an LLM provider (Ollama, OpenAI, or Anthropic)
2. Create the `sysadmin-copilot` system account with appropriate groups
3. Install the app and Python dependencies to `/opt/sysadmin-copilot/`
4. Write the `.env` file with your API key (mode 600)
5. Generate and validate the sudoers file
6. Create the `/usr/local/bin/sysadmin-copilot` wrapper command

Then run from any sudoer account:

```bash
sysadmin-copilot
```

### Manual / development setup

```bash
pip install -r requirements.txt
python agent.py
```

Set up your LLM:

**Option A: Ollama (recommended for demos — fully self-hosted)**

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.1:8b
python agent.py
```

**Option B: Cloud API**

```bash
# OpenAI
pip install langchain-openai
LLM_PROVIDER=openai OPENAI_API_KEY=sk-... python agent.py

# Anthropic (Claude)
pip install langchain-anthropic
LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=sk-ant-... python agent.py
```

**Option C: OpenAI-compatible endpoint (LM Studio, vLLM, LocalAI, etc.)**

```bash
pip install langchain-openai
LLM_PROVIDER=openai OPENAI_BASE_URL=http://localhost:1234/v1 OPENAI_MODEL=my-model OPENAI_API_KEY=not-needed python agent.py
```

### Shell scripts

| Script | Purpose | Run as |
|--------|---------|--------|
| `install.sh` | Full automated install — service account, app, sudoers, wrapper | `sudo bash install.sh` |
| `sysadmin-copilot-configure.sh` | Reconfigure LLM provider, API key, and sudoers after install | `sudo bash sysadmin-copilot-configure.sh` |
| `sync-sudoers.sh` | Regenerate sudoers from `ALLOWED_SERVICES` in `safety.py` | `sudo bash sync-sudoers.sh` |

**Typical workflow after install:**

```bash
# Change LLM provider or API key
sudo bash sysadmin-copilot-configure.sh

# After editing ALLOWED_SERVICES in safety.py
sudo bash sync-sudoers.sh
```

### 3. Talk to your server

```
❯ Show me failed SSH login attempts today
❯ How much disk space is left?
❯ Restart nginx and check if it's healthy
❯ Are there any zombie processes?
❯ What services have failed?
```

## Architecture

```
User (terminal)
    ↓ natural language
LangChain Agent (ReAct pattern)
    ↓ picks a tool
Tool layer (Python functions wrapping subprocess calls)
    ↓ executes
Linux system (journalctl, systemctl, df, etc.)
    ↓ output
Agent interprets results → responds in plain English
```

## Project Structure

```
sysadmin-copilot/
├── agent.py           # Main entry point and REPL loop
├── tools.py           # All agent tools (Linux CLI wrappers)
├── safety.py          # Permission tiers, allowlists, confirmation prompts
├── audit.py           # Command audit logging
├── tools_extra/       # Drop-in directory for custom tools (auto-discovered)
│   ├── _example.py    # Template (skipped by _ prefix)
│   ├── threat_intel.py # VirusTotal + IOC extraction
│   ├── breach_check.py # Have I Been Pwned breach monitoring
│   ├── abuse_ch.py    # URLhaus, MalwareBazaar, ThreatFox
│   ├── abuseipdb.py   # IP reputation scoring
│   └── ransomware_tracker.py  # ransomware.live victim/group tracking
├── install.sh         # Automated service account installer
├── sync-sudoers.sh    # Regenerate sudoers from safety.py ALLOWED_SERVICES
├── requirements.txt   # Python dependencies
├── docs/              # In-depth code walkthrough (8 chapters)
└── README.md          # This file
```

## Available Tools

| Category | Tool | Description |
|----------|------|-------------|
| **Logs** | `query_journal_logs` | Query journalctl with unit, priority, time filters |
| | `read_log_file` | Read from /var/log files with grep |
| | `check_dmesg` | Kernel ring buffer messages |
| **Health** | `check_disk_usage` | Filesystem disk usage (df) |
| | `check_directory_size` | Directory sizes (du) |
| | `check_memory` | RAM and swap usage |
| | `check_cpu_and_load` | CPU info and load averages |
| | `check_top_processes` | Top processes by CPU or memory |
| | `find_zombie_processes` | Detect defunct processes |
| **Services** | `check_service_status` | systemctl status for a service |
| | `list_failed_services` | All failed systemd units |
| | `restart_service` | Restart a service ⚠️ |
| | `stop_service` | Stop a service ⚠️ |
| **Network** | `check_open_ports` | Listening ports (ss) |
| | `check_network_connections` | Active connections by state |
| | `ping_host` | Ping connectivity check |
| | `dns_lookup` | DNS resolution (dig) |
| | `check_url_health` | HTTP health check (curl) |
| **Users** | `check_logged_in_users` | who + recent logins |
| | `check_cron_jobs` | Cron job listings |
| | `find_recent_files` | Recently modified files |
| **Security** | `system_audit` | CIS-aligned security audit (SSH, perms, firewall, kernel) |
| | `check_outdated_packages` | Outdated packages across apt/dnf/yum/snap/flatpak |
| | `update_packages` | Install available package updates ⚠️ |
| **General** | `change_directory` | Change working directory for subsequent tool calls |
| | `run_command` | Run any shell command for ad-hoc investigation |
| | `search_web` | Web search via DuckDuckGo for docs, CVEs, troubleshooting |

⚠️ = Requires user confirmation (write action)

## Safety Model

The safety layer enforces three permission tiers:

- **READ** — Always allowed. Querying logs, checking status, viewing disk usage.
- **WRITE** — Requires explicit user confirmation. The terminal prompts `Allow this action? [y/N]` before executing.
- **BLOCKED** — Never allowed. Patterns like `rm`, `dd`, `shutdown` are rejected even if the agent tries to use them.

### Service Allowlist

Only services listed in `safety.py → ALLOWED_SERVICES` can be restarted or stopped. Edit this set for your environment:

```python
ALLOWED_SERVICES = {
    "nginx",
    "postgresql",
    "docker",
    "redis",
    # add your services here
}
```

### Audit Log

Every command is logged with timestamp, tool name, arguments, and status (OK / BLOCKED / DENIED / CONFIRMED). Type `audit` in the REPL to see the session log, or check `~/.sysadmin-copilot/logs/` for persistent JSONL files.

## Adding New Tools

### Custom tools (plugin directory)

Drop a `.py` file into `tools_extra/` — it's auto-discovered at startup with no edits to core files:

```python
# tools_extra/docker_tools.py
from langchain_core.tools import tool
from tools import run_cmd

@tool
def check_docker_containers(all: bool = False) -> str:
    """List Docker containers, optionally including stopped ones."""
    cmd = ["docker", "ps"]
    if all:
        cmd.append("-a")
    return run_cmd(cmd)

@tool
def restart_container(name: str) -> str:
    """Restart a Docker container. REQUIRES CONFIRMATION."""
    return run_cmd(["docker", "restart", name])

# List write tools that need user confirmation before executing.
# Read tools (check_docker_containers) run without prompting.
WRITE_TOOLS = {"restart_container"}
```

Rules:
- Files must be `.py` and not start with `_`
- Each `@tool` function is registered automatically
- A single file can contain any mix of read and write tools
- Declare `WRITE_TOOLS = {"name1", "name2"}` for tools that need confirmation
- See `tools_extra/_example.py` for a full template

### Core tools

To add a tool that ships with the project:

1. Add a function in `tools.py` with a `@tool` decorator and a clear docstring.
2. Add it to `ALL_TOOLS` at the bottom of `tools.py`.
3. If it's a write action, add its name to `WRITE_TOOLS` in `safety.py`.
4. If the write action calls `sudo`, run `sudo bash sync-sudoers.sh` to update the sudoers file.

The safety and audit wrappers are applied automatically — no changes needed in `agent.py`. See [docs/08-extending.md](docs/08-extending.md) for a full walkthrough.

## Environment Variables

### LLM Provider

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `ollama` | LLM backend: `ollama`, `openai`, `anthropic` |
| `OLLAMA_MODEL` | `llama3.1:8b` | Ollama model name |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_NUM_CTX` | auto | Ollama context window size in tokens. Auto-sized based on loaded tools; override if needed |
| `OPENAI_MODEL` | `gpt-4o-mini` | OpenAI model name |
| `OPENAI_BASE_URL` | — | OpenAI-compatible endpoint URL (e.g. `http://localhost:1234/v1`) |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Anthropic model name |
| `ANTHROPIC_API_KEY` | — | Anthropic API key |
| `MAX_OUTPUT_TOKENS` | per-provider | Maximum output tokens. Defaults: Ollama 4096, OpenAI 16384, Anthropic 8192. Overrides all providers when set |
| `MAX_HISTORY_CHARS` | auto | Max conversation history in chars. Auto-calculated from model context window; override to set manually |

### Safety & Permissions

| Variable | Default | Description |
|----------|---------|-------------|
| `EXTRA_SERVICES` | — | Comma-separated services to add to `ALLOWED_SERVICES` at runtime, e.g. `myapp,worker` |
| `EXTRA_COMMANDS` | — | Comma-separated commands to add to `ALLOWED_COMMANDS` for `run_command`, e.g. `nmap,tcpdump` |
| `LOG_PATHS` | `/var/log` | Comma-separated path prefixes allowed for `read_log_file`, e.g. `/var/log,/run/log` |

### Threat Intelligence API Keys

Plugins are lazy-loaded — only plugins whose API keys are set will load. This keeps tool count low for small models.

| Variable | Free? | Plugin | Description |
|----------|-------|--------|-------------|
| `VT_API_KEY` | Yes (free tier) | `threat_intel.py` | VirusTotal — hash/IP/domain lookups, IOC extraction |
| `HIBP_API_KEY` | Partially | `breach_check.py` | Have I Been Pwned — email/domain breach monitoring (some tools free without key) |
| `ABUSECH_AUTH_KEY` | Yes (free key) | `abuse_ch.py` | abuse.ch — URLhaus, MalwareBazaar, ThreatFox. Get key at https://auth.abuse.ch/ |
| `ABUSEIPDB_API_KEY` | Yes (1K/day) | `abuseipdb.py` | AbuseIPDB — IP reputation scoring and blacklists |
| `RANSOMWARE_LIVE_API_KEY` | Paid | `ransomware_tracker.py` | ransomware.live PRO — ransomware group/victim tracking |
| `LEAKCHECK_API_KEY` | Paid | `leakcheck.py` | LeakCheck Pro — detailed breach search by email, username, domain, phone |
| `DEHASHED_API_KEY` | Paid | `dehashed.py` | DeHashed — breach search with actual leaked passwords/hashes (~$0.03/query) |

## Documentation

The `docs/` folder contains an 8-chapter code walkthrough that explains every module in depth — architecture, design decisions, and annotated snippets from the real source:

- [docs/README.md](docs/README.md) — table of contents
- [01 — Introduction](docs/01-introduction.md) · [02 — Architecture](docs/02-architecture.md) · [03 — The Agent](docs/03-the-agent.md) · [04 — Tools](docs/04-tools.md)
- [05 — Safety Layer](docs/05-safety-layer.md) · [06 — Audit Logger](docs/06-audit-logger.md) · [07 — Configuration](docs/07-configuration.md) · [08 — Extending](docs/08-extending.md)

---

## Ideas for Extension

- **Docker management** — list/restart containers, tail container logs
- **Database health** — PostgreSQL connections, slow queries, table sizes
- **Backup verification** — check backup age, verify checksums
- **Multi-host via SSH** — run tools on remote servers
- **Slack/Matrix bot** — expose the copilot as a team chatbot
- **MCP server** — expose tools via Model Context Protocol
- **Prometheus/Grafana** — query metrics as an agent tool
- **Ansible integration** — run playbooks from natural language

## License

MIT — Use it, modify it, share it with your Linux users group.
