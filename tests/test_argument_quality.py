"""
Evals for LLM argument quality.

Tests whether the LLM fills in tool parameters correctly based on
the user's natural-language question. The tool docstrings include
Args: descriptions — this eval checks if the model reads them properly.

Each test asks a question, verifies the right tool is called, and then
checks that specific arguments were set to expected values.

Run:  python -m pytest tests/test_argument_quality.py -v -s

Requires a working LLM backend (set LLM_PROVIDER / API keys as usual).
"""

import os
import sys
import pytest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()


# ─── LLM setup ──────────────────────────────────────────────────────────────

def _get_llm():
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
        print(f"\n  Using OpenAI ({model})")
        return ChatOpenAI(model=model, base_url=os.environ.get("OPENAI_BASE_URL"), temperature=0)
    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
        print(f"\n  Using Anthropic ({model})")
        return ChatAnthropic(model=model, temperature=0)
    else:
        pytest.skip(f"Unknown LLM_PROVIDER: {provider}")


def _build_system_prompt():
    uname = os.uname()
    return f"""You are a helpful Linux sysadmin assistant running on this machine.
You help the user investigate system issues, check health, and manage services.

Current time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
Hostname: {uname.nodename}
OS: {uname.sysname} {uname.release}

Guidelines:
- Use the available tools to investigate before answering.
- IMPORTANT: Only report information that comes from tool output. Never
  guess or make up file listings, log entries, or command results.
- IMPORTANT: Always prefer a specific tool over run_command. Only use
  run_command as a LAST RESORT when no dedicated tool covers the task.
- IMPORTANT: Always call tools directly. Never write tool calls as JSON text
  in your response — use the actual tool-calling mechanism instead.
"""


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def llm_with_tools():
    from tools import ALL_TOOLS
    llm = _get_llm()
    return llm.bind_tools(ALL_TOOLS)


# ─── Helper ──────────────────────────────────────────────────────────────────

def get_first_tool_call(llm_with_tools, question: str):
    """Ask the LLM a question and return (tool_name, args_dict) of the first
    tool call, or (None, {}) if no tool was called."""
    messages = [
        {"role": "system", "content": _build_system_prompt()},
        {"role": "user", "content": question},
    ]
    response = llm_with_tools.invoke(messages)

    if hasattr(response, "tool_calls") and response.tool_calls:
        tc = response.tool_calls[0]
        return tc["name"], tc.get("args", {})

    return None, {}


# ═════════════════════════════════════════════════════════════════════════════
#  TEST CASES
#
#  Each case: (question, expected_tool, arg_checks, description)
#
#  arg_checks is a dict of:
#    arg_name -> check_function(value) -> bool
#
#  Check functions allow flexible matching (contains, equals, type checks)
#  rather than exact string comparison, since LLMs may phrase things
#  slightly differently ("1 hour ago" vs "1h ago" vs "one hour ago").
# ═════════════════════════════════════════════════════════════════════════════

def eq(expected):
    """Exact match (case-insensitive for strings)."""
    def check(val):
        if isinstance(val, str) and isinstance(expected, str):
            return val.lower() == expected.lower()
        return val == expected
    check.__repr__ = lambda: f"eq({expected!r})"
    return check


def contains(substring):
    """String contains substring (case-insensitive)."""
    def check(val):
        return isinstance(val, str) and substring.lower() in val.lower()
    check.__repr__ = lambda: f"contains({substring!r})"
    return check


def one_of(*options):
    """Value matches one of the given options (case-insensitive for strings)."""
    def check(val):
        for opt in options:
            if isinstance(val, str) and isinstance(opt, str):
                if val.lower() == opt.lower():
                    return True
            elif val == opt:
                return True
        return False
    check.__repr__ = lambda: f"one_of{options!r}"
    return check


def is_type(t):
    """Value is of the given type."""
    def check(val):
        return isinstance(val, t)
    check.__repr__ = lambda: f"is_type({t.__name__})"
    return check


def is_present():
    """Argument exists and is not None/empty."""
    def check(val):
        return val is not None and val != ""
    check.__repr__ = lambda: "is_present()"
    return check


def gt(n):
    """Value is greater than n (numeric)."""
    def check(val):
        try:
            return float(val) > n
        except (TypeError, ValueError):
            return False
    check.__repr__ = lambda: f"gt({n})"
    return check


# ─── query_journal_logs ──────────────────────────────────────────────────────

