# Chapter 3 — The Agent (`agent.py`)

## The ReAct Pattern

ReAct stands for **Reason + Act**. It's a prompting strategy where the LLM alternates between two kinds of output:

- **Thought** — "I need to check the service status before deciding whether to restart it"
- **Action** — a tool call with specific arguments

After each action, the tool's output is appended to the conversation and the LLM reasons again. This continues until the LLM produces a final answer with no tool call.

LangChain's `create_react_agent` implements this pattern. You hand it a model, a list of tools, and a system prompt. It handles the loop.

---

## Building the Agent

Here's the agent creation block from `agent.py` (lines 146–160):

```python
    # Initialize LLM and agent
    try:
        llm = get_llm()
    except Exception as e:
        print(f"\033[31mFailed to initialize LLM: {e}\033[0m")
        sys.exit(1)

    # Wrap tools with safety and audit layers
    wrapped_tools = safety.wrap_tools(ALL_TOOLS, audit)

    agent = create_react_agent(
        model=llm,
        tools=wrapped_tools,
        prompt=build_system_prompt(),
    )
```

Three steps:
1. **`get_llm()`** — creates the right LangChain chat model based on `LLM_PROVIDER`
2. **`safety.wrap_tools()`** — wraps each tool with audit logging, blocked-pattern detection, and (for write tools) confirmation prompts
3. **`create_react_agent()`** — wires the model and tools into a LangGraph state machine

Notice that `wrapped_tools` — not `ALL_TOOLS` directly — is passed to the agent. The safety and audit layers are injected at this point, not inside the tool functions themselves. This keeps `tools.py` clean.

---

## System Prompt

The system prompt is generated fresh on every startup (`agent.py:118–135`):

```python
def build_system_prompt():
    uname = os.uname()
    return f"""You are a helpful Linux sysadmin assistant running on this machine.
You help the user investigate system issues, check health, and manage services.

Current time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
Hostname: {uname.nodename}
OS: {uname.sysname} {uname.release}

Guidelines:
- Use the available tools to investigate before answering.
- Always explain what you found in plain, clear language.
- For destructive actions (restart, stop), explain what you're about to do
  and call the tool — the safety layer will handle confirmation.
- If a command fails, explain what went wrong and suggest alternatives.
- Be concise but thorough. Highlight anything unusual or concerning.
- When showing numbers (disk space, memory), use human-readable formats.
"""
```

Including the **hostname and OS version** grounds the LLM. Without this, it might give generic advice that doesn't match the actual system. Including the **current time** helps it interpret time-relative journal queries correctly.

The line "the safety layer will handle confirmation" is important: it tells the LLM not to ask the user for permission itself, but to call the tool directly. The Python code handles the prompt.

---

## Conversation History

The `history` list is the core of multi-turn conversation (`agent.py:164`):

```python
history = []  # accumulates messages across turns for multi-step investigations
```

Each turn, the user's message is appended before calling the agent:

```python
history.append(HumanMessage(content=user_input))
```

After the agent responds, the **full thread** replaces history — not just the final reply, but every message including intermediate tool calls:

```python
history = final_state["messages"]
```

This means on the next turn, the LLM can see:
- What the user asked before
- What tools were called and what they returned
- What the agent said

The `new` command resets history to `[]`, starting a fresh context. The `clear` command only clears the screen; history is preserved.

---

## Streaming

The streaming loop (`agent.py:213–247`) uses LangGraph's dual stream mode:

```python
for mode, data in agent.stream(
    {"messages": history},
    stream_mode=["messages", "values"],
):
    if mode == "messages":
        chunk, metadata = data
        node = metadata.get("langgraph_node", "")

        if node == "tools":
            # A tool just finished — show its name as a progress indicator
            if in_response:
                print("\033[0m", end="")
                in_response = False
            tool_name = getattr(chunk, "name", "tool")
            print(f"\033[90m  [{tool_name}]\033[0m")

        elif node == "agent":
            # Stream final-answer tokens; skip tool-call decision chunks
            if (
                isinstance(chunk.content, str)
                and chunk.content
                and not getattr(chunk, "tool_call_chunks", None)
            ):
                if not in_response:
                    print("\033[97m", end="", flush=True)
                    in_response = True
                print(chunk.content, end="", flush=True)

    elif mode == "values":
        final_state = data
```

Two stream modes work together:

| Mode | What it delivers | Used for |
|------|-----------------|----------|
| `"messages"` | Individual message chunks as they're generated | Streaming tokens to the terminal; showing tool names inline |
| `"values"` | The complete agent state after each node completes | Capturing `final_state["messages"]` to update history |

The `langgraph_node` field tells us which part of the graph generated this chunk:
- `"tools"` — a tool result message; print the tool name in grey
- `"agent"` — an LLM output chunk; if it's a text token (not a tool-call decision), print it

The guard `not getattr(chunk, "tool_call_chunks", None)` is crucial. Without it, the intermediate LLM output where it *decides* to call a tool would also be printed, exposing raw JSON to the user.

---

## Ollama Connectivity Check

Before starting the REPL, the code verifies Ollama is running (`agent.py:78–86`):

```python
        try:
            import urllib.request
            urllib.request.urlopen(f"{base_url}/api/tags", timeout=3)
        except Exception:
            print(f"\033[31mError: Cannot connect to Ollama at {base_url}\033[0m")
            print("Make sure Ollama is running: ollama serve")
            sys.exit(1)

        return ChatOllama(model=model, base_url=base_url, temperature=0)
```

This fails fast with a useful message instead of letting the first `agent.stream()` call fail with an obscure connection error. The `timeout=3` keeps startup snappy even when Ollama is genuinely unavailable.

---

## Built-in Commands

Built-in commands are intercepted before the input is sent to the agent (`agent.py:176–204`). The pattern is a simple `if/elif` chain on the lowercased input:

```python
cmd = user_input.lower()
if cmd in ("quit", "exit", "q"):
    ...
elif cmd == "help":
    print(HELP_TEXT)
    continue
elif cmd == "tools":
    print("\n\033[33mAvailable tools:\033[0m")
    for tool in ALL_TOOLS:
        print(f"  \033[36m{tool.name:<25}\033[0m {tool.description[:70]}")
    print()
    continue
elif cmd == "audit" or cmd.startswith("audit "):
    parts = cmd.split()
    if len(parts) >= 2 and parts[1] == "last":
        count = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        audit.show_last(count)
    else:
        audit.show()
    continue
elif cmd == "new":
    history = []
    ...
```

Using `continue` returns to the `while True` loop's top without touching the agent. This means built-in commands are never sent to the LLM, and they don't affect conversation history.

---

Next: [Chapter 4 — Tools](04-tools.md)
