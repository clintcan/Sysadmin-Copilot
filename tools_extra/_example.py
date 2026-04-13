"""
Example plugin for Sysadmin Copilot — tools_extra/

Drop .py files into this directory to add custom tools at startup. The loader
auto-discovers any @tool-decorated functions and registers them with the agent.

Rules:
  - Files must be .py, .pyc, or .so and NOT start with '_' (this file is skipped)
  - Each @tool function becomes available to the agent automatically
  - Import run_cmd from tools if you need subprocess execution
  - A single file can contain any mix of read and write tools
  - Declare WRITE_TOOLS = {"tool_name", ...} for tools that need
    user confirmation before running

For distributing plugins without source, see "Distributing compiled plugins"
in docs/08-extending.md.

Rename this file to example.py (remove the leading underscore) to activate it.
"""

from langchain_core.tools import tool
from tools import run_cmd


@tool
def check_docker_containers(all: bool = False) -> str:
    """List Docker containers, optionally including stopped ones.

    Args:
        all: If True, show all containers including stopped.
    """
    cmd = ["docker", "ps"]
    if all:
        cmd.append("-a")
    return run_cmd(cmd)


@tool
def check_docker_images() -> str:
    """List locally available Docker images."""
    return run_cmd(["docker", "images"])


@tool
def restart_container(name: str) -> str:
    """Restart a Docker container. REQUIRES CONFIRMATION.

    Args:
        name: Container name or ID to restart.
    """
    return run_cmd(["docker", "restart", name])


# Declare write tools that require user confirmation.
# Any tool name listed here will get a confirmation prompt before executing.
# Tools NOT listed here (check_docker_containers, check_docker_images) are
# treated as read-only and run without prompting.
# Leave empty (or omit entirely) if all tools in this file are read-only.
WRITE_TOOLS = {"restart_container"}
