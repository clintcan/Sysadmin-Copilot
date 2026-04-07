"""
Ransomware tracking tools for Sysadmin Copilot.

Monitors ransomware group activity and victim disclosures using the
ransomware.live API. Use these tools to check if your organization
or sector has been targeted, and to stay aware of active threats.

Completely free — no API key required.
"""

import json
import urllib.request
import urllib.parse

from langchain_core.tools import tool


# ─── Helpers ─────────────────────────────────────────────────────────────────

_BASE_URL = "https://api.ransomware.live/v2"


def _api_get(endpoint, timeout=30):
    """GET request to ransomware.live. Returns parsed JSON or error dict."""
    url = f"{_BASE_URL}/{endpoint}"
    headers = {
        "User-Agent": "SysadminCopilot-RansomwareTracker",
        "Accept": "application/json",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        if e.code == 429:
            return {"error": "Rate limit exceeded. Wait a moment and try again."}
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except urllib.error.URLError as e:
        return {"error": f"Connection failed: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


def _safe(obj, key, default="N/A"):
    """Safely get a value from a dict, returning default if missing or None."""
    if not isinstance(obj, dict):
        return default
    val = obj.get(key)
    return val if val is not None and val != "" else default


def _format_victim(v):
    """Format a single victim record."""
    lines = []
    name = _safe(v, "victim", _safe(v, "post_title", "Unknown"))
    group = _safe(v, "group_name", _safe(v, "group", "Unknown"))
    date = _safe(v, "discovered", _safe(v, "published", "N/A"))
    country = _safe(v, "country", "??")
    activity = _safe(v, "activity", "")
    website = _safe(v, "website", "")

    lines.append(f"  {name}")
    lines.append(f"    Group: {group}  |  Date: {date}  |  Country: {country}")
    if activity and activity != "N/A":
        lines.append(f"    Sector: {activity}")
    if website and website != "N/A":
        lines.append(f"    Website: {website}")
    return "\n".join(lines)


# ─── Tools ───────────────────────────────────────────────────────────────────

@tool
def list_ransomware_victims(count: int = 10) -> str:
    """List the most recent ransomware victims.

    Shows organizations recently disclosed on ransomware group leak sites.
    Use this to stay aware of active campaigns and check if organizations
    in your sector or supply chain have been hit.

    No API key required.

    Args:
        count: Number of victims to show (default 10, max 30).
    """
    count = min(max(count, 1), 30)

    result = _api_get("victims/recent")

    if isinstance(result, dict) and "error" in result:
        return f"[ERROR] {result['error']}"
    if result is None or not result:
        return "No recent victim data available."

    victims = result if isinstance(result, list) else []
    if not victims:
        return "No recent victim data available."

    lines = [f"Most recent {min(count, len(victims))} ransomware victims:\n"]
    for v in victims[:count]:
        lines.append(_format_victim(v))
        lines.append("")

    lines.append(f"Total in response: {len(victims)}")
    return "\n".join(lines)


@tool
def search_ransomware_victims(query: str) -> str:
    """Search for an organization in ransomware victim disclosures.

    Checks if a specific company or organization has been listed as a
    victim by any ransomware group. Use this to check if your organization,
    clients, or supply chain partners have been compromised.

    No API key required.

    Args:
        query: Organization name or keyword to search (e.g. 'Acme Corp').
    """
    encoded = urllib.parse.quote(query.strip())
    result = _api_get(f"victims/search?q={encoded}")

    if isinstance(result, dict) and "error" in result:
        return f"[ERROR] {result['error']}"
    if result is None:
        return f"No ransomware victims found matching: {query}"

    victims = result if isinstance(result, list) else []
    if not victims:
        return f"No ransomware victims found matching: {query}"

    lines = [f"Ransomware victim search for: {query}\n"]
    lines.append(f"Found {len(victims)} result(s):\n")
    for v in victims[:20]:
        lines.append(_format_victim(v))
        lines.append("")

    if len(victims) > 20:
        lines.append(f"  ... and {len(victims) - 20} more results")

    return "\n".join(lines)


@tool
def list_ransomware_groups() -> str:
    """List all known ransomware groups tracked by ransomware.live.

    Shows all ransomware gangs/groups in the database. Use this to
    understand the threat landscape and identify active groups.

    No API key required.
    """
    result = _api_get("groups/all")

    if isinstance(result, dict) and "error" in result:
        return f"[ERROR] {result['error']}"
    if result is None or not result:
        return "No ransomware group data available."

    groups = result if isinstance(result, list) else []
    if not groups:
        return "No ransomware group data available."

    lines = [f"Known ransomware groups ({len(groups)} total):\n"]

    for g in groups:
        name = _safe(g, "name", "Unknown")
        lines.append(f"  {name}")

    return "\n".join(lines)


@tool
def get_ransomware_group_info(group_name: str) -> str:
    """Get details about a specific ransomware group.

    Shows information about a ransomware group including their known
    leak site URLs, description, and victim count.

    No API key required.

    Args:
        group_name: Name of the ransomware group (e.g. 'lockbit', 'alphv').
    """
    encoded = urllib.parse.quote(group_name.strip().lower())
    result = _api_get(f"groups/{encoded}")

    if isinstance(result, dict) and "error" in result:
        return f"[ERROR] {result['error']}"
    if result is None:
        return f"Ransomware group not found: {group_name}"

    # Result may be a list with one item or a dict
    if isinstance(result, list):
        if not result:
            return f"Ransomware group not found: {group_name}"
        g = result[0]
    else:
        g = result

    name = _safe(g, "name", group_name)
    lines = [f"Ransomware group: {name}\n"]

    desc = _safe(g, "description", "")
    if desc and desc != "N/A":
        if len(desc) > 300:
            desc = desc[:300] + "..."
        lines.append(f"  Description: {desc}")

    profile = _safe(g, "profile", [])
    if isinstance(profile, list) and profile:
        lines.append(f"  Profiles/URLs:")
        for p in profile[:5]:
            lines.append(f"    {p}")

    locations = _safe(g, "locations", [])
    if isinstance(locations, list) and locations:
        lines.append(f"  Leak site URLs:")
        for loc in locations[:5]:
            if isinstance(loc, dict):
                url = loc.get("slug") or loc.get("fqdn", "N/A")
                status = loc.get("available")
                status_str = "online" if status else "offline" if status is not None else "unknown"
                lines.append(f"    {url} ({status_str})")
            else:
                lines.append(f"    {loc}")

    return "\n".join(lines)


@tool
def ransomware_victims_by_country(country_code: str) -> str:
    """List ransomware victims in a specific country.

    Shows organizations in a given country that have been targeted by
    ransomware groups. Use ISO 3166-1 alpha-2 country codes.

    No API key required.

    Args:
        country_code: Two-letter country code (e.g. 'US', 'GB', 'DE', 'PH').
    """
    code = country_code.strip().upper()
    if len(code) != 2 or not code.isalpha():
        return f"[ERROR] Invalid country code: {country_code}. Use ISO 2-letter codes (e.g. US, GB, DE)."

    result = _api_get(f"victims/country/{code}")

    if isinstance(result, dict) and "error" in result:
        return f"[ERROR] {result['error']}"
    if result is None:
        return f"No ransomware victims found in country: {code}"

    victims = result if isinstance(result, list) else []
    if not victims:
        return f"No ransomware victims found in country: {code}"

    # Show most recent first
    victims.sort(key=lambda v: v.get("discovered") or v.get("published", ""), reverse=True)

    lines = [f"Ransomware victims in {code} ({len(victims)} total):\n"]
    for v in victims[:20]:
        lines.append(_format_victim(v))
        lines.append("")

    if len(victims) > 20:
        lines.append(f"  ... and {len(victims) - 20} more victims")

    return "\n".join(lines)


WRITE_TOOLS = set()
