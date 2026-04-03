"""
Evals for LLM tool selection accuracy.

Tests whether the LLM picks the correct tool(s) for natural-language
questions. Calls the actual LLM with the real tool definitions but
does NOT execute any tools — it only inspects which tools the model
chose to call.

Requires a working LLM backend (set LLM_PROVIDER / API keys as usual).

Run:  python -m pytest tests/test_tool_selection.py -v -s
      LLM_PROVIDER=anthropic python -m pytest tests/test_tool_selection.py -v -s

The -s flag shows per-test tool selections.
"""

import os
import sys
import pytest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()


# ─── LLM setup (standalone, does not import agent.py) ────────────────────────

def _get_llm():
    """Initialize the LLM based on environment — mirrors agent.py's get_llm()
    but avoids importing agent.py (which pulls in the full langchain stack)."""
    provider = os.environ.get("LLM_PROVIDER", "ollama").lower()

    if provider == "ollama":
        from langchain_ollama import ChatOllama
        model = os.environ.get("OLLAMA_MODEL", "qwen3.5")
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        print(f"\n  Using Ollama ({model}) at {base_url}")
        return ChatOllama(model=model, base_url=base_url, temperature=0)

    elif provider == "openai":
        from langchain_openai import ChatOpenAI
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        base_url = os.environ.get("OPENAI_BASE_URL")
        print(f"\n  Using OpenAI ({model})")
        return ChatOpenAI(model=model, base_url=base_url, temperature=0)

    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
        print(f"\n  Using Anthropic ({model})")
        return ChatAnthropic(model=model, temperature=0)

    else:
        pytest.skip(f"Unknown LLM_PROVIDER: {provider}")


def _build_system_prompt():
    """Simplified system prompt for eval — same guidance as agent.py."""
    uname = os.uname()
    return f"""You are a helpful Linux sysadmin assistant running on this machine.
You help the user investigate system issues, check health, and manage services.

Current time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
Hostname: {uname.nodename}
OS: {uname.sysname} {uname.release}

Guidelines:
- Use the available tools to investigate before answering.
- IMPORTANT: Always prefer a specific tool over run_command. Only use
  run_command as a LAST RESORT when no dedicated tool covers the task.
  For example, use check_disk_usage instead of run_command("df -h").
- IMPORTANT: Always call tools directly. Never write tool calls as JSON text
  in your response — use the actual tool-calling mechanism instead.
"""


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def llm_with_tools():
    """Initialize the LLM and bind tools once for the entire test session."""
    from tools import ALL_TOOLS
    llm = _get_llm()
    return llm.bind_tools(ALL_TOOLS)


# ─── Helper ──────────────────────────────────────────────────────────────────

def get_selected_tools(llm_with_tools, question: str) -> list[str]:
    """Ask the LLM a question and return the tool name(s) it chose.

    Does NOT execute any tools — just inspects the model's tool_calls.
    """
    messages = [
        {"role": "system", "content": _build_system_prompt()},
        {"role": "user", "content": question},
    ]
    response = llm_with_tools.invoke(messages)

    tool_names = []
    if hasattr(response, "tool_calls") and response.tool_calls:
        for tc in response.tool_calls:
            tool_names.append(tc["name"])

    return tool_names


# ═════════════════════════════════════════════════════════════════════════════
#  TEST CASES
#
#  Each case is: (question, acceptable_tools, description)
#
#  acceptable_tools: set of tool names that are correct for this question.
#  The eval passes if the model's FIRST tool call is in this set.
# ═════════════════════════════════════════════════════════════════════════════

