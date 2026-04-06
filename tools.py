"""
Sysadmin Copilot Tools

Each tool wraps one or more Linux CLI commands. The agent selects the right
tool based on the user's question, runs it, and interprets the output.

To add a new tool:
  1. Write a function decorated with @tool
  2. Add a clear docstring — the agent reads it to decide when to use it
  3. Add it to ALL_TOOLS at the bottom
"""

import importlib.util
import os
import shlex
import subprocess
from pathlib import Path
from typing import Optional

from langchain_core.tools import BaseTool, tool


# ─── Helpers ──────────────────────────────────────────────────────────────────

MAX_OUTPUT_CHARS = 8000

# Allowed prefixes for read_log_file — extend via LOG_PATHS env var
# Example: LOG_PATHS=/var/log,/run/log,/home/app/logs
_log_paths_env = os.environ.get("LOG_PATHS", "")
ALLOWED_LOG_PATHS: tuple = (
    tuple(p.strip() for p in _log_paths_env.split(",") if p.strip())
    if _log_paths_env
    else ("/var/log",)
)


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


# ═══════════════════════════════════════════════════════════════════════════════
# LOG ANALYSIS TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

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


@tool
def read_log_file(
    path: str = "/var/log/syslog",
    lines: int = 50,
    grep: Optional[str] = None,
) -> str:
    """Read lines from a log file on disk.

    Args:
        path: Path to the log file. Common paths:
              /var/log/syslog, /var/log/auth.log, /var/log/kern.log,
              /var/log/dpkg.log, /var/log/apt/history.log
        lines: Number of lines from the end (default 50).
        grep: Optional pattern to filter lines.
    """
    # Security: only allow reading from approved log directories
    if not any(path.startswith(p) for p in ALLOWED_LOG_PATHS):
        return f"[DENIED] Can only read files under: {', '.join(ALLOWED_LOG_PATHS)}"

    if grep:
        # grep then tail
        cmd = f"grep -i {shlex.quote(grep)} {shlex.quote(path)} | tail -n {lines}"
        return run_cmd(["bash", "-c", cmd])
    else:
        return run_cmd(["tail", "-n", str(lines), path])


@tool
def check_dmesg(lines: int = 30, level: Optional[str] = None) -> str:
    """Check kernel ring buffer (dmesg) for hardware/driver messages.

    Args:
        lines: Number of recent lines to show (default 30).
        level: Filter by level: emerg, alert, crit, err, warn, notice, info, debug.
    """
    cmd = "dmesg --time-format=reltime -T"
    if level:
        cmd += f" -l {shlex.quote(level)}"
    cmd += f" | tail -n {lines}"
    return run_cmd(["bash", "-c", cmd])


# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM HEALTH TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def check_disk_usage(path: str = "/") -> str:
    """Check disk usage for mounted filesystems.

    Args:
        path: Specific mount point to check, or '/' for all filesystems.
    """
    if path == "/":
        return run_cmd(["df", "-h", "--type=ext4", "--type=xfs", "--type=btrfs",
                        "--type=tmpfs", "--type=overlay"])
    return run_cmd(["df", "-h", path])


@tool
def check_directory_size(path: str) -> str:
    """Check the size of a directory and its largest subdirectories.

    Args:
        path: Directory path to check (e.g. '/var/log', '/home', '/tmp').
    """
    # Top 10 largest subdirectories
    p = shlex.quote(path)
    cmd = f"du -sh {p} 2>/dev/null && echo '---' && du -h --max-depth=1 {p} 2>/dev/null | sort -rh | head -10"
    return run_cmd(["bash", "-c", cmd])


@tool
def check_memory() -> str:
    """Check memory usage (RAM and swap) in human-readable format."""
    return run_cmd(["free", "-h"])


@tool
def check_cpu_and_load() -> str:
    """Check CPU info, load averages, and uptime."""
    parts = []
    parts.append("=== Uptime & Load ===")
    parts.append(run_cmd(["uptime"]))
    parts.append("\n=== CPU Info ===")
    parts.append(run_cmd(["bash", "-c", "lscpu | grep -E 'Model name|CPU\\(s\\)|Thread|Core'"]))
    return "\n".join(parts)


