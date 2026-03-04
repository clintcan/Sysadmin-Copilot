# Chapter 4 — Tools (`tools.py`)

## What Is a LangChain Tool?

A LangChain tool is a Python function decorated with `@tool`. When the agent decides to call a tool, LangChain:

1. Reads the **function name** — that's the tool's identifier
2. Reads the **docstring** — that's what the LLM reads to decide *when* to use it
3. Reads the **type annotations** — that's how it validates and formats arguments
4. Calls the function with the arguments the LLM provided
5. Returns the string result back into the conversation

The docstring is not just documentation — it's part of the prompt. Write it as if you're explaining the tool to someone who has to decide whether to use it.

---

## A Minimal Tool

`check_memory` (`tools.py:169–172`) is the simplest tool in the codebase:

```python
@tool
def check_memory() -> str:
    """Check memory usage (RAM and swap) in human-readable format."""
    return run_cmd(["free", "-h"])
```

Four lines. No parameters, no logic. The docstring is the agent's guide. `run_cmd()` handles the subprocess call. That's all it needs.

---

## The `run_cmd()` Helper

Every tool calls `run_cmd()`. Here's the full implementation (`tools.py:37–59`):

```python
def run_cmd(cmd: list[str], timeout: int = 30) -> str:
    """Run a command and return stdout. Returns stderr on failure."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout.strip()
        if result.returncode != 0 and result.stderr:
            output += f"\n[STDERR]: {result.stderr.strip()}"
        if len(output) > MAX_OUTPUT_CHARS:
            overflow = len(output) - MAX_OUTPUT_CHARS
            suffix = f"\n[... {overflow} chars truncated]"
            output = output[:MAX_OUTPUT_CHARS - len(suffix)] + suffix
        return output if output else "(no output)"
    except subprocess.TimeoutExpired:
        return f"[ERROR] Command timed out after {timeout}s"
    except FileNotFoundError:
        return f"[ERROR] Command not found: {cmd[0]}"
    except Exception as e:
        return f"[ERROR] {e}"
```

Key decisions:

**List-based subprocess call** — `subprocess.run(cmd, ...)` where `cmd` is a `list[str]` avoids shell injection entirely. There's no shell involved; arguments are passed directly to the kernel. Compare this to `subprocess.run("free -h", shell=True)` — the shell=True form would let malicious input escape the argument.

**Capture both stdout and stderr** — `capture_output=True` captures both. If the command fails (`returncode != 0`), stderr is appended to the output so the LLM can see what went wrong.

**Hard timeout** — 30 seconds by default. Prevents the agent from hanging indefinitely on a slow command like `ping` or a stalled process.

**Output truncation** (`tools.py:49–52`):

```python
        if len(output) > MAX_OUTPUT_CHARS:
            overflow = len(output) - MAX_OUTPUT_CHARS
            suffix = f"\n[... {overflow} chars truncated]"
            output = output[:MAX_OUTPUT_CHARS - len(suffix)] + suffix
```

`MAX_OUTPUT_CHARS = 8000`. This matters because every tool output is sent back to the LLM as part of the conversation. Very long outputs (a verbose `journalctl` dump, a full process list) would fill the context window and degrade response quality. The truncation math accounts for the suffix length so the final string is never longer than `MAX_OUTPUT_CHARS`.

---

## A Multi-Parameter Tool

`query_journal_logs` (`tools.py:66–94`) shows how to handle optional parameters:

```python
@tool
def query_journal_logs(
    unit: Optional[str] = None,
    priority: Optional[str] = None,
    since: Optional[str] = None,
    lines: int = 50,
    grep: Optional[str] = None,
) -> str:
    """Query systemd journal logs with filters.

    Args:
        unit: Service unit name (e.g. 'nginx', 'sshd', 'docker').
        priority: Minimum priority: emerg, alert, crit, err, warning, notice, info, debug.
        since: Time filter like '1 hour ago', '30 min ago', 'today', '2024-01-15'.
        lines: Max number of log lines to return (default 50).
        grep: Optional text pattern to filter log lines.
    """
    cmd = ["journalctl", "--no-pager", "-n", str(lines)]

    if unit:
        cmd += ["-u", unit]
    if priority:
        cmd += ["-p", priority]
    if since:
        cmd += ["--since", since]
    if grep:
        cmd += ["-g", grep]

    return run_cmd(cmd)
```

The docstring's `Args:` section is important — the LLM reads these descriptions to know what values to fill in. When a user asks "show me nginx errors from the last hour", the LLM maps:
- `unit` → `"nginx"`
- `priority` → `"err"`
- `since` → `"1 hour ago"`

