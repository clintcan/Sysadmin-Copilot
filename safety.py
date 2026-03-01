"""
Safety Layer for Sysadmin Copilot

Enforces permission tiers so the agent can't do anything dangerous without
explicit user confirmation. This is the most important part of the project.

Three permission levels:
  READ    — Always allowed (logs, status, disk usage)
  WRITE   — Requires user confirmation (restart, stop services)
  BLOCKED — Never allowed (rm, dd, shutdown, etc.)
"""

import os
from functools import wraps
from langchain_core.tools import BaseTool


# ─── Configuration ────────────────────────────────────────────────────────────

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


class SafetyLayer:
    """Wraps tools with confirmation prompts and allowlist checks."""

    def __init__(self, allowed_services: set = None):
        self.allowed_services = allowed_services or ALLOWED_SERVICES

    def wrap_tools(self, tools: list, audit_logger=None) -> list:
        """Wrap a list of tools with safety checks."""
        wrapped = []
        for t in tools:
            if t.name in WRITE_TOOLS:
                wrapped.append(self._wrap_write_tool(t, audit_logger))
            else:
                wrapped.append(self._wrap_read_tool(t, audit_logger))
        return wrapped

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