@tool
def check_top_processes(count: int = 10, sort_by: str = "cpu") -> str:
    """Show top processes by CPU or memory usage.

    Args:
        count: Number of processes to show (default 10).
        sort_by: Sort by 'cpu' or 'memory'.
    """
    if sort_by == "memory":
        cmd = f"ps aux --sort=-%mem | head -n {count + 1}"
    else:
        cmd = f"ps aux --sort=-%cpu | head -n {count + 1}"

    return run_cmd(["bash", "-c", cmd])


@tool
def find_zombie_processes() -> str:
    """Find zombie (defunct) processes on the system."""
    output = run_cmd(["bash", "-c", "ps aux | grep -w Z | grep -v grep"])
    if "(no output)" in output or not output.strip():
        return "No zombie processes found."
    return output


# ═══════════════════════════════════════════════════════════════════════════════
# SERVICE MANAGEMENT TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def check_service_status(service: str) -> str:
    """Check the status of a systemd service.

    Args:
        service: Service name (e.g. 'nginx', 'sshd', 'docker', 'postgresql').
    """
    return run_cmd(["systemctl", "status", service, "--no-pager", "-l"])


@tool
def list_failed_services() -> str:
    """List all failed systemd services."""
    return run_cmd(["systemctl", "list-units", "--state=failed", "--no-pager"])


@tool
def restart_service(service: str) -> str:
    """Restart a systemd service. REQUIRES CONFIRMATION.

    This is a WRITE action. The safety layer will prompt the user
    for confirmation before executing.

    Args:
        service: Service name to restart (e.g. 'nginx', 'sshd').
    """
    result = run_cmd(["sudo", "systemctl", "restart", service])
    # Also get the new status
    status = run_cmd(["systemctl", "is-active", service])
    return f"Restart result: {result}\nCurrent status: {status}"


@tool
def stop_service(service: str) -> str:
    """Stop a systemd service. REQUIRES CONFIRMATION.

    This is a WRITE action. The safety layer will prompt the user
    for confirmation before executing.

    Args:
        service: Service name to stop.
    """
    result = run_cmd(["sudo", "systemctl", "stop", service])
    status = run_cmd(["systemctl", "is-active", service])
    return f"Stop result: {result}\nCurrent status: {status}"


# ═══════════════════════════════════════════════════════════════════════════════
# NETWORK TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def check_open_ports() -> str:
    """Show open/listening ports on THIS machine (local only).

    Lists ports that local services are listening on, with process names.
    This does NOT scan remote hosts — use nmap_scan for that.
    """
    return run_cmd(["ss", "-tulnp"])


@tool
def check_network_connections(state: str = "established") -> str:
    """Show network connections, optionally filtered by state.

    Args:
        state: Connection state filter: 'established', 'listening', 'time-wait', 'all'.
    """
    if state == "all":
        return run_cmd(["ss", "-tunap"])
    return run_cmd(["ss", "-tunap", f"state", state])


@tool
def ping_host(host: str, count: int = 4) -> str:
    """Ping a host to check connectivity.

    Args:
        host: Hostname or IP to ping.
        count: Number of pings (default 4).
    """
    return run_cmd(["ping", "-c", str(count), "-W", "3", host], timeout=20)


@tool
def dns_lookup(domain: str) -> str:
    """Perform a DNS lookup for a domain.

    Args:
        domain: Domain name to look up (e.g. 'google.com').
    """
    return run_cmd(["dig", "+short", domain])


@tool
def check_url_health(url: str) -> str:
    """Check if a URL is responding and show HTTP status code.

    Args:
        url: Full URL to check (e.g. 'http://localhost:80', 'https://example.com').
    """
    return run_cmd([
        "curl", "-o", "/dev/null", "-s", "-w",
        "HTTP Status: %{http_code}\nTime Total: %{time_total}s\nTime Connect: %{time_connect}s\nSize: %{size_download} bytes",
        "-L", "--max-time", "10", url
    ])


