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

The entire configuration lives at the top of `safety.py` (lines 20–92):

```python
# Tools that require user confirmation before execution
WRITE_TOOLS = {
    "restart_service",
    "stop_service",
    "update_packages",
}

# Services that are allowed to be restarted/stopped
# Add your services here — anything not listed will be blocked
ALLOWED_SERVICES = {
    "nginx",
    "apache2",
    "httpd",
    "postgresql",
    "mysql",
    "mariadb",
    "docker",
    "redis",
    "memcached",
    "php-fpm",
    "php8.1-fpm",
    "gunicorn",
    "uwsgi",
    "cron",
    "postfix",
    # Add more as needed for your environment
}

# Extend the allowlist at runtime without editing this file:
#   EXTRA_SERVICES=myapp,myworker python agent.py
_extra_services = os.environ.get("EXTRA_SERVICES", "")
if _extra_services:
    ALLOWED_SERVICES |= {s.strip() for s in _extra_services.split(",") if s.strip()}

# Arguments that are NEVER allowed in any tool
# NOTE: This is defense-in-depth, not a security boundary.
# The real boundary is OS-level permissions (service account + sudoers).
# See docs/05-safety-layer.md "Threat Model and Limitations" for details.
BLOCKED_PATTERNS = [
    # File removal / destruction
    "rm ", "rm -",
    "rmdir ",
    "unlink ",
    "shred ",
    # Disk / device
    "dd ",
    "mkfs",
    "> /dev/",
    "tee /dev/",
    # System state
    "shutdown",
    "reboot",
    "poweroff",
    "halt",
    "init 0",
    "init 6",
    # Permissions
    "chmod 777", "chmod 0777",
    "chmod a+rwx",
    # Fork bomb
    ":(){ :|:",
    # File overwrite / zeroing
    "truncate ",
    # find's destructive flag
    "-delete",
    # Encoding evasion (decode payload then execute)
    "base64 -d",
    "base64 --decode",
    # Piped shell execution (catches encoded payloads piped to shell)
    "| bash", "| sh",
    "| /bin/bash", "| /bin/sh",
]
```

`WRITE_TOOLS` is a set of tool names. Anything not in this set is treated as READ-only.

`ALLOWED_SERVICES` is the allowlist for the service management tools. If the agent tries to restart a service not in this list, it's denied before any subprocess runs.

`BLOCKED_PATTERNS` is a list of substrings. If any tool argument contains one of these patterns, the call is rejected. This is a last-resort defence against prompt injection — if someone tricks the LLM into calling a tool with `path="/ && rm -rf /"`, the pattern `"rm -"` will catch it.

---

## The `wrap_tools()` Dispatcher

`wrap_tools()` (lines 116–124) is the main entry point. It iterates over the tool list and applies the appropriate wrapper:

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

## `_check_blocked_patterns()` — Shared Normalization Helper

Both `_wrap_read_tool` and `_wrap_write_tool` delegate to a shared helper that normalizes argument strings before checking them against `BLOCKED_PATTERNS`:

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

The blocked-pattern check is **defense-in-depth** — a first line of defense against accidental destruction, not a security sandbox. It's important to understand what it protects against and what it doesn't.

### What blocked patterns defend against

- **LLM misunderstanding**: The model interprets "clean up disk space" as `rm -rf /tmp/*`. The pattern `"rm "` catches this before any subprocess runs.
- **Basic prompt injection**: Crafted log output or user input tricks the model into calling `run_command(command="dd if=/dev/zero of=/dev/sda")`. The pattern `"dd "` catches this.
- **Obvious destructive commands**: `shutdown`, `reboot`, `mkfs`, fork bombs — commands that are never appropriate for a read-mostly sysadmin assistant.

### What blocked patterns don't defend against

- **Sophisticated evasion**: A determined attacker can use hex encoding (`echo 726d202d7266202f | xxd -r -p`), alternative interpreters (`python3 -c "import os; os.remove(...)"`), or exploit edge cases the pattern list doesn't cover.
- **Novel destructive commands**: The list can never be exhaustive. New tools, shell builtins, and creative combinations can bypass substring matching.
- **Non-command attacks**: The patterns only check tool argument strings. They can't prevent the LLM from leaking sensitive data it read from logs, or from making poor recommendations.

### The three-layer defense model

The real security comes from layering multiple controls:

```
Layer 1: Blocked Patterns    — catches common mistakes and obvious attacks
Layer 2: User Confirmation   — human approves every WRITE action before execution
Layer 3: OS Permissions      — service account + sudoers limit what can actually run
```

**Layer 3 is the security boundary.** Even if layers 1 and 2 are bypassed, the `sysadmin-copilot` service account can only:
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

### Blocked pattern coverage

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

**Known gaps** (accepted, because Layer 3 covers them):
- `perl -e`, `python -c`, `ruby -e` — scripting language one-liners
- `mv` to overwrite critical files
- `curl ... | python` — piped execution via other interpreters
- Hex/octal encoding, variable expansion tricks

---

## Shell Injection Audit

Since tools execute shell commands via `subprocess.run()`, it's worth documenting where user-controlled input flows into those commands.

### `run_cmd()` — the subprocess helper

All tools use `run_cmd(cmd, timeout)` which calls `subprocess.run(cmd, ...)` with a **list** argument. This means `subprocess` uses `execvp` directly — no shell interpretation, no injection risk — *unless* the list is `["bash", "-c", some_string]`, which does invoke a shell.

### Code paths through `bash -c`

**`run_command` — freeform shell** (lines 812–843): Takes a user-supplied `command` string and runs `bash -c {command}`. Bare `cd` commands are intercepted and handled via `os.chdir()` instead (see [Chapter 4 — Tools](04-tools.md#bare-cd-interception)). This is the most exposed surface. It relies on `BLOCKED_PATTERNS` (with normalization) and OS permissions for safety.

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
