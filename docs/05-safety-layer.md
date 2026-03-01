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

The entire configuration lives at the top of `safety.py` (lines 20–67):

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
BLOCKED_PATTERNS = [
    "rm ", "rm -",
    "dd ",
    "mkfs",
    "shutdown",
    "reboot",
    "poweroff",
    "halt",
    "init 0",
    "init 6",
    "> /dev/",
    "chmod 777",
    ":(){ :|:",  # fork bomb
]
```

`WRITE_TOOLS` is a set of tool names. Anything not in this set is treated as READ-only.

`ALLOWED_SERVICES` is the allowlist for the service management tools. If the agent tries to restart a service not in this list, it's denied before any subprocess runs.

`BLOCKED_PATTERNS` is a list of substrings. If any tool argument contains one of these patterns, the call is rejected. This is a last-resort defence against prompt injection — if someone tricks the LLM into calling a tool with `path="/ && rm -rf /"`, the pattern `"rm -"` will catch it.

---

## The `wrap_tools()` Dispatcher

`wrap_tools()` (lines 76–84) is the main entry point. It iterates over the tool list and applies the appropriate wrapper:

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

## `_wrap_read_tool()` — Audit + Blocked Pattern Check

Lines 86–109:

```python
    def _wrap_read_tool(self, t: BaseTool, audit_logger) -> BaseTool:
        """Wrap a read-only tool with audit logging."""
        original_func = t.func

        @wraps(original_func)
        def wrapped(*args, **kwargs):
            # Check for blocked patterns against individual string argument values
            # (avoids false matches against Python's dict/tuple repr syntax)
            str_values = [v for v in args if isinstance(v, str)]
            str_values += [v for v in kwargs.values() if isinstance(v, str)]
            for pattern in BLOCKED_PATTERNS:
                if any(pattern in val for val in str_values):
                    msg = f"[BLOCKED] Dangerous pattern detected: '{pattern}'"
                    if audit_logger:
                        audit_logger.log_command(t.name, kwargs, blocked=True)
                    return msg

            if audit_logger:
                audit_logger.log_command(t.name, kwargs)

            return original_func(*args, **kwargs)

        t.func = wrapped
        return t
```

The blocked-pattern check iterates over **individual string values**:

```python
str_values = [v for v in args if isinstance(v, str)]
str_values += [v for v in kwargs.values() if isinstance(v, str)]
```

This is subtle but important. An earlier version checked `str(kwargs)`, which produced Python's dict representation: `"{'path': '/var/log/syslog', 'lines': 50}"`. That string contains `{`, `'`, `:` characters that might accidentally match patterns in `BLOCKED_PATTERNS`, and it includes all the Python syntax noise that isn't actually argument content.

By extracting only the string values, the check is precise: if `grep="shutdown"` is passed as a filter pattern, it's caught; if `grep="check shutdown logs"` is passed as a filter, the substring `"shutdown"` in the value is still caught. Only actual argument content is inspected.

`@wraps(original_func)` preserves the original function's name, docstring, and signature. Without it, LangChain would see a generic `wrapped` function instead of the original tool.

Finally, `t.func = wrapped` replaces the underlying function on the tool object in place and returns the same tool object. The LLM's tool schema (name, description, parameters) is untouched.

---

## `_wrap_write_tool()` — Allowlist + Confirmation

Lines 111–152:

```python
    def _wrap_write_tool(self, t: BaseTool, audit_logger) -> BaseTool:
        """Wrap a write tool with confirmation prompt and allowlist check."""
        original_func = t.func

        @wraps(original_func)
        def wrapped(*args, **kwargs):
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

Next: [Chapter 6 — Audit Logger](06-audit-logger.md)