All parameters are `Optional` with defaults, so the LLM can omit any of them. The function builds the `journalctl` command incrementally, only adding flags that have values.

---

## Security: Shell Injection

Some tools genuinely need a shell pipeline — two commands connected with `|`. For those, `read_log_file` shows the safe pattern (`tools.py:116–121`):

```python
    if grep:
        # grep then tail
        cmd = f"grep -i {shlex.quote(grep)} {shlex.quote(path)} | tail -n {lines}"
        return run_cmd(["bash", "-c", cmd])
    else:
        return run_cmd(["tail", "-n", str(lines), path])
```

The **dangerous pattern** would be:

```python
# DO NOT DO THIS
cmd = f"grep -i {grep} {path} | tail -n {lines}"
run_cmd(["bash", "-c", cmd])
```

If `grep` were `"error; rm -rf /"`, the shell would execute `rm -rf /`. This is a classic command injection vulnerability.

The **safe pattern** uses `shlex.quote()` to shell-escape both values:

```python
grep_q = shlex.quote(grep)    # 'error; rm -rf /' becomes "'error; rm -rf /'"
path_q = shlex.quote(path)    # the semicolon and slashes are neutralised
```

`shlex.quote()` wraps the string in single quotes and escapes any embedded single quotes, making it impossible to break out of the argument context.

---

## Configurable Log Paths

`read_log_file` only reads files under approved directories (`tools.py:29–34`, `113–114`):

```python
_log_paths_env = os.environ.get("LOG_PATHS", "")
ALLOWED_LOG_PATHS: tuple = (
    tuple(p.strip() for p in _log_paths_env.split(",") if p.strip())
    if _log_paths_env
    else ("/var/log",)
)
```

```python
    if not any(path.startswith(p) for p in ALLOWED_LOG_PATHS):
        return f"[DENIED] Can only read files under: {', '.join(ALLOWED_LOG_PATHS)}"
```

Default: `/var/log` only. To add more paths:

```bash
LOG_PATHS=/var/log,/run/log,/home/myapp/logs python agent.py
```

This prevents the LLM from being convinced to read `/etc/shadow`, `/root/.ssh/id_rsa`, or other sensitive files through `read_log_file`.

---

## The Tool Registry

All tools are collected at the bottom of `tools.py`:

```python
ALL_TOOLS = [
    # Log analysis
    query_journal_logs,
    read_log_file,
    check_dmesg,

    # System health
    check_disk_usage,
    check_directory_size,
    check_memory,
    check_cpu_and_load,
    check_top_processes,
    find_zombie_processes,

    # Service management
    check_service_status,
    list_failed_services,
    restart_service,
    stop_service,

    # Network
    check_open_ports,
    check_network_connections,
    ping_host,
    dns_lookup,
    check_url_health,

    # Users & files
    check_logged_in_users,
    check_cron_jobs,
    find_recent_files,

    # Security audit
    system_audit,
    check_outdated_packages,
    update_packages,

    # General purpose
    change_directory,
    run_command,
    search_web,
]
```

`agent.py` imports `ALL_TOOLS` and passes it to `safety.wrap_tools()`. Adding a new tool is as simple as defining it in `tools.py` and appending it to this list. See Chapter 8 for a full walkthrough.

---

## The General-Purpose Escape Hatch

The 24 specific tools cover common sysadmin tasks, but investigations often need follow-up commands that no dedicated tool anticipates — reading a `/proc` entry, checking a config file, or running `ip route show`.

`run_command` (`tools.py:815–832`) fills that gap:

```python
@tool
def run_command(command: str) -> str:
    """Run a shell command for ad-hoc investigation when no specific tool fits."""
    return run_cmd(["bash", "-c", command])
```

It passes the command string through `bash -c`, reusing the same `run_cmd()` helper — 30-second timeout, 8000 char output cap, and error handling all apply.

**Safety**: because `run_command` is not in `WRITE_TOOLS`, it gets wrapped by `_wrap_read_tool()`. The wrapper scans the `command` string against `BLOCKED_PATTERNS` before execution, so attempts to run destructive commands (`rm`, `dd`, `shutdown`, `reboot`, `shred`, `truncate`, `mkfs`, etc.), encoding evasion (`base64 -d | sh`), and dangerous permission changes (`chmod 777`) are blocked. No `sudo` is used, so OS-level permissions further constrain what can run. See [Chapter 5 — Threat Model](05-safety-layer.md#threat-model-and-limitations) for the full coverage table and known limitations.

The system prompt tells the agent to prefer `run_command` over suggesting commands for the user to run manually.

---

Next: [Chapter 5 — Safety Layer](05-safety-layer.md)
