from langchain_core.tools import tool
from tools import run_cmd


@tool
def get_weather(location: str = "") -> str:
    """Get the current weather for a location.

    Args:
        location: Name of location, city or country (e.g. 'London', 'Tokyo').
                  If not provided, uses IP-based geolocation.
    """
    if location:
        url = f"wttr.in/{location}?format=3"
    else:
        url = "wttr.in?format=3"
    return run_cmd(["curl", "-s", url], 60)


WRITE_TOOLS = {}