# ═══════════════════════════════════════════════════════════════════════════════
# USER & FILE TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def check_logged_in_users() -> str:
    """Show currently logged-in users and recent login activity."""
    parts = []
    parts.append("=== Currently Logged In ===")
    parts.append(run_cmd(["who"]))
    parts.append("\n=== Recent Logins ===")
    parts.append(run_cmd(["last", "-n", "10", "--time-format=short"]))
    return "\n".join(parts)


@tool
def check_cron_jobs(user: Optional[str] = None) -> str:
    """List cron jobs, optionally for a specific user.

    Args:
        user: Username to check crontab for. If None, shows system cron.
    """
    parts = []
    if user:
        parts.append(f"=== Crontab for {user} ===")
        parts.append(run_cmd(["crontab", "-u", user, "-l"]))
    else:
        parts.append("=== System Cron (/etc/crontab) ===")
        parts.append(run_cmd(["cat", "/etc/crontab"]))
        parts.append("\n=== /etc/cron.d (file contents) ===")
        parts.append(run_cmd(["bash", "-c",
            "for f in /etc/cron.d/*; do [ -f \"$f\" ] && echo \"--- $f ---\" && cat \"$f\" && echo; done 2>/dev/null || echo '(empty)'"
        ]))
        for d in ["cron.daily", "cron.hourly"]:
            path = f"/etc/{d}"
            parts.append(f"\n=== {path} (scripts) ===")
            parts.append(run_cmd(["ls", "-1", path]))
    return "\n".join(parts)


@tool
def find_recent_files(
    path: str = "/etc",
    minutes: int = 60,
    count: int = 20,
) -> str:
    """Find recently modified files.

    Args:
        path: Directory to search in.
        minutes: Find files modified within this many minutes.
        count: Max number of files to return.
    """
    cmd = f"find {shlex.quote(path)} -type f -mmin -{minutes} 2>/dev/null | head -n {count}"
    return run_cmd(["bash", "-c", cmd])