EVAL_CASES = [
    (
        "Show me nginx errors from the last hour",
        "query_journal_logs",
        {
            "unit": one_of("nginx", "nginx.service"),
            "priority": one_of("err", "error", "3"),
            "since": contains("hour"),
        },
        "journal — unit, priority, and since should all be set",
    ),
    (
        "Show me the last 100 lines of sshd logs",
        "query_journal_logs",
        {
            "unit": one_of("sshd", "sshd.service", "ssh", "ssh.service"),
            "lines": one_of(100, "100"),
        },
        "journal — unit and lines count",
    ),
    (
        "Search the journal for 'out of memory' messages",
        "query_journal_logs",
        {
            "grep": contains("out of memory"),
        },
        "journal — grep filter should contain the search term",
    ),
    (
        "Show me critical errors from docker since yesterday",
        "query_journal_logs",
        {
            "unit": one_of("docker", "docker.service"),
            "priority": one_of("crit", "critical", "2"),
            "since": is_present(),
        },
        "journal — unit, priority=crit, since=yesterday",
    ),
    (
        "Show me warning-level journal entries from today",
        "query_journal_logs",
        {
            "priority": one_of("warning", "4"),
            "since": is_present(),
        },
        "journal — priority=warning, since=today",
    ),

    # ── read_log_file ────────────────────────────────────────────────────
    (
        "Show me the last 200 lines of /var/log/syslog",
        "read_log_file",
        {
            "path": eq("/var/log/syslog"),
            "lines": one_of(200, "200"),
        },
        "log file — exact path and line count",
    ),
    (
        "Search /var/log/auth.log for 'Failed password'",
        "read_log_file",
        {
            "path": eq("/var/log/auth.log"),
            "grep": contains("failed password"),
        },
        "log file — path and grep filter",
    ),

    # ── Service management ───────────────────────────────────────────────
    (
        "Check the status of postgresql",
        "check_service_status",
        {
            "service": one_of("postgresql", "postgresql.service"),
        },
        "service status — correct service name",
    ),
    (
        "Restart nginx",
        "restart_service",
        {
            "service": one_of("nginx", "nginx.service"),
        },
        "restart — correct service name",
    ),
    (
        "Stop the redis service",
        "stop_service",
        {
            "service": one_of("redis", "redis.service", "redis-server", "redis-server.service"),
        },
        "stop — correct service name",
    ),

    # ── Network tools ────────────────────────────────────────────────────
    (
        "Ping google.com 10 times",
        "ping_host",
        {
            "host": one_of("google.com", "www.google.com"),
            "count": one_of(10, "10"),
        },
        "ping — host and count",
    ),
    (
        "What does example.org resolve to?",
        "dns_lookup",
        {
            "domain": one_of("example.org", "www.example.org"),
        },
        "DNS — correct domain",
    ),
    (
        "Check if https://httpbin.org/status/200 is responding",
        "check_url_health",
        {
            "url": contains("httpbin.org"),
        },
        "URL health — URL passed correctly",
    ),

    # ── File system tools ────────────────────────────────────────────────
    (
        "How big is the /home directory?",
        "check_directory_size",
        {
            "path": eq("/home"),
        },
        "directory size — correct path",
    ),
    (
        "Check disk usage on /var",
        "check_disk_usage",
        {
            "path": one_of("/var", "/var/"),
        },
        "disk usage — correct mount/path",
    ),
    (
        "What files were changed in /var/log in the last 2 days?",
        "find_recent_files",
        {
            "path": one_of("/var/log", "/var/log/"),
            "minutes": one_of(2880, "2880", 2 * 24 * 60),  # 2 days in minutes
        },
        "recent files — path and minutes (2 days = 2880 min)",
    ),

    # ── Top processes with count ─────────────────────────────────────────
    (
        "Show me the top 20 processes by CPU usage",
        "check_top_processes",
        {
            "count": one_of(20, "20"),
        },
        "top processes — custom count",
    ),

    # ── dmesg with level filter ──────────────────────────────────────────
    (
        "Show me error-level kernel messages",
        "check_dmesg",
        {
            "level": one_of("err", "error"),
        },
        "dmesg — level filter",
    ),

    # ── run_command — verify the actual command string ────────────────────
    (
        "Show me the routing table",
        "run_command",
        {
            "command": one_of(
                "ip route", "ip route show", "ip r", "ip r show",
                "route -n", "netstat -rn",
            ),
        },
        "run_command — correct Linux command for routing table",
    ),
]


# ═════════════════════════════════════════════════════════════════════════════
#  Parametrized test
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "question, expected_tool, arg_checks, description",
    EVAL_CASES,
    ids=[c[3] for c in EVAL_CASES],
)
def test_argument_quality(llm_with_tools, question, expected_tool,
                          arg_checks, description):
    """Verify the LLM sets tool arguments correctly."""
    tool_name, args = get_first_tool_call(llm_with_tools, question)

    print(f"\n  Q: {question}")
    print(f"  Tool: {tool_name}")
    print(f"  Args: {args}")

    # Must call a tool
    assert tool_name is not None, (
        f"Model did not call any tool for: {question!r}"
    )

    # Must call the expected tool
    assert tool_name == expected_tool, (
        f"Wrong tool for: {question!r}\n"
        f"  Got:      {tool_name}\n"
        f"  Expected: {expected_tool}"
    )

    # Check each expected argument
    failures = []
    for arg_name, checker in arg_checks.items():
        actual = args.get(arg_name)
        if not checker(actual):
            failures.append(
                f"  {arg_name}: got {actual!r}, expected {checker!r}"
            )

    if failures:
        print(f"  FAILURES:")
        for f in failures:
            print(f)

    assert not failures, (
        f"Argument quality issues for: {question!r}\n"
        f"  Tool: {tool_name}, Args: {args}\n"
        + "\n".join(failures)
    )
