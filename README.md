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

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Set up your LLM

**Option A: Ollama (recommended for demos — fully self-hosted)**

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull a model
ollama pull llama3.1:8b

# Run the copilot
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
├── agent.py          # Main entry point and REPL loop
├── tools.py          # All agent tools (Linux CLI wrappers)
├── safety.py         # Permission tiers, allowlists, confirmation prompts
├── audit.py          # Command audit logging
├── requirements.txt  # Python dependencies
└── README.md         # This file
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

1. Add a function in `tools.py`:

```python
@tool
def check_docker_containers(all: bool = False) -> str:
    """List Docker containers, optionally including stopped ones.

    Args:
        all: If True, show all containers including stopped.
    """
    cmd = ["docker", "ps"]
    if all:
        cmd.append("-a")
    return run_cmd(cmd)
```

2. Add it to `ALL_TOOLS` at the bottom of `tools.py`.

3. If it's a write action, add its name to `WRITE_TOOLS` in `safety.py`.

That's it — the agent will automatically discover and use the new tool.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `ollama` | LLM backend: `ollama`, `openai`, `anthropic` |
| `OLLAMA_MODEL` | `llama3.1:8b` | Ollama model name |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `OPENAI_MODEL` | `gpt-4o-mini` | OpenAI model name |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Anthropic model name |
| `ANTHROPIC_API_KEY` | — | Anthropic API key |

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