# ═══════════════════════════════════════════════════════════════════════════════
# SECURITY AUDIT
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def system_audit(scope: str = "quick") -> str:
    """Run a security audit aligned with common CIS benchmarks.

    Checks SSH hardening, file permissions, user accounts, firewall status,
    kernel parameters, and open ports. All checks are read-only.

    Args:
        scope: 'quick' for essential checks (~10-15s), or 'full' to add
               SUID/SGID scan, world-writable files, running services,
               auto-updates, failed logins, and SELinux/AppArmor (~30s).
    """
    timeout = 60 if scope == "full" else 30
    sections: list[str] = []

    def _run(cmd: str, tm: int = timeout) -> str:
        return run_cmd(["bash", "-c", cmd], timeout=tm)

    def _flag(condition: bool, ok_msg: str, warn_msg: str) -> str:
        return f"[OK] {ok_msg}" if condition else f"[!] {warn_msg}"

    # --- SSH Hardening ---
    ssh_lines: list[str] = []
    sshd_cfg = "/etc/ssh/sshd_config"
    ssh_checks = {
        "PermitRootLogin": ("no", "Root login disabled", "Root login NOT disabled"),
        "PasswordAuthentication": ("no", "Password auth disabled", "Password auth enabled"),
        "X11Forwarding": ("no", "X11 forwarding disabled", "X11 forwarding enabled"),
        "PermitEmptyPasswords": ("no", "Empty passwords denied", "Empty passwords allowed"),
        "Protocol": ("2", "SSH protocol 2", "SSH protocol not set to 2"),
    }
    for key, (expected, ok, warn) in ssh_checks.items():
        val = _run(f"grep -i '^\\s*{key}' {sshd_cfg} 2>/dev/null | tail -1 | awk '{{print $2}}'")
        if val == "(no output)":
            ssh_lines.append(f"[!] {key} not explicitly set (check defaults)")
        else:
            ssh_lines.append(_flag(val.strip().lower() == expected, ok, f"{warn} ({key} = {val.strip()})"))
    sections.append("=== SSH Hardening ===\n" + "\n".join(ssh_lines))

    # --- Sensitive File Permissions ---
    perm_lines: list[str] = []
    perm_checks = {
        "/etc/passwd":  ("644", False),
        "/etc/shadow":  ("640", True),
        "/etc/group":   ("644", False),
        "/etc/sudoers": ("440", True),
    }
    for fpath, (expected, root_only) in perm_checks.items():
        raw = _run(f"stat -c '%a %U:%G' {fpath} 2>/dev/null")
        if raw.startswith("[ERROR]") or raw == "(no output)":
            perm_lines.append(f"[!] Cannot stat {fpath}")
            continue
        parts = raw.split()
        mode = parts[0] if parts else "?"
        owner = parts[1] if len(parts) > 1 else "?"
        ok = (mode == expected)
        if root_only:
            ok = ok and owner.startswith("root:")
        perm_lines.append(_flag(ok, f"{fpath} ({mode} {owner})",
                                f"{fpath} ({mode} {owner}) — expected {expected}"))
    sections.append("=== Sensitive File Permissions ===\n" + "\n".join(perm_lines))

    # --- User Accounts ---
    user_lines: list[str] = []
    uid0 = _run("awk -F: '$3 == 0 {print $1}' /etc/passwd")
    uid0_users = [u for u in uid0.splitlines() if u.strip() and u != "(no output)"]
    if not uid0_users:
        user_lines.append("[!] Could not read /etc/passwd to check UID 0 users")
    else:
        user_lines.append(_flag(uid0_users == ["root"],
                                "Only root has UID 0",
                                f"UID 0 users: {', '.join(uid0_users)}"))
    empty_pw = _run("awk -F: '$2 == \"\" {print $1}' /etc/shadow 2>/dev/null")
    if empty_pw == "(no output)" or not empty_pw.strip():
        user_lines.append("[OK] No users with empty passwords")
    else:
        user_lines.append(f"[!] Users with empty passwords: {empty_pw.strip()}")
    sudo_members = _run("getent group sudo wheel 2>/dev/null | cut -d: -f4")
    user_lines.append(f"sudo/wheel members: {sudo_members.strip() if sudo_members != '(no output)' else 'N/A'}")
    sections.append("=== User Accounts ===\n" + "\n".join(user_lines))

    # --- Firewall Status ---
    fw_lines: list[str] = []
    ufw = _run("ufw status 2>/dev/null")
    if "inactive" in ufw.lower():
        fw_lines.append("[!] UFW is inactive")
    elif "active" in ufw.lower():
        fw_lines.append("[OK] UFW is active")
        fw_lines.append(ufw)
    else:
        ipt = _run("iptables -L -n 2>/dev/null | head -20")
        if "Chain" in ipt:
            fw_lines.append("[OK] iptables rules present")
            fw_lines.append(ipt)
        else:
            fw_lines.append("[!] No firewall detected (ufw/iptables)")
    sections.append("=== Firewall Status ===\n" + "\n".join(fw_lines))

    # --- Kernel Hardening (sysctl) ---
    sysctl_lines: list[str] = []
    sysctl_checks = {
        "net.ipv4.ip_forward":                     ("0", "IP forwarding disabled", "IP forwarding enabled"),
        "net.ipv4.tcp_syncookies":                  ("1", "SYN cookies enabled", "SYN cookies disabled"),
        "kernel.randomize_va_space":                ("2", "ASLR fully enabled", "ASLR not fully enabled"),
        "net.ipv4.conf.all.rp_filter":              ("1", "Reverse path filtering enabled", "Reverse path filtering disabled"),
        "net.ipv4.conf.all.accept_redirects":       ("0", "ICMP redirects rejected", "ICMP redirects accepted"),
        "net.ipv4.conf.all.send_redirects":         ("0", "Send redirects disabled", "Send redirects enabled"),
        "net.ipv4.conf.all.accept_source_route":    ("0", "Source routing disabled", "Source routing enabled"),
    }
    for param, (expected, ok, warn) in sysctl_checks.items():
        val = _run(f"sysctl -n {param} 2>/dev/null").strip()
        if val == "(no output)":
            sysctl_lines.append(f"[!] {param}: N/A")
        else:
            sysctl_lines.append(_flag(val == expected, f"{param} = {val}", f"{warn} ({param} = {val})"))
    sections.append("=== Kernel Hardening ===\n" + "\n".join(sysctl_lines))

    # --- Open Listening Ports ---
    ports = _run("ss -tulnp 2>/dev/null")
    sections.append("=== Open Listening Ports ===\n" + ports)

    # ── Full scope only ──────────────────────────────────────────────────
    if scope == "full":
        # SUID binaries
        suid = _run("find / -xdev -perm -4000 -type f "
                     "-not -path '/proc/*' -not -path '/sys/*' "
                     "-not -path '/dev/*' -not -path '/run/*' "
                     "2>/dev/null | head -20", tm=60)
        sections.append("=== SUID Binaries ===\n" + suid)

        # SGID binaries
        sgid = _run("find / -xdev -perm -2000 -type f "
                     "-not -path '/proc/*' -not -path '/sys/*' "
                     "-not -path '/dev/*' -not -path '/run/*' "
                     "2>/dev/null | head -20", tm=60)
        sections.append("=== SGID Binaries ===\n" + sgid)

        # World-writable files
        ww = _run("find / -xdev -perm -002 -type f "
                   "-not -path '/proc/*' -not -path '/sys/*' "
                   "-not -path '/dev/*' -not -path '/run/*' "
                   "2>/dev/null | head -20", tm=60)
        sections.append("=== World-Writable Files ===\n" + (ww if ww != "(no output)" else "[OK] None found"))

        # Running / enabled services
        enabled_svc = _run("systemctl list-unit-files --type=service --state=enabled --no-pager 2>/dev/null | head -30")
        sections.append("=== Enabled Services ===\n" + enabled_svc)

        # Automatic updates
        auto_lines: list[str] = []
        ua = _run("systemctl is-enabled unattended-upgrades 2>/dev/null")
        if "enabled" in ua.lower():
            auto_lines.append("[OK] unattended-upgrades enabled")
        else:
            auto_lines.append("[!] unattended-upgrades not enabled or not installed")
        sections.append("=== Automatic Updates ===\n" + "\n".join(auto_lines))

        # Failed logins (last 24h)
        failed = _run("journalctl _COMM=sshd --since '24 hours ago' --no-pager 2>/dev/null "
                       "| grep -i 'failed\\|invalid' | tail -15")
        sections.append("=== Failed SSH Logins (24h) ===\n" +
                        (failed if failed != "(no output)" else "[OK] None found"))

        # SELinux / AppArmor
        mac_lines: list[str] = []
        se = _run("getenforce 2>/dev/null")
        if se != "(no output)" and not se.startswith("[ERROR]"):
            mac_lines.append(f"SELinux: {se.strip()}")
        aa = _run("cat /sys/module/apparmor/parameters/enabled 2>/dev/null")
        if aa.strip() == "Y":
            mac_lines.append("[OK] AppArmor enabled")
        elif se == "(no output)" or se.startswith("[ERROR]"):
            mac_lines.append("[!] Neither SELinux nor AppArmor detected")
        sections.append("=== SELinux / AppArmor ===\n" + "\n".join(mac_lines))

    max_chars = 16000  # audit reports need more room than typical tool output
    output = "\n\n".join(sections)
    if len(output) > max_chars:
        overflow = len(output) - max_chars
        suffix = f"\n[... {overflow} chars truncated]"
        output = output[:max_chars - len(suffix)] + suffix
    return output


