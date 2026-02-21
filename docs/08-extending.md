# Chapter 8 — Extending: Add Your Own Tool

This chapter walks through adding a new tool from scratch. By the end, you'll have a working `check_swap_usage` tool that the agent can call to get detailed swap statistics.

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

Open `tools.py`. Find a natural place — swap is related to memory, so add it after `check_memory` (line 170).

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

## Summary: The Three-File Checklist

When adding any new tool, touch these files:

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
