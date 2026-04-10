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

# History limit is calculated dynamically from the model's context window
# (see _calculate_max_history_chars). Override via MAX_HISTORY_CHARS env var.
MAX_HISTORY_CHARS = None  # set in main() after LLM init

# Known context window sizes (tokens) for common models.
# Used when the provider doesn't report it dynamically.
_CONTEXT_WINDOWS = {
    # Ollama — detected dynamically via /api/show, these are fallbacks
    "llama3.1:8b": 131072, "llama3.1:70b": 131072, "llama3.1:405b": 131072,
    "qwen3.5": 262144, "qwen2.5:7b": 32768, "qwen2.5:14b": 32768,
    "mistral": 32768, "mixtral": 32768, "gemma2": 8192, "phi3": 4096,
    # OpenAI — from developers.openai.com/docs/models (verified 2026-04)
    "gpt-5.4": 1050000, "gpt-5.4-mini": 400000, "gpt-5.4-nano": 400000,
    "gpt-4.1": 1047576, "gpt-4.1-mini": 1047576, "gpt-4.1-nano": 1047576,
    "gpt-4o": 128000, "gpt-4o-mini": 128000, "gpt-4-turbo": 128000,
    "gpt-4": 8192, "gpt-3.5-turbo": 16385,
    "o4-mini": 200000,
    "o3": 200000, "o3-mini": 200000, "o3-pro": 200000,
    "o1": 200000, "o1-mini": 128000, "o1-pro": 200000,
    # Anthropic — detected dynamically via /v1/models, these are fallbacks
    "claude-sonnet-4-20250514": 200000, "claude-opus-4-20250514": 200000,
    "claude-3-5-sonnet-20241022": 200000, "claude-3-haiku-20240307": 200000,
}