@tool
def check_outdated_packages() -> str:
    """Check for outdated system packages across all detected package managers.

    Auto-detects which package managers are installed (apt, dnf, yum, snap,
    flatpak) and reports available updates for each. Security updates are
    flagged with [!] where detectable. All checks are read-only — nothing
    is installed or upgraded.
    """
    timeout = 30
    sections: list[str] = []

    def _run(cmd: str, tm: int = timeout) -> str:
        return run_cmd(["bash", "-c", cmd], timeout=tm)

    def _has(binary: str) -> bool:
        return _run(f"which {binary} 2>/dev/null") != "(no output)"

    # --- apt (Debian/Ubuntu) ---
    if _has("apt"):
        apt_lines: list[str] = []
        raw = _run("apt list --upgradable 2>/dev/null | tail -n +2 | head -50")
        if raw == "(no output)" or not raw.strip():
            apt_lines.append("[OK] All packages are up to date")
        else:
            pkgs = [l for l in raw.splitlines() if l.strip()]
            total = len(pkgs)
            # Security updates contain the distro security pocket
            sec_pkgs = [l for l in pkgs if "-security" in l.lower()]
            sec_count = len(sec_pkgs)
            if sec_count:
                apt_lines.append(f"[!] {sec_count} security update(s) available")
            apt_lines.append(f"{total} package(s) can be upgraded:")
            apt_lines.append(raw)
        sections.append("=== apt ===\n" + "\n".join(apt_lines))
    else:
        sections.append("=== apt ===\nN/A (not installed)")

    # --- dnf (Fedora/RHEL 8+) ---
    has_dnf = _has("dnf")
    if has_dnf:
        dnf_lines: list[str] = []
        # dnf check-update exits 100 when updates are available, 0 when none
        raw = _run("dnf check-update 2>/dev/null | tail -n +3 | head -50")
        if raw == "(no output)" or not raw.strip():
            dnf_lines.append("[OK] All packages are up to date")
        else:
            pkgs = [l for l in raw.splitlines() if l.strip()]
            total = len(pkgs)
            dnf_lines.append(f"{total} package(s) can be upgraded:")
            dnf_lines.append(raw)
        # Security updates
        sec_raw = _run("dnf updateinfo list security 2>/dev/null | tail -n +2 | head -50")
        if sec_raw != "(no output)" and sec_raw.strip():
            sec_pkgs = [l for l in sec_raw.splitlines() if l.strip()]
            if sec_pkgs:
                dnf_lines.insert(0, f"[!] {len(sec_pkgs)} security update(s) available")
        sections.append("=== dnf ===\n" + "\n".join(dnf_lines))
    else:
        sections.append("=== dnf ===\nN/A (not installed)")

    # --- yum (RHEL 7/CentOS) — only if dnf is absent ---
    if not has_dnf and _has("yum"):
        yum_lines: list[str] = []
        raw = _run("yum check-update 2>/dev/null | tail -n +3 | head -50")
        if raw == "(no output)" or not raw.strip():
            yum_lines.append("[OK] All packages are up to date")
        else:
            pkgs = [l for l in raw.splitlines() if l.strip()]
            total = len(pkgs)
            yum_lines.append(f"{total} package(s) can be upgraded:")
            yum_lines.append(raw)
        sec_raw = _run("yum updateinfo list security 2>/dev/null | tail -n +2 | head -50")
        if sec_raw != "(no output)" and sec_raw.strip():
            sec_pkgs = [l for l in sec_raw.splitlines() if l.strip()]
            if sec_pkgs:
                yum_lines.insert(0, f"[!] {len(sec_pkgs)} security update(s) available")
        sections.append("=== yum ===\n" + "\n".join(yum_lines))
    elif not has_dnf:
        sections.append("=== yum ===\nN/A (not installed)")

    # --- snap ---
    if _has("snap"):
        snap_lines: list[str] = []
        raw = _run("snap refresh --list 2>/dev/null | head -50")
        if raw == "(no output)" or not raw.strip() or "All snaps up to date" in raw:
            snap_lines.append("[OK] All snaps are up to date")
        else:
            pkgs = [l for l in raw.splitlines() if l.strip()]
            # First line is the header
            count = max(0, len(pkgs) - 1)
            snap_lines.append(f"{count} snap(s) can be updated:")
            snap_lines.append(raw)
        sections.append("=== snap ===\n" + "\n".join(snap_lines))
    else:
        sections.append("=== snap ===\nN/A (not installed)")

    # --- flatpak ---
    if _has("flatpak"):
        flat_lines: list[str] = []
        raw = _run("flatpak remote-ls --updates 2>/dev/null | head -50")
        if raw == "(no output)" or not raw.strip():
            flat_lines.append("[OK] All flatpaks are up to date")
        else:
            pkgs = [l for l in raw.splitlines() if l.strip()]
            flat_lines.append(f"{len(pkgs)} flatpak(s) can be updated:")
            flat_lines.append(raw)
        sections.append("=== flatpak ===\n" + "\n".join(flat_lines))
    else:
        sections.append("=== flatpak ===\nN/A (not installed)")

    max_chars = 16000
    output = "\n\n".join(sections)
    if len(output) > max_chars:
        overflow = len(output) - max_chars
        suffix = f"\n[... {overflow} chars truncated]"
        output = output[:max_chars - len(suffix)] + suffix
    return output


