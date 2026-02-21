# Chapter 1 — Introduction

## The Problem

Debugging a Linux server usually means knowing which commands to run, in what order, and what the output means. That knowledge takes years to accumulate. Even experienced sysadmins spend time searching for the right `journalctl` flags or remembering the `ss` syntax for filtering by state.

Sysadmin Copilot lets you skip that lookup. You describe what you want in plain English, and the agent figures out which tool to call, calls it, and explains the output.

```
❯ Why is the server running slow?

  [check_cpu_and_load]
  [check_top_processes]
  [check_memory]

The server load average is 4.2 over the last minute, which is high for a
2-core machine. The top CPU consumer is 'python3' at 87% — that's your
data pipeline job. Memory is fine: 3.1 GB used of 8 GB.
```

The agent doesn't just run `top`. It reasons about the question, decides which tools are relevant, runs them in sequence, and synthesises the results into a plain-language answer.

---

## Who It's For

- **Self-hosters and homelab users** who manage servers but aren't full-time sysadmins
- **Developers** who need to investigate production issues but find Linux tooling opaque
- **Sysadmins** who want a faster first-pass investigation tool with a built-in audit trail

The tool targets **Linux with systemd**. It assumes `journalctl`, `systemctl`, `ss`, `dig`, `free`, and similar utilities are available.

---

## Sample Session

Here's a realistic multi-turn session. The agent maintains conversation history across turns, so each question can build on the previous one.

```
❯ Show me failed SSH login attempts in the last hour

  [query_journal_logs]

I found 23 failed SSH login attempts in the last hour, all from the IP
address 185.220.101.47. The attempts are brute-forcing the 'root' account.

❯ Is that IP address doing anything else on the network?

  [check_network_connections]

There is no current active connection from 185.220.101.47. The attempts
appear to be automated — all 23 happened in a 4-minute window at 14:32.

❯ Restart fail2ban to make sure it picks up the new entries

  ⚠  The agent wants to: restart_service(fail2ban)
     Allow this action? [y/N]: y

  [restart_service]

fail2ban has been restarted and is now active. It will re-read its log
sources and apply bans based on its current configuration.
```

Notice three things:
1. **The agent used different tools for each question** — journal for logs, network sockets for connections, service restart for the last step.
2. **Context carried across turns** — the second question referred to "that IP address" without repeating it.
3. **The service restart required explicit confirmation** — the safety layer prompted before executing.

---

## Key Features

| Feature | What it means |
|---------|---------------|
| **Natural language interface** | Ask in plain English, get a plain-English answer |
| **ReAct agent loop** | The LLM reasons step-by-step and picks tools adaptively |
| **Multi-turn conversation** | History is preserved until you type `new` |
| **Three-tier safety model** | READ always allowed; WRITE needs confirmation; BLOCKED is rejected outright |
| **Audit log** | Every tool call is logged to JSONL with timestamp and status |
| **Multiple LLM backends** | Ollama (local), OpenAI, or Anthropic — one env var to switch |
| **Configurable service allowlist** | Only named services can be restarted/stopped |

---

## Built-in REPL Commands

Beyond natural-language questions, the REPL has a few built-in commands:

| Command | What it does |
|---------|-------------|
| `help` | Show the command list |
| `tools` | List all 21 available tools with descriptions |
| `audit` | Show the audit log for the current session |
| `audit last N` | Show tool calls from the last N past sessions |
| `new` | Reset conversation history (start fresh) |
| `clear` | Clear the terminal screen |
| `quit` / `exit` | Exit the copilot |

---

Next: [Chapter 2 — Architecture](02-architecture.md)
