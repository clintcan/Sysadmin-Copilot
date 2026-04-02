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
    # ── Logs ──────────────────────────────────────────────────────────────
    (
        "Show me the last 50 lines of nginx logs",
        {"query_journal_logs", "read_log_file"},
        "log query — should use journal or log file tool",
    ),
    (
        "Are there any errors in the system journal from the last hour?",
        {"query_journal_logs"},
        "journal query with time filter",
    ),
    (
        "Show me kernel messages",
        {"check_dmesg"},
        "kernel log — should use dmesg",
    ),
    (
        "Check /var/log/auth.log for failed login attempts",
        {"read_log_file"},
        "specific log file read",
    ),

    # ── System health ────────────────────────────────────────────────────
    (
        "How much disk space is left?",
        {"check_disk_usage"},
        "disk space check",
    ),
    (
        "How big is the /var/log directory?",
        {"check_directory_size"},
        "directory size — should use check_directory_size not disk_usage",
    ),
    (
        "How much RAM is being used?",
        {"check_memory"},
        "memory check",
    ),
    (
        "What's the CPU load right now?",
        {"check_cpu_and_load"},
        "CPU/load check",
    ),
    (
        "What processes are using the most CPU?",
        {"check_top_processes"},
        "top processes",
    ),
    (
        "Are there any zombie processes?",
        {"find_zombie_processes"},
        "zombie process check",
    ),
    (
        "Why is the server running slow?",
        {"check_cpu_and_load", "check_memory", "check_top_processes"},
        "open-ended performance — any health tool is acceptable as first step",
    ),

    # ── Services ─────────────────────────────────────────────────────────
    (
        "Is nginx running?",
        {"check_service_status"},
        "service status check",
    ),
    (
        "Are there any failed services?",
        {"list_failed_services"},
        "failed services check",
    ),
    (
        "Restart the nginx service",
        {"restart_service"},
        "service restart — must use restart_service not run_command",
    ),
    (
        "Stop postgresql",
        {"stop_service"},
        "service stop",
    ),

    # ── Network ──────────────────────────────────────────────────────────
    (
        "What ports are open on this machine?",
        {"check_open_ports"},
        "open ports check",
    ),
    (
        "Show me active network connections",
        {"check_network_connections"},
        "network connections",
    ),
    (
        "Can we reach 8.8.8.8?",
        {"ping_host"},
        "ping check",
    ),
    (
        "What IP does example.com resolve to?",
        {"dns_lookup"},
        "DNS lookup",
    ),
    (
        "Is https://example.com responding?",
        {"check_url_health"},
        "URL health check",
    ),

    # ── Users & files ────────────────────────────────────────────────────
    (
        "Who is logged into the server right now?",
        {"check_logged_in_users"},
        "logged-in users",
    ),
    (
        "Show me the cron jobs on this system",
        {"check_cron_jobs"},
        "cron jobs",
    ),
    (
        "What files were modified in /etc in the last 24 hours?",
        {"find_recent_files"},
        "recently modified files",
    ),

    # ── Security ─────────────────────────────────────────────────────────
    (
        "Run a security audit on this system",
        {"system_audit"},
        "security audit",
    ),
    (
        "Are there any outdated packages?",
        {"check_outdated_packages"},
        "outdated packages check",
    ),
    (
        "Update all system packages",
        {"update_packages", "check_outdated_packages"},
        "package update — update_packages or check first is acceptable",
    ),

    # ── General purpose (these SHOULD use run_command) ───────────────────
    (
        "Show me the routing table",
        {"run_command"},
        "no dedicated tool — run_command is correct here",
    ),
    (
        "What's in /proc/cpuinfo?",
        {"run_command"},
        "reading /proc — run_command is appropriate",
    ),

    # ── Ambiguous / tricky ───────────────────────────────────────────────
    (
        "Check if port 443 is open and if nginx is handling it",
        {"check_open_ports", "check_service_status", "check_network_connections"},
        "compound question — either network or service tool is fine first",
    ),
    (
        "The website is down, help me figure out why",
        {"check_service_status", "check_url_health", "check_open_ports",
         "check_network_connections", "query_journal_logs"},
        "open-ended debugging — many reasonable first steps",
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
