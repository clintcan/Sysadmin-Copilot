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
import sys
import readline  # noqa: F401 — enables arrow keys in input()
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from tools import ALL_TOOLS
from safety import SafetyLayer
from audit import AuditLogger

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
  help          Show this help message
  tools         List all available agent tools
  audit         Show the audit log for this session
  clear         Clear the screen
  quit / exit   Exit the copilot
"""


# ─── LLM Setup ────────────────────────────────────────────────────────────────

def get_llm():
    """Initialize the LLM based on environment configuration."""
    provider = os.environ.get("LLM_PROVIDER", "ollama").lower()

    if provider == "ollama":
        try:
            from langchain_ollama import ChatOllama
        except ImportError:
            print("\033[31mError: pip install langchain-ollama\033[0m")
            sys.exit(1)

        model = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        print(f"\033[90mUsing Ollama ({model}) at {base_url}\033[0m")
        return ChatOllama(model=model, base_url=base_url, temperature=0)

    elif provider == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            print("\033[31mError: pip install langchain-openai\033[0m")
            sys.exit(1)

        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        print(f"\033[90mUsing OpenAI ({model})\033[0m")
        return ChatOpenAI(model=model, temperature=0)

    elif provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError:
            print("\033[31mError: pip install langchain-anthropic\033[0m")
            sys.exit(1)

        model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
        print(f"\033[90mUsing Anthropic ({model})\033[0m")
        return ChatAnthropic(model=model, temperature=0)

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
- Always explain what you found in plain, clear language.
- For destructive actions (restart, stop), explain what you're about to do
  and call the tool — the safety layer will handle confirmation.
- If a command fails, explain what went wrong and suggest alternatives.
- Be concise but thorough. Highlight anything unusual or concerning.
- When showing numbers (disk space, memory), use human-readable formats.
"""


# ─── Main Loop ────────────────────────────────────────────────────────────────

def main():
    print(BANNER)

    audit = AuditLogger()
    safety = SafetyLayer()

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

    print()

    while True:
        try:
            user_input = input("\033[32m❯ \033[0m").strip()
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
        elif cmd == "audit":
            audit.show()
            continue
        elif cmd == "clear":
            os.system("clear")
            continue

        # Run the agent
        try:
            result = agent.invoke(
                {"messages": [HumanMessage(content=user_input)]}
            )

            # Extract the final response
            final_message = result["messages"][-1]
            print(f"\n\033[97m{final_message.content}\033[0m\n")

            # Log the interaction
            audit.log_interaction(user_input, final_message.content)

        except KeyboardInterrupt:
            print("\n\033[33mInterrupted. Ready for next question.\033[0m\n")
        except Exception as e:
            print(f"\n\033[31mError: {e}\033[0m\n")


if __name__ == "__main__":
    main()