EVAL_CASES = [
    # ── Logs (8) ─────────────────────────────────────────────────────────
    (
        "Show me the last 50 lines of nginx logs",
        {"query_journal_logs", "read_log_file"},
        "logs: nginx logs — journal or file",
    ),
    (
        "Are there any errors in the system journal from the last hour?",
        {"query_journal_logs"},
        "logs: journal errors with time filter",
    ),
    (
        "Show me kernel messages",
        {"check_dmesg"},
        "logs: kernel — should use dmesg",
    ),
    (
        "Check /var/log/auth.log for failed login attempts",
        {"read_log_file"},
        "logs: specific file path",
    ),
    (
        "What did sshd log recently?",
        {"query_journal_logs"},
        "logs: service journal query",
    ),
    (
        "Show me the latest syslog entries",
        {"query_journal_logs", "read_log_file"},
        "logs: syslog — either approach valid",
    ),
    (
        "Are there any warnings in the journal for docker?",
        {"query_journal_logs"},
        "logs: journal with unit + priority",
    ),
    (
        "Check dmesg for hardware errors",
        {"check_dmesg"},
        "logs: dmesg with context",
    ),

    # ── System health (12) ───────────────────────────────────────────────
    (
        "How much disk space is left?",
        {"check_disk_usage"},
        "health: disk space",
    ),
    (
        "How big is the /var/log directory?",
        {"check_directory_size"},
        "health: directory size",
    ),
    (
        "How much RAM is being used?",
        {"check_memory"},
        "health: memory — RAM",
    ),
    (
        "What's the CPU load right now?",
        {"check_cpu_and_load"},
        "health: CPU load",
    ),
    (
        "What processes are using the most CPU?",
        {"check_top_processes"},
        "health: top processes",
    ),
    (
        "Are there any zombie processes?",
        {"find_zombie_processes"},
        "health: zombie processes",
    ),
    (
        "Why is the server running slow?",
        {"check_cpu_and_load", "check_memory", "check_top_processes"},
        "health: open-ended performance",
    ),
    (
        "Is the filesystem filling up?",
        {"check_disk_usage"},
        "health: filesystem — alt phrasing",
    ),
    (
        "How much storage is available on /home?",
        {"check_disk_usage"},
        "health: storage on specific mount",
    ),
    (
        "What's the swap usage?",
        {"check_memory"},
        "health: swap — part of memory tool",
    ),
    (
        "Show me system resource utilization",
        {"check_cpu_and_load", "check_memory", "check_top_processes"},
        "health: general resource check",
    ),
    (
        "What's hogging all the disk space in /var?",
        {"check_directory_size"},
        "health: directory size — investigative",
    ),

    # ── Services (8) ─────────────────────────────────────────────────────
    (
        "Is nginx running?",
        {"check_service_status"},
        "services: status check",
    ),
    (
        "Are there any failed services?",
        {"list_failed_services"},
        "services: failed list",
    ),
    (
        "Restart the nginx service",
        {"restart_service"},
        "services: restart",
    ),
    (
        "Stop postgresql",
        {"stop_service"},
        "services: stop",
    ),
    (
        "What's the status of the docker daemon?",
        {"check_service_status"},
        "services: status — alt phrasing",
    ),
    (
        "Is redis up and running?",
        {"check_service_status"},
        "services: status — informal",
    ),
    (
        "Bring nginx back up",
        {"restart_service"},
        "services: restart — informal phrasing",
    ),
    (
        "Which systemd services have crashed?",
        {"list_failed_services"},
        "services: failed — alt phrasing",
    ),

    # ── Network (8) ──────────────────────────────────────────────────────
    (
        "What ports are open on this machine?",
        {"check_open_ports"},
        "network: open ports",
    ),
    (
        "Show me active network connections",
        {"check_network_connections"},
        "network: connections",
    ),
    (
        "Can we reach 8.8.8.8?",
        {"ping_host"},
        "network: ping",
    ),
    (
        "What IP does example.com resolve to?",
        {"dns_lookup"},
        "network: DNS lookup",
    ),
    (
        "Is https://example.com responding?",
        {"check_url_health"},
        "network: URL health",
    ),
    (
        "Is anything listening on port 3306?",
        {"check_open_ports", "check_network_connections"},
        "network: specific port check",
    ),
    (
        "Resolve the A record for google.com",
        {"dns_lookup"},
        "network: DNS — technical phrasing",
    ),
    (
        "Check connectivity to 10.0.0.1",
        {"ping_host"},
        "network: ping — alt phrasing",
    ),

    # ── Users & files (6) ────────────────────────────────────────────────
    (
        "Who is logged into the server right now?",
        {"check_logged_in_users"},
        "users: logged-in",
    ),
    (
        "Show me the cron jobs on this system",
        {"check_cron_jobs"},
        "users: cron jobs",
    ),
    (
        "What files were modified in /etc in the last 24 hours?",
        {"find_recent_files"},
        "files: recently modified",
    ),
    (
        "Are there any active SSH sessions?",
        {"check_logged_in_users", "check_network_connections"},
        "users: SSH sessions",
    ),
    (
        "Show me scheduled tasks",
        {"check_cron_jobs"},
        "users: cron — alt phrasing",
    ),
    (
        "Any new files created in /tmp recently?",
        {"find_recent_files"},
        "files: recent in /tmp",
    ),

    # ── Security (6) ─────────────────────────────────────────────────────
    (
        "Run a security audit on this system",
        {"system_audit"},
        "security: audit",
    ),
    (
        "Are there any outdated packages?",
        {"check_outdated_packages"},
        "security: outdated packages",
    ),
    (
        "Update all system packages",
        {"update_packages", "check_outdated_packages"},
        "security: package update",
    ),
    (
        "Check this system for security issues",
        {"system_audit"},
        "security: audit — alt phrasing",
    ),
    (
        "Are there packages with available updates?",
        {"check_outdated_packages"},
        "security: outdated — alt phrasing",
    ),
    (
        "Patch the system",
        {"update_packages", "check_outdated_packages"},
        "security: patch — informal",
    ),

    # ── General purpose (4) ──────────────────────────────────────────────
    (
        "Show me the routing table",
        {"run_command"},
        "general: routing table",
    ),
    (
        "What's in /proc/cpuinfo?",
        {"run_command"},
        "general: /proc file",
    ),
    (
        "Show me the ARP table",
        {"run_command"},
        "general: ARP — no dedicated tool",
    ),
    (
        "Run uname -r to see the kernel version",
        {"run_command"},
        "general: uname — explicit command request",
    ),

    # ── Ambiguous / compound (5) ─────────────────────────────────────────
    (
        "Check if port 443 is open and if nginx is handling it",
        {"check_open_ports", "check_service_status", "check_network_connections"},
        "ambiguous: port + service compound",
    ),
    (
        "The website is down, investigate why",
        {"check_service_status", "check_url_health", "check_open_ports",
         "check_network_connections", "query_journal_logs"},
        "ambiguous: website down — many valid starts",
    ),
    (
        "We're having intermittent connection drops, check the network",
        {"check_network_connections", "check_open_ports", "ping_host",
         "check_cpu_and_load", "query_journal_logs"},
        "ambiguous: connection drops",
    ),
    (
        "The app is throwing 502 errors, investigate",
        {"check_service_status", "check_url_health", "query_journal_logs",
         "check_open_ports", "check_network_connections"},
        "ambiguous: 502 errors — multiple investigation paths",
    ),
    (
        "Something crashed overnight, can you investigate?",
        {"list_failed_services", "query_journal_logs", "check_dmesg",
         "system_audit"},
        "ambiguous: overnight crash — log or service check",
    ),
]


# ═════════════════════════════════════════════════════════════════════════════
#  Parametrized test
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "question, acceptable, description",
    EVAL_CASES,
    ids=[c[2] for c in EVAL_CASES],
)
def test_tool_selection(llm_with_tools, question, acceptable, description):
    """Verify the LLM selects an appropriate tool for the question."""
    selected = get_selected_tools(llm_with_tools, question)

    # The model should call at least one tool (not just answer with text)
    assert len(selected) > 0, (
        f"Model did not call any tools for: {question!r}\n"
        f"Expected one of: {sorted(acceptable)}"
    )

    first_tool = selected[0]
    print(f"\n  Q: {question}")
    print(f"  Selected: {selected}")
    print(f"  Expected one of: {sorted(acceptable)}")

    # Primary check: first tool is in the acceptable set
    assert first_tool in acceptable, (
        f"Wrong tool for: {question!r}\n"
        f"  Got:      {first_tool}\n"
        f"  Expected: {sorted(acceptable)}"
    )

    # Soft check: warn if run_command was used when a dedicated tool exists
    if first_tool == "run_command" and acceptable != {"run_command"}:
        print(f"  WARNING: Used run_command instead of a dedicated tool")
