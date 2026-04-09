#!/usr/bin/env python3
"""
Sysadmin Copilot — An AI-powered Linux system administration assistant.

Talk to your server in natural language. The agent uses LangChain's ReAct
pattern to select the right Linux tools, run them, interpret the output,
and respond in plain English.

Usage:
    # With Ollama (local, self-hosted)
    python agent.py

    # With OpenAI-compatible API
    LLM_PROVIDER=openai OPENAI_API_KEY=sk-... python agent.py

    # With Anthropic
    LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=sk-ant-... python agent.py
"""

import os
import pwd
import subprocess
import sys
import termios
import readline  # noqa: F401 — enables arrow keys in input()
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()  # loads .env if present; does not override existing env vars

from langchain_core.messages import HumanMessage
from langchain.agents import create_agent

from tools import ALL_TOOLS, EXTRA_WRITE_TOOLS
from safety import SafetyLayer
import safety as safety_module
from audit import AuditLogger

# History is trimmed before each agent call if it exceeds this character count.
# Roughly 25k tokens at 4 chars/token — well under all supported model limits.
# Override via MAX_HISTORY_CHARS env var if you use a model with a smaller window.
MAX_HISTORY_CHARS = int(os.environ.get("MAX_HISTORY_CHARS", "100000"))

# Maximum output tokens per provider. Ollama defaults are often too low (2048),
# causing responses to cut off mid-sentence when summarizing large tool output.
# Cloud providers have generous defaults — we leave them alone unless overridden.
# Override any provider via MAX_OUTPUT_TOKENS env var.
_MAX_OUTPUT_TOKENS_DEFAULT = {
    "ollama": 4096,
    "openai": 16384,
    "anthropic": 8192,
}


def _count_history_chars(history: list) -> int:
    """Estimate conversation history size in characters.

    Handles both plain string content and Anthropic's list-of-blocks format
    so the check works correctly across all three LLM providers.
    """
    total = 0
    for msg in history:
        if isinstance(msg.content, str):
            total += len(msg.content)
        elif isinstance(msg.content, list):
            for block in msg.content:
                if isinstance(block, dict):
                    total += len(block.get("text", ""))
    return total

# ─── Banner ───────────────────────────────────────────────────────────────────

BANNER = """
\033[32m╔══════════════════════════════════════════════════════════╗
║           🖥️  Sysadmin Copilot v0.1                      ║
║           AI-Powered Linux System Administration         ║
╚══════════════════════════════════════════════════════════╝\033[0m

Type your questions in natural language. Examples:
  • "Why is the server running slow?"
  • "Show me failed SSH login attempts in the last hour"
  • "How much disk space is left?"
  • "Restart nginx and check if it's healthy"

Type \033[33mhelp\033[0m for more commands, \033[33mquit\033[0m to exit.
"""

HELP_TEXT = """
\033[33mAvailable commands:\033[0m
  help               Show this help message
  tools              List all available agent tools
  audit              Show the audit log for this session
  audit last [N]     Show the last N past session(s) (default 1)
  raw                Toggle raw output mode (show tool output without LLM summary)
  new                Start a fresh conversation (clear history)
  clear              Clear the screen
  quit / exit        Exit the copilot
"""


# ─── LLM Setup ────────────────────────────────────────────────────────────────