def _calculate_max_history_chars(context_window, output_tokens):
    """Calculate max history chars from context window size.

    Budget: context_window - tool_definitions - system_prompt - output_tokens
    Convert remaining tokens to chars (* 4 chars/token estimate).
    """
    tool_tokens = len(ALL_TOOLS) * 200
    system_tokens = 500
    available = context_window - tool_tokens - system_tokens - output_tokens
    # Floor: enough for ~2 turns (one question + one tool result + one answer)
    min_history = min(2048, context_window // 4) * 4
    return max(min_history, available * 4)

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
    """Initialize the LLM based on environment configuration.

    Returns (llm, context_window_tokens, output_tokens) so the caller
    can calculate the appropriate MAX_HISTORY_CHARS.
    """
    import json as _json
    import urllib.request

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
            urllib.request.urlopen(f"{base_url}/api/tags", timeout=3)
        except Exception:
            print(f"\033[31mError: Cannot connect to Ollama at {base_url}\033[0m")
            print("Make sure Ollama is running: ollama serve")
            sys.exit(1)

        max_tokens = int(os.environ.get(
            "MAX_OUTPUT_TOKENS", _MAX_OUTPUT_TOKENS_DEFAULT["ollama"]))

        # Query model info from Ollama: context window, architecture
        model_max_ctx = None
        model_params_b = None
        embedding_dim = None
        num_layers = None
        try:
            req = urllib.request.Request(
                f"{base_url}/api/show",
                data=_json.dumps({"name": model}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                show_data = _json.loads(resp.read())
            model_info = show_data.get("model_info", {})
            for key, val in model_info.items():
                if key.endswith(".context_length") and isinstance(val, int):
                    model_max_ctx = val
                if key.endswith(".embedding_length") and isinstance(val, int) and "vision" not in key:
                    embedding_dim = val
                if key.endswith(".block_count") and isinstance(val, int) and "vision" not in key:
                    num_layers = val
            # Parse parameter size (e.g. "9.7B" -> 9.7)
            param_str = show_data.get("details", {}).get("parameter_size", "")
            if param_str.endswith("B"):
                try:
                    model_params_b = float(param_str[:-1])
                except ValueError:
                    pass
        except Exception:
            pass

        if model_max_ctx is None:
            model_max_ctx = _CONTEXT_WINDOWS.get(model, 8192)

        # Calculate num_ctx: balance context size vs. speed.
        #
        # Constraints:
        #   1. Must fit tools + system prompt + output tokens (minimum)
        #   2. KV cache must fit in RAM (max 40% of total RAM for KV)
        #   3. Don't over-allocate — large num_ctx is slow even if RAM allows it
        #      (every token in the prompt is processed on each turn)
        #   4. Never exceed model's max context
        #
        # Strategy: tools + 8K history room (~5 turns), capped by RAM and model max.
        total_ram_gb = None
        try:
            with open("/proc/meminfo") as f:
                for mem_line in f:
                    if mem_line.startswith("MemTotal:"):
                        total_ram_gb = int(mem_line.split()[1]) / (1024 * 1024)
                        break
        except Exception:
            pass

        tool_tokens = len(ALL_TOOLS) * 200
        target_ctx = tool_tokens + 500 + 8192 + max_tokens  # tools + system + ~5 turns + output

        # RAM cap: limit KV cache to 40% of total RAM (leave room for model + OS)
        ram_max_ctx = model_max_ctx
        if total_ram_gb is not None and embedding_dim and num_layers:
            kv_budget_gb = total_ram_gb * 0.4
            bytes_per_token = 2 * num_layers * embedding_dim * 2
            if kv_budget_gb > 0:
                ram_max_ctx = int((kv_budget_gb * 1024**3) / bytes_per_token)
            else:
                ram_max_ctx = 4096

        num_ctx = min(target_ctx, model_max_ctx, ram_max_ctx)
        # Ensure at least the minimum fits (tools + system + output, no history)
        min_ctx = tool_tokens + 500 + max_tokens
        if num_ctx < min_ctx:
            num_ctx = min(min_ctx, model_max_ctx)  # force tools to fit, up to model max
            if num_ctx > ram_max_ctx:
                print(f"\033[33m  Warning: {len(ALL_TOOLS)} tools need ~{min_ctx:,} tokens "
                      f"but RAM safely allows {ram_max_ctx:,}. May be slow.\n"
                      f"  Consider reducing plugins (unset unused API keys).\033[0m")

        num_ctx = int(os.environ.get("OLLAMA_NUM_CTX", str(num_ctx)))

        history_tokens = max(0, num_ctx - tool_tokens - 500 - max_tokens)
        kv_gb = (num_ctx * 2 * (num_layers or 32) * (embedding_dim or 4096) * 2) / (1024**3)
        print(f"\033[90mContext window: {num_ctx:,} tokens "
              f"(model max: {model_max_ctx:,}, "
              f"KV cache: {kv_gb:.1f} GB, "
              f"history: ~{history_tokens:,} tokens)\033[0m")

        llm = ChatOllama(model=model, base_url=base_url, temperature=0,
                         num_predict=max_tokens, num_ctx=num_ctx)
        return llm, num_ctx, max_tokens

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
        context_window = _CONTEXT_WINDOWS.get(model, 128000)
        llm = ChatOpenAI(model=model, base_url=base_url, temperature=0,
                         max_tokens=max_tokens)
        return llm, context_window, max_tokens

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

        # Query Anthropic API for the model's actual context window
        context_window = _CONTEXT_WINDOWS.get(model, 200000)
        try:
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            req = urllib.request.Request(
                f"https://api.anthropic.com/v1/models/{model}",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                model_data = _json.loads(resp.read())
            api_ctx = model_data.get("max_input_tokens")
            if isinstance(api_ctx, int) and api_ctx > 0:
                context_window = api_ctx
                print(f"\033[90mContext window: {context_window:,} tokens (from API)\033[0m")
        except Exception:
            pass  # fall back to lookup table

        llm = ChatAnthropic(model=model, temperature=0,
                            max_tokens=max_tokens)
        return llm, context_window, max_tokens

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
    global MAX_HISTORY_CHARS
    try:
        llm, context_window, output_tokens = get_llm()
    except Exception as e:
        print(f"\033[31mFailed to initialize LLM: {e}\033[0m")
        sys.exit(1)

    # Calculate history limit from model's context window, or use env override
    env_override = os.environ.get("MAX_HISTORY_CHARS")
    if env_override:
        MAX_HISTORY_CHARS = int(env_override)
    else:
        MAX_HISTORY_CHARS = _calculate_max_history_chars(context_window, output_tokens)
    print(f"\033[90mMax history: {MAX_HISTORY_CHARS:,} chars "
          f"(~{MAX_HISTORY_CHARS // 4:,} tokens from {context_window:,} context)\033[0m")

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
