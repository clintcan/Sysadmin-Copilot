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
import re
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

# Commands allowed in run_command (allowlist approach).
# Only the first word of the command (the binary name) is checked.
# This is the primary safety boundary for run_command — anything not listed
# here is denied before it reaches the shell.
# Extend at runtime via EXTRA_COMMANDS env var:
#   EXTRA_COMMANDS=nmap,tcpdump python agent.py
ALLOWED_COMMANDS = {
    # System info
    "uname", "hostname", "uptime", "date", "whoami", "id", "w", "who",
    "arch", "nproc", "lscpu", "lsmem", "lsblk", "lspci", "lsusb", "lsmod",
    "dmidecode", "hostnamectl", "timedatectl", "localectl",
    # Files and directories (read-only)
    "ls", "cat", "head", "tail", "wc", "file", "stat", "find",
    "tree", "diff", "sort", "uniq", "cut", "tr", "awk", "sed",
    "tee", "less", "more", "realpath", "basename", "dirname",
    # Search
    "grep", "egrep", "fgrep", "rg", "locate", "which", "whereis", "type",
    # Disk and filesystem
    "df", "du", "mount", "findmnt", "blkid", "tune2fs", "lsof",
    # Process and performance
    "ps", "top", "htop", "free", "vmstat", "iostat", "mpstat",
    "sar", "pidof", "pgrep", "strace", "ltrace",
    # Network (read-only)
    "ip", "ss", "netstat", "ping", "ping6", "traceroute", "tracepath",
    "dig", "nslookup", "host", "curl", "wget",
    "arp", "route", "mtr", "ethtool", "ifconfig", "nmcli",
    "iptables", "ip6tables", "nft", "firewall-cmd",
    "tc", "brctl", "bridge", "resolvectl",
    # Logs and journal
    "journalctl", "dmesg", "last", "lastb", "lastlog", "ausearch",
    # Packages (query only — actual updates go through update_packages tool)
    "apt", "apt-cache", "dpkg", "rpm", "dnf", "yum", "snap", "flatpak",
    "pip", "pip3",
    # Systemd (read-only — write actions go through dedicated tools)
    "systemctl",
    # Security and audit
    "getent", "groups", "passwd", "chage", "faillock",
    "openssl", "ssh-keygen", "gpg",
    "aa-status", "getenforce", "sestatus", "ausearch", "aureport",
    # Hardware and kernel
    "modinfo", "sysctl", "zcat", "xz",
    # Containers
    "docker", "podman", "crictl", "kubectl",
    # Misc sysadmin
    "env", "printenv", "set", "locale",
    "crontab", "at", "systemd-analyze",
    "iconv", "od", "xxd", "hexdump", "strings",
    "md5sum", "sha256sum", "sha1sum",
}

# Extend the command allowlist at runtime without editing this file:
#   EXTRA_COMMANDS=nmap,tcpdump python agent.py
_extra_commands = os.environ.get("EXTRA_COMMANDS", "")
if _extra_commands:
    ALLOWED_COMMANDS |= {c.strip() for c in _extra_commands.split(",") if c.strip()}

# Arguments that are NEVER allowed in any tool
# NOTE: This is defense-in-depth backing the allowlist above.
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


def check_command_allowlist(command: str) -> str | None:
    """Check if a command's binary is in ALLOWED_COMMANDS.

    Extracts the first word (binary name) from the command string,
    strips any path prefix (e.g. /usr/bin/ls -> ls), and checks
    against the allowlist. Also rejects command substitution syntax
    ($(...) and backticks) to prevent embedding denied commands.

    Returns an error message string if denied, or None if allowed.
    """
    stripped = command.strip()
    if not stripped:
        return "[DENIED] Empty command."

    # Reject command substitution — these can embed arbitrary commands
    # that bypass the allowlist check on the outer command.
    if "$(" in stripped or "`" in stripped:
        return "[DENIED] Command substitution ($() and backticks) is not allowed."

    # Reject process substitution <() and >()
    if "<(" in stripped or ">(" in stripped:
        return "[DENIED] Process substitution is not allowed."

    # Reject output redirection — can overwrite arbitrary files.
    # Matches >, >>, 1>, 2>, &>, but not -> (flag args like apt-get -t).
    if re.search(r"(?<![-\w])\d*>{1,2}\s*[^&]|&>", stripped):
        return "[DENIED] Output redirection is not allowed."

    # Extract the first token — handles env vars, sudo prefixes, etc.
    # Walk past leading variable assignments (FOO=bar cmd ...)
    tokens = stripped.split()
    binary = None
    for token in tokens:
        if "=" in token and not token.startswith("-"):
            continue  # skip VAR=value prefixes
        binary = token
        break

    if not binary:
        return "[DENIED] Could not determine command."

    # Strip path prefix: /usr/bin/ls -> ls
    binary = binary.rsplit("/", 1)[-1]

    if binary not in ALLOWED_COMMANDS:
        return (
            f"[DENIED] Command '{binary}' is not in the allowlist.\n"
            f"Set EXTRA_COMMANDS={binary} or edit safety.py ALLOWED_COMMANDS to add it."
        )
    return None


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
            blocked = _check_blocked_patterns(args, kwargs, t.name, audit_logger)
            if blocked:
                return blocked

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
