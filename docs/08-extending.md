# Chapter 8 — Extending: Add Your Own Tool

This chapter covers two ways to add tools:

1. **Plugin directory** (`tools_extra/`) — drop in a `.py` file, no core edits needed. Best for custom or site-specific tools.
2. **Core tool** (in `tools.py`) — for tools that ship with the project.

---

## Option 1: Plugin Directory (`tools_extra/`)

The fastest way to add custom tools. Files in `tools_extra/` are auto-discovered at startup — including files in subfolders.

### How it works

At import time, `tools.py` scans `tools_extra/` recursively for `.py`, `.pyc`, and `.so` files (skipping `_`-prefixed files and directories). Each file is loaded via `importlib`, and any `@tool`-decorated functions are collected and appended to `ALL_TOOLS`. An optional module-level `WRITE_TOOLS` set declares which tools need user confirmation.

When the same module name appears in multiple formats (e.g., both `foo.py` and `foo.pyc`), the loader picks one in priority order: `.py` > `.pyc` > `.so`. Cpython tags in compiled filenames (e.g., `foo.cpython-311-x86_64-linux-gnu.so`, `foo.cpython-311.pyc`) are stripped — both register as module `foo`.

### Flat layout (simple)

```python
# tools_extra/docker_tools.py
from langchain_core.tools import tool
from tools import run_cmd

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

@tool
def check_docker_images() -> str:
    """List locally available Docker images."""
    return run_cmd(["docker", "images"])

# Optional: declare write tools that need user confirmation
WRITE_TOOLS = set()
```

### Categorized layout (subfolders)

For larger plugin collections, organize by category:

```
tools_extra/
├── threat_intel.py              # flat — works as before
├── network/
│   ├── scanner.py               # auto-discovered
│   └── dns_tools.py             # auto-discovered
├── monitoring/
│   ├── prometheus.py
│   └── grafana.py
└── _templates/                  # skipped (starts with _)
    └── example.py
```

Each subfolder is just for organization — tools from all levels are merged into a single flat list for the agent. There's no functional difference between `tools_extra/scanner.py` and `tools_extra/network/scanner.py`.

Subfolders starting with `_` are skipped entirely (useful for templates or work-in-progress tools).

### Plugin rules

- Files must be `.py`, `.pyc`, or `.so` and not start with `_` (the `_example.py` template is skipped)
- Directories starting with `_` are skipped entirely
- Subfolders are scanned recursively — organize by category as needed
- Each `@tool` function is auto-registered — no need to edit `ALL_TOOLS`
- Import `run_cmd` from `tools` for subprocess execution
- Declare `WRITE_TOOLS = {"tool_name"}` for tools that modify system state
- Declare `REQUIRED_ENV = {"API_KEY"}` to skip the plugin when the env var is not set (source `.py` only — see "Distributing compiled plugins" below)
- Declare `REQUIRED_BINS = {"nmap"}` to skip the plugin when a binary is not installed (source `.py` only)
- Errors in a plugin file are warned but don't crash startup
- Plugins load in sorted module-name order

### Conditional loading (REQUIRED_ENV / REQUIRED_BINS)

Plugins can declare dependencies that are checked *before* the file is imported. If a requirement is not met, the plugin is silently skipped — no import errors, no wasted context tokens.

```python
# tools_extra/my_api_tool.py
from langchain_core.tools import tool

@tool
def my_api_lookup(query: str) -> str:
    """Look up something on MyAPI."""
    ...

# Only load this plugin if the API key is set
REQUIRED_ENV = {"MY_API_KEY"}
WRITE_TOOLS = set()
```

```python
# tools_extra/nmap_tools.py
from langchain_core.tools import tool
from tools import run_cmd

@tool
def custom_scan(target: str) -> str:
    """Run a custom nmap scan."""
    return run_cmd(["nmap", "-sV", target])

# Only load if nmap is installed
REQUIRED_BINS = {"nmap"}
WRITE_TOOLS = set()
```

You can combine both — the plugin loads only if all env vars are set AND all binaries are in PATH:

