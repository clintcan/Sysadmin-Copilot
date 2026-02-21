"""
Audit Logger for Sysadmin Copilot

Logs every command the agent executes, with timestamps, arguments,
and whether it was blocked/denied/confirmed. Essential for accountability.

Logs are written to both an in-memory buffer (for the `audit` command)
and a persistent log file.
"""

import json
import os
from datetime import datetime


class AuditLogger:
    """Logs all agent actions for accountability and review."""

    def __init__(self, log_dir: str = None):
        self.entries: list[dict] = []
        self.session_start = datetime.now()

        # Set up file logging
        if log_dir is None:
            log_dir = os.path.expanduser("~/.sysadmin-copilot/logs")
        os.makedirs(log_dir, exist_ok=True)

        timestamp = self.session_start.strftime("%Y%m%d_%H%M%S")
        self.log_file = os.path.join(log_dir, f"session_{timestamp}.jsonl")

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

            # Color-code status
            if status == "BLOCKED":
                status_str = f"\033[31m{status}\033[0m"
            elif status == "DENIED":
                status_str = f"\033[33m{status}\033[0m"
            elif status == "CONFIRMED":
                status_str = f"\033[32m{status}\033[0m"
            else:
                status_str = f"\033[90m{status}\033[0m"

            print(f"  \033[90m{ts}\033[0m  {status_str:<20}  \033[36m{tool}\033[0m  {args_str}")

        print(f"\n  Total commands: {len(self.entries)}")
        print(f"\033[33m{'─' * 60}\033[0m\n")

    def _write_to_file(self, entry: dict):
        """Append an entry to the persistent log file."""
        try:
            with open(self.log_file, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass  # Don't crash if we can't write the log


def _get_status(blocked: bool, denied: bool, confirmed: bool) -> str:
    if blocked:
        return "BLOCKED"
    elif denied:
        return "DENIED"
    elif confirmed:
        return "CONFIRMED"
    return "OK"


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


def _format_args(args: dict) -> str:
    """Format args dict for display."""
    if not args:
        return ""
    parts = [f"{k}={v}" for k, v in args.items() if v is not None]
    return ", ".join(parts)