def get_llm():
    """Initialize the LLM based on environment configuration."""
    provider = os.environ.get("LLM_PROVIDER", "ollama").lower()

    if provider == "ollama":
        try:
            from langchain_ollama import ChatOllama
        except ImportError:
            print("\033[33mInstalling langchain-ollama...\033[0m")
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", "langchain-ollama"])
                from langchain_ollama import ChatOllama
            except (subprocess.CalledProcessError, ImportError) as e:
                print(f"\033[31mFailed to install langchain-ollama: {e}\033[0m")
                sys.exit(1)

        model = os.environ.get("OLLAMA_MODEL", "qwen3.5")
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        print(f"\033[90mUsing Ollama ({model}) at {base_url}\033[0m")

        try:
            import urllib.request
            urllib.request.urlopen(f"{base_url}/api/tags", timeout=3)
        except Exception:
            print(f"\033[31mError: Cannot connect to Ollama at {base_url}\033[0m")
            print("Make sure Ollama is running: ollama serve")
            sys.exit(1)

        # Estimate context needed: tool definitions + system prompt + history room.
        # Each tool's name + docstring + schema averages ~200 tokens.
        # Add room for system prompt (~500 tokens) and conversation history.
        max_tokens = int(os.environ.get(
            "MAX_OUTPUT_TOKENS", _MAX_OUTPUT_TOKENS_DEFAULT["ollama"]))

        # Estimate context needed: tool definitions + system prompt + history room.
        tool_tokens = len(ALL_TOOLS) * 200
        system_tokens = 500
        history_room = 4096
        min_ctx = tool_tokens + system_tokens + history_room + max_tokens
        num_ctx = max(8192, ((min_ctx + 1023) // 1024) * 1024)
        num_ctx = int(os.environ.get("OLLAMA_NUM_CTX", str(num_ctx)))

        if num_ctx > 8192:
            print(f"\033[90mContext window: {num_ctx} tokens "
                  f"({len(ALL_TOOLS)} tools need ~{tool_tokens} tokens)\033[0m")

        return ChatOllama(model=model, base_url=base_url, temperature=0,
                         num_predict=max_tokens, num_ctx=num_ctx)

    elif provider == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            print("\033[33mInstalling langchain-openai...\033[0m")
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", "langchain-openai"])
                from langchain_openai import ChatOpenAI
            except (subprocess.CalledProcessError, ImportError) as e:
                print(f"\033[31mFailed to install langchain-openai: {e}\033[0m")
                sys.exit(1)

        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        base_url = os.environ.get("OPENAI_BASE_URL")
        if base_url:
            print(f"\033[90mUsing OpenAI-compatible ({model}) at {base_url}\033[0m")
        else:
            print(f"\033[90mUsing OpenAI ({model})\033[0m")
        max_tokens = int(os.environ.get(
            "MAX_OUTPUT_TOKENS", _MAX_OUTPUT_TOKENS_DEFAULT["openai"]))
        return ChatOpenAI(model=model, base_url=base_url, temperature=0,
                         max_tokens=max_tokens)

    elif provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError:
            print("\033[33mInstalling langchain-anthropic...\033[0m")
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", "langchain-anthropic"])
                from langchain_anthropic import ChatAnthropic
            except (subprocess.CalledProcessError, ImportError) as e:
                print(f"\033[31mFailed to install langchain-anthropic: {e}\033[0m")
                sys.exit(1)

        model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
        print(f"\033[90mUsing Anthropic ({model})\033[0m")
        max_tokens = int(os.environ.get(
            "MAX_OUTPUT_TOKENS", _MAX_OUTPUT_TOKENS_DEFAULT["anthropic"]))
        return ChatAnthropic(model=model, temperature=0,
                            max_tokens=max_tokens)

    else:
        print(f"\033[31mUnknown LLM_PROVIDER: {provider}\033[0m")
        print("Supported: ollama, openai, anthropic")
        sys.exit(1)


# ─── System Prompt ────────────────────────────────────────────────────────────

def build_system_prompt():
    uname = os.uname()
    return f"""You are a helpful Linux sysadmin assistant running on this machine.
You help the user investigate system issues, check health, and manage services.

Current time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
Hostname: {uname.nodename}
OS: {uname.sysname} {uname.release}

Guidelines:
- Use the available tools to investigate before answering.
- IMPORTANT: Only report information that comes from tool output. Never
  guess or make up file listings, log entries, or command results. If a
  tool returned data, quote it accurately. If you have not run a tool yet,
  run one first — do not fabricate output.
- Always explain what you found in plain, clear language.
- For destructive actions (restart, stop), explain what you're about to do
  and call the tool — the safety layer will handle confirmation.
- If a command fails, explain what went wrong and suggest alternatives.
- Be concise but thorough. Highlight anything unusual or concerning.
- When showing numbers (disk space, memory), use human-readable formats.
- IMPORTANT: Always prefer a specific tool over run_command. Only use
  run_command as a LAST RESORT when no dedicated tool covers the task.
  For example, use check_disk_usage instead of run_command("df -h").
- To change the working directory, use the change_directory tool.
  Do NOT run 'cd' inside run_command — it only affects that single call.
- Do NOT suggest commands for the user to run manually — use tools instead.
- IMPORTANT: Always call tools directly. Never write tool calls as JSON text
  in your response — use the actual tool-calling mechanism instead.
"""


# ─── Main Loop ────────────────────────────────────────────────────────────────

def main():
    # Save terminal state at startup so we can restore it before each prompt.
    # Streaming and subprocess calls can corrupt terminal settings, causing
    # invisible typed text and broken keyboard mappings.
    try:
        _saved_term_attrs = termios.tcgetattr(sys.stdin)
    except (termios.error, ValueError):
        _saved_term_attrs = None  # not a real terminal (piped input, etc.)

    print(BANNER)

    # We will get the current user and change directory there if it is sysadmin-copilot
    username = pwd.getpwuid(os.getuid())[0]

    # check if username is sysadmin-copilot
    if username == 'sysadmin-copilot':
        print("Changing home directory to sysadmin-copilot")
        os.chdir("/home/sysadmin-copilot")

    audit = AuditLogger()
    safety = SafetyLayer()

    # Initialize LLM and agent
    try:
        llm = get_llm()
    except Exception as e:
        print(f"\033[31mFailed to initialize LLM: {e}\033[0m")
        sys.exit(1)

    # Merge any write-tool declarations from plugins into the safety layer
    safety_module.WRITE_TOOLS |= EXTRA_WRITE_TOOLS

    # Wrap tools with safety and audit layers
    wrapped_tools = safety.wrap_tools(ALL_TOOLS, audit)

    agent = create_agent(
        model=llm,
        tools=wrapped_tools,
        system_prompt=build_system_prompt(),
    )

    print()

    history = []  # accumulates messages across turns for multi-step investigations
    raw_mode = False  # when True, print raw tool output and skip LLM summary

    while True:
        try:
            # get current directory
            current_directory = os.getcwd()
            # Restore terminal to saved state before prompting.
            # Streaming and subprocesses can corrupt terminal settings,
            # causing invisible text and broken keyboard mappings.
            if _saved_term_attrs:
                try:
                    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, _saved_term_attrs)
                except (termios.error, ValueError):
                    pass
            sys.stdout.write("\033[0m")
            sys.stdout.flush()
            prompt_prefix = "\033[33m[RAW]\033[0m " if raw_mode else ""
            user_input = input(prompt_prefix + current_directory + " ❯ ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\033[90mGoodbye!\033[0m")
            break

        if not user_input:
            continue

        # Handle built-in commands
        cmd = user_input.lower()
        if cmd in ("quit", "exit", "q"):
            print("\033[90mGoodbye!\033[0m")
            break
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
        elif cmd == "raw":
            raw_mode = not raw_mode
            state = "ON" if raw_mode else "OFF"
            print(f"\033[90mRaw output mode: {state}\033[0m\n")
            continue
        elif cmd == "new":
            history = []
            print("\033[90mConversation history cleared.\033[0m\n")
            continue
        elif cmd == "clear":
            os.system("clear")
            continue

        # Run the agent
        history.append(HumanMessage(content=user_input))

        # Guard against context overflow before sending to the LLM
        total_chars = _count_history_chars(history)
        if total_chars > MAX_HISTORY_CHARS:
            history.pop()
            print(
                f"\n\033[33mConversation history is too long "
                f"({total_chars:,} chars, limit {MAX_HISTORY_CHARS:,}).\033[0m"
            )
            print("\033[33mType \033[1mnew\033[0m\033[33m to start a fresh conversation.\033[0m\n")
            continue

        try:
            print()
            final_state = None
            in_response = False
            tool_was_called = False

            stream = agent.stream(
                {"messages": history},
                stream_mode=["messages", "values"],
            )

            for mode, data in stream:
                if mode == "messages":
                    chunk, metadata = data
                    node = metadata.get("langgraph_node", "")

                    if node == "tools":
                        # A tool just finished — show its name
                        if in_response:
                            print("\033[0m", end="")
                            in_response = False
                        tool_name = getattr(chunk, "name", "tool")
                        print(f"\033[90m  [{tool_name}]\033[0m")
                        tool_was_called = True

                        # In raw mode, print the tool's actual output
                        if raw_mode:
                            content = chunk.content if hasattr(chunk, "content") else ""
                            if isinstance(content, str) and content:
                                print(content)

                    elif node == "model":
                        # In raw mode, skip the LLM summary if a tool was called.
                        # If no tool was called, show the LLM response anyway
                        # (e.g. the user asked a general question).
                        if raw_mode and tool_was_called:
                            continue
                        # Stream final-answer tokens; skip tool-call decision chunks
                        if (
                            isinstance(chunk.content, str)
                            and chunk.content
                            and not getattr(chunk, "tool_call_chunks", None)
                        ):
                            if not in_response:
                                # Use default terminal color (not bright white,
                                # which is invisible on light backgrounds)
                                print("\033[0m", end="", flush=True)
                                in_response = True
                            print(chunk.content, end="", flush=True)

                elif mode == "values":
                    final_state = data
                    # In raw mode, drain the rest of the stream in a background
                    # thread so the prompt returns immediately. Without this,
                    # closing the generator blocks while the LLM finishes
                    # generating its unused summary.
                    if raw_mode and tool_was_called:
                        import threading
                        def _drain(s):
                            try:
                                for _ in s:
                                    pass
                            except Exception:
                                pass
                        threading.Thread(target=_drain, args=(stream,), daemon=True).start()
                        break

            # Always reset terminal colors after streaming.
            # Use sys.stdout.write + flush to guarantee the reset
            # reaches the terminal before the next input() prompt.
            sys.stdout.write("\033[0m\n")
            sys.stdout.flush()

            # Persist the full thread (user + tool calls + assistant reply)
            history = final_state["messages"]

            # Log the interaction — guard against Anthropic's list-of-blocks content format
            last_content = history[-1].content
            if isinstance(last_content, list):
                last_content = " ".join(
                    b.get("text", "") for b in last_content if isinstance(b, dict)
                )
            elif not isinstance(last_content, str):
                last_content = "" if last_content is None else str(last_content)
            audit.log_interaction(user_input, last_content)

        except KeyboardInterrupt:
            sys.stdout.write("\033[0m\n")
            sys.stdout.flush()
            history.pop()  # discard the unanswered user message
            print("\033[33mInterrupted. Ready for next question.\033[0m\n")
        except Exception as e:
            sys.stdout.write("\033[0m\n")
            sys.stdout.flush()
            history.pop()  # discard the unanswered user message
            err_str = str(e).lower()
            if any(kw in err_str for kw in (
                "context", "token", "length", "maximum", "too long", "limit exceeded"
            )):
                print("\n\033[31mError: conversation history is too long for this model.\033[0m")
                print("\033[33mType \033[1mnew\033[0m\033[33m to start a fresh conversation.\033[0m\n")
            else:
                print(f"\n\033[31mError: {e}\033[0m\n")


if __name__ == "__main__":
    main()
