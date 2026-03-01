# Chapter 2 — Architecture

## Component Map

```
┌─────────────────────────────────────────────────────────────────┐
│                        agent.py (REPL)                          │
│                                                                 │
│  readline input ──► built-in commands (help/audit/new/clear)   │
│                 └──► LangGraph ReAct agent                      │
│                          │                                      │
│                    conversation history []                      │
└─────────────────────────────────┬───────────────────────────────┘
                                  │ agent.stream()
                    ┌─────────────▼──────────────┐
                    │      LangGraph / LangChain  │
                    │                             │
                    │  Reason ──► Act ──► Reason  │
                    │   (LLM)       (tool)  (LLM) │
                    └─────────────┬──────────────┘
                                  │ tool call
              ┌───────────────────▼───────────────────┐
              │           safety.py (wrappers)         │
              │                                        │
              │  READ tool ──► BLOCKED check ──► run  │
              │  WRITE tool ──► allowlist check        │
              │              ──► confirmation prompt   │
              │              ──► run                   │
              └───────────────────┬───────────────────┘
                       │          │
              ┌────────▼──┐  ┌────▼──────────────────┐
              │  tools.py │  │      audit.py          │
              │           │  │                        │
              │ run_cmd() │  │ log_command() ──► JSONL│
              │ (Linux    │  │ log_interaction()      │
              │  CLI)     │  │ show() / show_last()   │
              └───────────┘  └────────────────────────┘
```

---

## Four Modules, Four Responsibilities

| Module | Responsibility |
|--------|---------------|
| `agent.py` | Entry point. Initialises the LLM, creates the ReAct agent, runs the REPL loop, manages conversation history, streams output. |
| `tools.py` | 26 tools (24 specific + 2 general-purpose), each decorated with `@tool`. All call `run_cmd()` to execute CLI commands via subprocess. |
| `safety.py` | Wraps every tool before it reaches the agent. READ tools get blocked-pattern detection; WRITE tools get allowlist and confirmation checks. |
| `audit.py` | Logs every tool invocation (tool name, args, status) to an in-memory list and a persistent JSONL file. |

---

## Data Flow: One Turn

Here's what happens when a user types a question:

```
1. User types: "Check if nginx is running"

2. agent.py appends HumanMessage to history[]

3. agent.stream() sends history to LangGraph

4. LangGraph LLM reasons:
   "I should call check_service_status with service='nginx'"

5. LangGraph dispatches the tool call

6. safety.py _wrap_read_tool() fires:
   a. Checks 'nginx' against BLOCKED_PATTERNS → clean
   b. Calls audit_logger.log_command('check_service_status', {'service': 'nginx'})
   c. Calls the real check_service_status('nginx')

7. tools.py check_service_status() calls:
   run_cmd(["systemctl", "status", "nginx", "--no-pager", "-l"])
   → returns output string (capped at 8000 chars)

8. Output returned to LangGraph

9. LLM reads the output, generates a plain-English answer

10. agent.py streams the answer tokens to the terminal

11. history[] updated with the full thread
    (user message + tool calls + assistant reply)

12. audit_logger.log_interaction() writes interaction summary to JSONL
```

---

## File Map

```
sysadmin-copilot/
├── agent.py          ← Start here. The REPL, LLM init, agent loop.
├── tools.py          ← All CLI tools. Each is a @tool function.
├── safety.py         ← Tool wrappers. READ/WRITE/BLOCKED logic.
├── audit.py          ← Logging. In-memory + JSONL file.
├── install.sh        ← Automated installer (service account + sudoers).
├── sync-sudoers.sh   ← Re-generates sudoers after editing safety.py.
├── requirements.txt  ← Python dependencies.
└── docs/             ← This book.
```

Runtime paths created automatically:

```
~/.sysadmin-copilot/
└── logs/
    ├── session_20240115_143022.jsonl
    ├── session_20240116_091545.jsonl
    └── ...
```

---

## Why LangGraph Instead of a Simple Loop?

LangChain's `create_agent` provides the **ReAct loop** (Reason → Act → Reason → ...) out of the box:

- The LLM is called to *reason* about what to do next
- When it decides to call a tool, LangGraph *acts* by dispatching the call
- The tool result is fed back, and the LLM reasons again
- The loop continues until the LLM produces a final text answer (no tool call)

This means the agent can **chain multiple tool calls** in a single turn. When you ask "Why is the server slow?", the LLM might decide to check CPU, then memory, then the top processes — three tool calls, one answer.

---

Next: [Chapter 3 — The Agent](03-the-agent.md)