```python
REQUIRED_ENV = {"SHODAN_API_KEY"}
REQUIRED_BINS = {"nmap"}
```

This is important for keeping the tool count manageable. Each tool's name, docstring, and parameter schema are sent to the LLM on every call. Small models (e.g. llama3.1:8b with 8K context) can be overwhelmed if too many tools load. With conditional loading, only tools the user can actually use are registered.

### Startup output

When plugins are loaded you'll see:

```
Loaded 2 extra tool(s): check_docker_containers, check_docker_images
Skipped 3 plugin(s): my_api.py (missing env: MY_API_KEY), nmap_tools.py (missing binaries: nmap), ...
```

If a plugin has a syntax error:

```
[WARNING] Failed to load plugin bad_plugin.py: SyntaxError(...)
```

### Write tools in plugins

If your plugin has tools that modify system state, declare them in a module-level set. A single file can contain any mix of read and write tools — just list the write ones by name:

```python
# tools_extra/docker_management.py
from langchain_core.tools import tool
from tools import run_cmd

@tool
def check_docker_containers(all: bool = False) -> str:
    """List Docker containers."""
    cmd = ["docker", "ps"]
    if all:
        cmd.append("-a")
    return run_cmd(cmd)

@tool
def restart_container(name: str) -> str:
    """Restart a Docker container. REQUIRES CONFIRMATION."""
    return run_cmd(["docker", "restart", name])

@tool
def stop_container(name: str) -> str:
    """Stop a Docker container. REQUIRES CONFIRMATION."""
    return run_cmd(["docker", "stop", name])

# Read tools (check_docker_containers) are auto-allowed.
# Write tools listed here get a confirmation prompt.
WRITE_TOOLS = {"restart_container", "stop_container"}
```

The agent will prompt `Allow this action? [y/N]` before executing any tool listed in `WRITE_TOOLS`, just like the core write tools. Tools not in the set are treated as read-only. If the write tool manages systemd services, those services still need to be in `ALLOWED_SERVICES` (in `safety.py` or via `EXTRA_SERVICES` env var) and in the sudoers file.

### Distributing compiled plugins

If you want to ship a plugin without source — for IP protection, license enforcement, or to discourage casual modification — the loader accepts three formats:

| Format | How to produce | Pros | Cons |
|--------|----------------|------|------|
| `.py` (source) | Just write Python | Easiest; supports `REQUIRED_ENV` / `REQUIRED_BINS` gates | Source is readable |
| `.pyc` (bytecode) | `python -m compileall plugin.py` then ship `__pycache__/plugin.cpython-XYZ.pyc` as `plugin.pyc` | No source, no extra deps | Tied to Python version (magic-number mismatch fails to load); trivially decompilable |
| `.so` (compiled) | Build with [Cython](https://cython.org/) or [Nuitka](https://nuitka.net/) | Hardest to reverse | Per-Python-version + per-platform builds; loses easy-edit |
| `.py` + PyArmor | Wrap with [PyArmor](https://pyarmor.readthedocs.io/) | Encryption, expiration, machine binding; loads as a normal `.py` | Commercial license for advanced features; ship the `pyarmor_runtime_*` package alongside |

**Important:** `REQUIRED_ENV` and `REQUIRED_BINS` are read by parsing the source file before import. They are ignored for `.pyc` and `.so` files. For compiled plugins, check the requirements inside each tool function and return a clear error message instead — the plugin will still load, but tools that depend on missing config will fail gracefully when called.

(A `.py` shim with the same module name as the compiled file won't work as a workaround — the loader picks one format per module name, and `.py` wins, so the shim would replace the compiled module rather than load alongside it.)

**Tip for `.pyc`:** When `python -m compileall foo.py` produces `__pycache__/foo.cpython-311.pyc`, you can either:
- Drop it directly into `tools_extra/` keeping the cpython tag (`foo.cpython-311.pyc`) — the loader strips the tag
- Rename it to `foo.pyc` for cleanliness

Both register as module `foo`. Same applies to `.so` files with cpython/platform tags.

### Plugin security model

Plugin tools go through the same safety layer as core tools. Here's what was verified and why each potential concern is a false positive:

| Concern | Status | Reasoning |
|---------|--------|-----------|
| **Plugin tools bypass safety wrapping** | Safe | All plugins are appended to `ALL_TOOLS` before `safety.wrap_tools()` runs. Every plugin tool gets `_wrap_read_tool` (blocked pattern check) or `_wrap_write_tool` (blocked pattern check + confirmation prompt), identical to core tools. |
| **Plugin removes entries from `WRITE_TOOLS`** | Safe | The merge is additive (set union). Plugins can only add write-tool declarations, never remove existing ones. |
| **Plugin shadows a core tool name** | Safe | Both versions end up in `ALL_TOOLS` and both get safety-wrapped. This may cause LangChain to see duplicate tool names (a correctness issue), but neither copy bypasses the safety layer. |
| **Arbitrary code at import time** | By design | `exec_module()` runs all module-level code in the plugin, not just `@tool` functions. This is inherent to any plugin system. A plugin author has the same trust level as someone editing `tools.py` directly — if they can write to `tools_extra/`, they can write to the core files too. |
| **Symlinks pointing outside `tools_extra/`** | By design | `glob("*.py")` follows symlinks. However, anyone who can create symlinks in `tools_extra/` can also create `.py` files directly — same trust boundary. |

**Key invariant:** `safety.wrap_tools()` runs after all plugins are loaded and after `EXTRA_WRITE_TOOLS` is merged. No plugin tool reaches the agent unwrapped.

**Trust boundary:** Plugin files are trusted the same way core source files are. Treat `tools_extra/` with the same file permissions as the rest of the application. On a production install (via `install.sh`), the directory is owned by the `sysadmin-copilot` user and not world-writable.

---

## Option 2: Core Tool (in `tools.py`)

For tools that ship with the project. The rest of this chapter walks through this approach.

---

## Before You Start: Try the Command Manually

Always verify your command works before wrapping it. SSH into your server and run:

```bash
swapon --show
cat /proc/swaps
free -h | grep -i swap
```

Pick the output you want the agent to see. For swap, `swapon --show` gives structured output that's easy to read:

```
NAME      TYPE SIZE USED PRIO
/swapfile file   2G 512M   -2
```

`free -h` gives a summary line. Let's use both.

---

## Step 1: Write the `@tool` Function

Open `tools.py`. Find a natural place — swap is related to memory, so add it after `check_memory` (line 171).

```python
@tool
def check_swap_usage() -> str:
    """Check swap space usage and activity.

    Shows swap partitions/files, how much is used, and swap-in/out rates
    from /proc/vmstat to indicate swap pressure.
    """
    parts = []
    parts.append("=== Swap Devices ===")
    parts.append(run_cmd(["swapon", "--show"]))
    parts.append("\n=== Swap Summary ===")
    parts.append(run_cmd(["bash", "-c", "free -h | grep -i swap"]))
    parts.append("\n=== Swap Activity (since boot) ===")
    parts.append(run_cmd(["bash", "-c",
        "awk '/pswpin|pswpout/ {print $1\": \"$2}' /proc/vmstat"
    ]))
    return "\n".join(parts)
```

**Docstring quality matters.** The LLM reads it to decide whether this is the right tool. Include:
- What the tool does
- What it's useful for (detecting swap pressure, not just swap size)
- Any notable parameters (none here, but mention them if they exist)

---

## Step 2: Add to `ALL_TOOLS`

At the bottom of `tools.py`, add `check_swap_usage` to `ALL_TOOLS`:

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
    check_swap_usage,        # ← add here
    check_cpu_and_load,
    check_top_processes,
    find_zombie_processes,

    # ... rest of the list
]
```

That's all `tools.py` needs. The safety layer will automatically treat it as a READ tool (since it's not in `WRITE_TOOLS`), and the audit logger will log every call.

---

## Step 3: Verify It Works

Run the copilot and ask about swap:

```
❯ How much swap am I using?

  [check_swap_usage]

You have a 2 GB swap file (/swapfile) with 512 MB currently in use (25%).
The vmstat counters show 12,400 pages swapped in and 8,200 pages swapped
out since boot — moderate swap activity, but not alarming for a busy server.
```

Also type `tools` to confirm the new tool appears:

```
❯ tools

  check_swap_usage          Check swap space usage and activity.
```

---

## Step 4 (Optional): Adding a Write Tool

If your new tool modifies system state (writes a file, changes a config, restarts something), it needs two extra steps.

**Example:** a hypothetical `clear_journal_logs` tool that runs `journalctl --vacuum-size=500M`.

### 4a: Add to `WRITE_TOOLS` in `safety.py`

```python
WRITE_TOOLS = {
    "restart_service",
    "stop_service",
    "clear_journal_logs",   # ← add here
}
```

### 4b: If it manages a service, add to `ALLOWED_SERVICES`

For `clear_journal_logs`, there's no service name involved, so no allowlist change is needed. But if you wrote a `reload_service` tool that calls `sudo systemctl reload <service>`, you'd add the target services to `ALLOWED_SERVICES`.

The `_wrap_write_tool()` function looks for a `service` keyword argument by name. If your write tool has a different parameter name, you'd need to adjust the wrapper logic.

### 4c: Update sudoers if needed

If the tool calls `sudo`, run:

```bash
sudo bash sync-sudoers.sh
```

Or manually edit `/etc/sudoers.d/sysadmin-copilot` with `visudo`.

---

## Step 5: Full Worked Example (Read Tool with Parameters)

Here's a more complete example: a tool to check temperature sensors, with an optional filter.

```python
@tool
def check_temperatures(sensor: Optional[str] = None) -> str:
    """Check hardware temperatures from sensors.

    Useful for diagnosing thermal throttling or hardware issues.

    Args:
        sensor: Optional sensor name to filter (e.g. 'coretemp', 'acpitz').
                If not provided, shows all sensors.
    """
    if sensor:
        import shlex
        cmd = f"sensors {shlex.quote(sensor)}"
        return run_cmd(["bash", "-c", cmd])
    return run_cmd(["sensors"])
```

Then in `ALL_TOOLS`:

```python
    check_temperatures,
```

Note:
- `Optional[str] = None` lets the LLM omit the parameter
- `shlex.quote()` is used when the parameter goes into a shell string
- The docstring's `Args:` section tells the LLM what values make sense

---

## Summary: Adding a Tool

### Plugin (custom/site-specific)

Drop a `.py` file in `tools_extra/`. No core files need editing. Declare write tools via `WRITE_TOOLS = {"name"}`.

### Core tool

| File | What to do |
|------|-----------|
| `tools.py` | 1. Write the `@tool` function with docstring and type annotations. 2. Add to `ALL_TOOLS`. |
| `safety.py` | Only if it's a write tool: add to `WRITE_TOOLS`. Add new services to `ALLOWED_SERVICES`. |
| `install.sh` / sudoers | Only if it calls `sudo`: run `sync-sudoers.sh` to regenerate `/etc/sudoers.d/sysadmin-copilot`. |

READ tools (status checks, log queries, health checks) only require step 1 in `tools.py`.

---

## Quick Reference: `run_cmd()` Patterns

| Scenario | Pattern |
|----------|---------|
| Simple command, no user input | `run_cmd(["command", "arg1", "arg2"])` |
| Command with user-provided arg | `run_cmd(["command", user_arg])` — pass as list element, not interpolated |
| Shell pipeline needed | `run_cmd(["bash", "-c", f"cmd1 {shlex.quote(arg)} \| cmd2"])` |
| Longer timeout needed | `run_cmd(["ping", "-c", "10", host], timeout=60)` |

---

*That's the book. You now know how every line of Sysadmin Copilot works, why it was written that way, and how to extend it.*

---

[← Back to Table of Contents](README.md)