@tool
def update_packages(manager: str = "auto") -> str:
    """Install available package updates. REQUIRES CONFIRMATION.

    This is a WRITE action. The safety layer will prompt the user
    for confirmation before executing.

    Auto-detects the system package manager (apt, dnf, or yum) when
    manager='auto'. Snap and flatpak must be requested explicitly.

    Args:
        manager: Package manager to use. 'auto' detects the primary system
                 manager. Explicit options: 'apt', 'dnf', 'yum', 'snap', 'flatpak'.
    """
    valid_managers = {"auto", "apt", "dnf", "yum", "snap", "flatpak"}
    if manager not in valid_managers:
        return f"[ERROR] Unknown package manager: '{manager}'. Valid options: {', '.join(sorted(valid_managers))}"

    def _has(binary: str) -> bool:
        return run_cmd(["bash", "-c", f"which {binary} 2>/dev/null"]) != "(no output)"

    commands = {
        "apt":     "sudo apt-get update && sudo apt-get upgrade -y",
        "dnf":     "sudo dnf upgrade -y",
        "yum":     "sudo yum update -y",
        "snap":    "sudo snap refresh",
        "flatpak": "sudo flatpak update -y",
    }

    if manager == "auto":
        if _has("apt-get"):
            manager = "apt"
        elif _has("dnf"):
            manager = "dnf"
        elif _has("yum"):
            manager = "yum"
        else:
            return "[ERROR] No supported package manager found (apt, dnf, yum)."

    if not _has(manager.split("-")[0]):  # handle apt-get → apt
        check = "apt-get" if manager == "apt" else manager
        if not _has(check):
            return f"[ERROR] Package manager '{manager}' is not installed on this system."

    output = run_cmd(["bash", "-c", commands[manager]], timeout=300)

    max_chars = 16000
    if len(output) > max_chars:
        overflow = len(output) - max_chars
        suffix = f"\n[... {overflow} chars truncated]"
        output = output[:max_chars - len(suffix)] + suffix

    return f"=== {manager} upgrade ===\n{output}"


