# Chapter 6 — Audit Logger (`audit.py`)

## Why Audit Logging Matters

When an AI agent runs commands on your server, you need a record. Not just for debugging — for accountability. If something unexpected happens, you want to answer:

- What did the agent run?
- When?
- Did it have permission?
- Did a human confirm it?

The audit logger answers all of these. Every tool call is recorded with a timestamp, the tool name, the arguments, and a status code. The log is written both in memory (for `audit` display in the current session) and to a JSONL file on disk (for `audit last N` review later).

---

## JSONL Format

The log file is **JSONL** — one JSON object per line. This is easy to parse (one `json.loads()` per line) and easy to inspect with standard tools (`grep`, `jq`).

Two entry types are written:

**Tool call** (every tool invocation):
```json
{"timestamp": "2024-01-15T14:32:07.845123", "tool": "check_service_status", "args": {"service": "nginx"}, "status": "OK"}
{"timestamp": "2024-01-15T14:33:01.123456", "tool": "restart_service", "args": {"service": "nginx"}, "status": "CONFIRMED"}
{"timestamp": "2024-01-15T14:34:00.000000", "tool": "stop_service", "args": {"service": "mysql"}, "status": "DENIED"}
```

**Interaction summary** (one per REPL turn):
```json
{"timestamp": "2024-01-15T14:32:10.001", "type": "interaction", "user_input": "Check nginx status", "response_length": 342}
```

The interaction entry stores only the response length, not the full text. This keeps the log manageable while still providing a timeline of what was asked.

---

## `log_command()` and `log_interaction()`

The two logging methods (`audit.py:31–57`):

```python
    def log_command(
        self,
        tool_name: str,
        args: dict,
        blocked: bool = False,
        denied: bool = False,
        confirmed: bool = False,
    ):
        """Log a tool invocation."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "tool": tool_name,
            "args": _sanitize_args(args),
            "status": _get_status(blocked, denied, confirmed),
        }
        self.entries.append(entry)
        self._write_to_file(entry)

    def log_interaction(self, user_input: str, agent_response: str):
        """Log a full user↔agent interaction (summary only)."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": "interaction",
            "user_input": user_input,
            "response_length": len(agent_response),
        }
        self._write_to_file(entry)
```

`log_command()` appends to both `self.entries` (for `show()`) and the file. `log_interaction()` only writes to the file — there's no need to show interactions in the `audit` command display, since the user just had that conversation.

The `_get_status()` helper translates the three boolean flags into a string:

```python
def _get_status(blocked: bool, denied: bool, confirmed: bool) -> str:
    if blocked:
        return "BLOCKED"
    elif denied:
        return "DENIED"
    elif confirmed:
        return "CONFIRMED"
    return "OK"
```

`OK` is the default for all read-only tool calls. `CONFIRMED` is for write tools the user approved. `DENIED` is for write tools the user rejected. `BLOCKED` is for anything caught by the blocked-pattern check or the service allowlist.

---

## `_sanitize_args()`

Before logging, arguments are sanitised (`audit.py:182–192`):

```python
def _sanitize_args(args: dict) -> dict:
    """Remove potentially sensitive values, keep structure."""
    if not args:
        return {}
    sanitized = {}
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 200:
            sanitized[k] = v[:200] + "..."
        else:
            sanitized[k] = v
    return sanitized
```

String values longer than 200 characters are truncated. This prevents the log file from bloating when a tool is called with a very long grep pattern or path. The structure (keys and non-string values) is preserved.

---

## `show()` — Current Session

`show()` (`audit.py:59–90`) renders the current session's tool log to the terminal:

```python
    def show(self):
        """Display the audit log for the current session."""
        if not self.entries:
            print("\n\033[90mNo commands logged yet this session.\033[0m\n")
            return

        print(f"\n\033[33m{'─' * 60}\033[0m")
        print(f"\033[33m  Audit Log — Session {self.session_start.strftime('%Y-%m-%d %H:%M')}\033[0m")
        print(f"\033[33m  Log file: {self.log_file}\033[0m")
        print(f"\033[33m{'─' * 60}\033[0m")

        for entry in self.entries:
            ts = entry["timestamp"].split("T")[1][:8]
            tool = entry["tool"]
            status = entry["status"]
            args_str = _format_args(entry["args"])

            # Color-code status — pad plain text first so ANSI codes don't skew alignment
            status_padded = f"{status:<9}"
            if status == "BLOCKED":
                status_str = f"\033[31m{status_padded}\033[0m"
            elif status == "DENIED":
                status_str = f"\033[33m{status_padded}\033[0m"
            elif status == "CONFIRMED":
                status_str = f"\033[32m{status_padded}\033[0m"
            else:
                status_str = f"\033[90m{status_padded}\033[0m"

            print(f"  \033[90m{ts}\033[0m  {status_str}  \033[36m{tool}\033[0m  {args_str}")

        print(f"\n  Total commands: {len(self.entries)}")
        print(f"\033[33m{'─' * 60}\033[0m\n")
```

The ANSI alignment trick: status strings like `"BLOCKED"` and `"OK"` have different lengths (7 vs 2). Padding them *before* adding ANSI colour codes ensures the columns line up. If you padded after adding ANSI codes, the invisible escape sequences would throw off the count.

```python
status_padded = f"{status:<9}"           # "OK       " (9 chars, plain text)
status_str = f"\033[90m{status_padded}\033[0m"  # add colour around the padded string
```

Sample output:

```
────────────────────────────────────────────────────────────
  Audit Log — Session 2024-01-15 14:32
  Log file: /home/sysadmin-copilot/.sysadmin-copilot/logs/session_20240115_143200.jsonl
────────────────────────────────────────────────────────────
  14:32:07  OK         check_service_status  service=nginx
  14:33:01  CONFIRMED  restart_service       service=nginx
  14:34:00  DENIED     stop_service          service=mysql

  Total commands: 3
────────────────────────────────────────────────────────────
```

---

## `show_last()` — Past Sessions

`show_last(count)` (`audit.py:92–161`) reads JSONL files from previous runs:

```python
    def show_last(self, count: int = 1):
        """Display audit entries from the most recent previous session(s)."""
        log_dir = os.path.dirname(self.log_file)
        try:
            files = sorted(
                [f for f in os.listdir(log_dir) if f.startswith("session_") and f.endswith(".jsonl")],
                reverse=True,
            )
        except OSError:
            print("\033[90mNo past sessions found.\033[0m\n")
            return

        current = os.path.basename(self.log_file)
        past_files = [f for f in files if f != current]
        ...
        for fname in past_files[:count]:
            ...
            with open(fpath) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
```

Files are sorted reverse-alphabetically. Because filenames are `session_YYYYMMDD_HHMMSS.jsonl`, reverse alphabetical order is reverse chronological order — the most recent file is first.

The current session's file is excluded from `past_files`. That way `audit last 1` shows the previous session, not the current one.

Each line is parsed with `json.loads()` inside a `try/except` — partial writes (e.g., if the process was killed mid-write) are silently skipped.

Only entries with a `"tool"` key are displayed (filtering out interaction summary entries). The same colour-coding logic from `show()` applies.

---

## REPL Commands

| Command | What it calls |
|---------|--------------|
| `audit` | `audit.show()` — current session only |
| `audit last` | `audit.show_last(1)` — previous 1 session |
| `audit last 3` | `audit.show_last(3)` — previous 3 sessions |

---

Next: [Chapter 7 — Configuration & Installation](07-configuration.md)