# ═══════════════════════════════════════════════════════════════════════════════
# GENERAL PURPOSE
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def search_web(query: str, max_results: int = 5) -> str:
    """Search the web using DuckDuckGo. Use this tool whenever the user asks
    about anything that requires up-to-date information from the internet,
    including news, current events, error messages, documentation,
    troubleshooting guides, CVEs, or any topic you don't have current data on.

    IMPORTANT: Always use this tool instead of relying on training data when
    the user asks for recent/latest/current information or news.

    Args:
        query: Search query string (e.g. 'latest tech news',
               'nginx 502 bad gateway fix', 'CVE-2024-1234').
        max_results: Number of results to return (default 5, max 10).
    """
    try:
        from ddgs import DDGS
    except ImportError:
        import sys
        print("\033[33mInstalling ddgs...\033[0m")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "ddgs"]
            )
            from ddgs import DDGS
        except (subprocess.CalledProcessError, ImportError) as e:
            return f"[ERROR] Failed to install ddgs: {e}"

    import warnings
    max_results = min(max_results, 10)
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Impersonate")
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return f"No results found for: {query}"
        lines = []
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}")
            lines.append(f"   {r['href']}")
            lines.append(f"   {r['body']}")
            lines.append("")
        return "\n".join(lines)
    except Exception as e:
        return f"[ERROR] Search failed: {e}"


@tool
def change_directory(path: str) -> str:
    """Change the working directory. Use this instead of 'cd' in run_command.

    This affects all subsequent tool calls. Running 'cd' inside run_command
    only changes the directory for that single command.

    Args:
        path: Directory path to change to (e.g. '/var/log', '/etc/nginx', '~').
    """
    expanded = os.path.expanduser(path)
    try:
        os.chdir(expanded)
        return f"Changed directory to: {os.getcwd()}"
    except FileNotFoundError:
        return f"[ERROR] Directory not found: {expanded}"
    except PermissionError:
        return f"[ERROR] Permission denied: {expanded}"
    except Exception as e:
        return f"[ERROR] {e}"


@tool
def run_command(command: str) -> str:
    """Run a shell command. LAST RESORT — use a specific tool when one exists.

    Only use this when no dedicated tool covers the task. For example, use
    check_disk_usage for disk checks, query_journal_logs for logs, etc.

    Dangerous commands (rm, dd, shutdown, reboot, etc.) are blocked.
    Use change_directory to change directories — 'cd' here is temporary.

    Args:
        command: The shell command to execute.
    """
    # Intercept bare 'cd' commands — they don't persist across subprocesses.
    # Weak models use run_command("cd /path") instead of change_directory.
    stripped = command.strip()
    if stripped == "cd" or (
        stripped.startswith("cd ") and not any(c in stripped for c in ";|&")
    ):
        path = stripped[3:].strip() if len(stripped) > 2 else "~"
        if len(path) >= 2 and path[0] in "\"'" and path[-1] == path[0]:
            path = path[1:-1]
        expanded = os.path.expanduser(path or "~")
        try:
            os.chdir(expanded)
            return f"Changed directory to: {os.getcwd()}"
        except FileNotFoundError:
            return f"[ERROR] Directory not found: {expanded}"
        except PermissionError:
            return f"[ERROR] Permission denied: {expanded}"
        except Exception as e:
            return f"[ERROR] {e}"

    return run_cmd(["bash", "-c", command])


# ═══════════════════════════════════════════════════════════════════════════════
# PLUGIN LOADER
# ═══════════════════════════════════════════════════════════════════════════════

def _load_extra_tools() -> tuple[list, set]:
    """Auto-discover tools from tools_extra/ (including subfolders).

    Supports both flat and categorized layouts:
        tools_extra/threat_intel.py          (flat)
        tools_extra/network/scanner.py       (categorized)
        tools_extra/network/dns_tools.py     (categorized)

    Returns (tools_list, write_tools_set).
    Files and directories starting with '_' are skipped.
    Errors are warned but don't crash startup.
    """
    extra_dir = Path(__file__).parent / "tools_extra"
    tools = []
    write_tools = set()

    if not extra_dir.is_dir():
        return tools, write_tools

    for py_file in sorted(extra_dir.glob("**/*.py")):
        if any(part.startswith("_") for part in py_file.relative_to(extra_dir).parts):
            continue

        # Build a unique module name from the relative path:
        #   tools_extra/threat_intel.py       -> tools_extra.threat_intel
        #   tools_extra/network/scanner.py    -> tools_extra.network.scanner
        rel = py_file.relative_to(extra_dir.parent)
        module_name = ".".join(rel.with_suffix("").parts)

        try:
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:
            rel_path = py_file.relative_to(extra_dir)
            print(f"\033[33m[WARNING] Failed to load plugin {rel_path}: {e}\033[0m")
            continue

        # Collect @tool functions (BaseTool instances)
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if isinstance(attr, BaseTool):
                tools.append(attr)

        # Collect optional WRITE_TOOLS set
        mod_write = getattr(mod, "WRITE_TOOLS", None)
        if isinstance(mod_write, set):
            write_tools |= mod_write

    if tools:
        names = ", ".join(t.name for t in tools)
        print(f"\033[90mLoaded {len(tools)} extra tool(s): {names}\033[0m")

        # Warn about duplicate tool names — LangChain can't distinguish them
        seen = {}
        for t in tools:
            if t.name in seen:
                print(
                    f"\033[33m[WARNING] Duplicate tool name '{t.name}' — "
                    f"already loaded from a different plugin. "
                    f"LangChain may not call the correct one.\033[0m"
                )
            seen[t.name] = True

    return tools, write_tools


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

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

# Auto-discover plugins from tools_extra/
EXTRA_TOOLS, EXTRA_WRITE_TOOLS = _load_extra_tools()
ALL_TOOLS += EXTRA_TOOLS
