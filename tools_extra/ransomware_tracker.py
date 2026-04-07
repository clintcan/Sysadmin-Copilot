"""
Ransomware tracking tools for Sysadmin Copilot.

Monitors ransomware group activity, victim disclosures, IOCs, and
negotiation data using the ransomware.live PRO API.

Requires RANSOMWARE_LIVE_API_KEY environment variable.
Get an API key at: https://my.ransomware.live
"""

import json
import os
import urllib.request
import urllib.parse

from langchain_core.tools import tool


# ─── Helpers ─────────────────────────────────────────────────────────────────

_BASE_URL = "https://api-pro.ransomware.live"


def _api_get(endpoint, params=None, timeout=30):
    """GET request to ransomware.live PRO API. Returns parsed JSON or error dict."""
    api_key = os.environ.get("RANSOMWARE_LIVE_API_KEY")
    if not api_key:
        return {
            "error": (
                "RANSOMWARE_LIVE_API_KEY environment variable is not set.\n"
                "Get an API key at: https://my.ransomware.live"
            )
        }

    if params:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{_BASE_URL}/{endpoint}?{query}" if query else f"{_BASE_URL}/{endpoint}"
    else:
        url = f"{_BASE_URL}/{endpoint}"

    headers = {
        "X-API-KEY": api_key,
        "Accept": "application/json",
        "User-Agent": "SysadminCopilot-RansomwareTracker",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return {"error": "Invalid RANSOMWARE_LIVE_API_KEY."}
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
    name = _safe(v, "post_title", _safe(v, "victim", "Unknown"))
    group = _safe(v, "group_name", _safe(v, "group", "Unknown"))
    discovered = _safe(v, "discovered", "N/A")
    attacked = _safe(v, "attacked", "")
    country = _safe(v, "country", "??")
    activity = _safe(v, "activity", "")
    website = _safe(v, "website", "")

    lines.append(f"  {name}")
    date_str = f"Discovered: {discovered}"
    if attacked and attacked != "N/A":
        date_str += f"  |  Attacked: {attacked}"
    lines.append(f"    Group: {group}  |  Country: {country}")
    lines.append(f"    {date_str}")
    if activity and activity != "N/A":
        lines.append(f"    Sector: {activity}")
    if website and website != "N/A":
        lines.append(f"    Website: {website}")

    # Infostealer info if present
    infostealer = v.get("infostealer")
    if isinstance(infostealer, dict) and infostealer.get("name"):
        lines.append(f"    Infostealer: {infostealer['name']}")

    return "\n".join(lines)


def _check_error(result):
    """Return error string if result is an error, else None."""
    if isinstance(result, dict) and "error" in result:
        return f"[ERROR] {result['error']}"
    return None


# ─── Tools ───────────────────────────────────────────────────────────────────

@tool
def list_ransomware_victims(count: int = 10) -> str:
    """List the most recent ransomware victims.

    Shows organizations recently disclosed on ransomware group leak sites,
    enriched with sector, country, and infostealer data. Use this to stay
    aware of active campaigns and check if organizations in your supply
    chain have been hit.

    Requires RANSOMWARE_LIVE_API_KEY environment variable.

    Args:
        count: Number of victims to show (default 10, max 30).
    """
    count = min(max(count, 1), 30)

    result = _api_get("victims/recent")
    err = _check_error(result)
    if err:
        return err
    if not result or not isinstance(result, list):
        return "No recent victim data available."

    lines = [f"Most recent {min(count, len(result))} ransomware victims:\n"]
    for v in result[:count]:
        lines.append(_format_victim(v))
        lines.append("")

    return "\n".join(lines)


@tool
def search_ransomware_victims(query: str, country: str = None, sector: str = None) -> str:
    """Search for an organization in ransomware victim disclosures.

    Checks if a specific company or organization has been listed as a
    victim by any ransomware group. Use this to check if your organization,
    clients, or supply chain partners have been compromised.

    Requires RANSOMWARE_LIVE_API_KEY environment variable.

    Args:
        query: Organization name or keyword to search (e.g. 'Acme Corp').
        country: Optional 2-letter country code filter (e.g. 'US').
        sector: Optional sector filter (e.g. 'Healthcare').
    """
    params = {"q": query.strip()}
    if country:
        params["country"] = country.strip().upper()
    if sector:
        params["sector"] = sector.strip()

    result = _api_get("victims/search", params)
    err = _check_error(result)
    if err:
        return err
    if result is None or (isinstance(result, list) and len(result) == 0):
        return f"No ransomware victims found matching: {query}"

    victims = result if isinstance(result, list) else []
    lines = [f"Ransomware victim search for: {query}\n"]
    lines.append(f"Found {len(victims)} result(s):\n")
    for v in victims[:20]:
        lines.append(_format_victim(v))
        lines.append("")

    if len(victims) > 20:
        lines.append(f"  ... and {len(victims) - 20} more results")

    return "\n".join(lines)


@tool
def list_ransomware_victims_by_filter(
    country: str = None,
    sector: str = None,
    group: str = None,
    year: str = None,
    month: str = None,
) -> str:
    """List ransomware victims filtered by country, sector, group, or date.

    Flexible query to find victims matching specific criteria. At least
    one filter should be provided. Use list_sectors to find valid sector
    names. Use list_ransomware_groups to find valid group names.

    Requires RANSOMWARE_LIVE_API_KEY environment variable.

    Args:
        country: Two-letter country code (e.g. 'US', 'GB', 'DE').
        sector: Victim sector/industry (e.g. 'Healthcare', 'Education').
        group: Ransomware group name (e.g. 'lockbit', 'alphv').
        year: Four-digit year (e.g. '2024').
        month: Two-digit month (e.g. '03').
    """
    params = {}
    if country:
        params["country"] = country.strip().upper()
    if sector:
        params["sector"] = sector.strip()
    if group:
        params["group"] = group.strip().lower()
    if year:
        params["year"] = year.strip()
    if month:
        params["month"] = month.strip()

    if not params:
        return "[ERROR] Provide at least one filter (country, sector, group, year, or month)."

    result = _api_get("victims/", params)
    err = _check_error(result)
    if err:
        return err
    if not result or not isinstance(result, list):
        filter_desc = ", ".join(f"{k}={v}" for k, v in params.items())
        return f"No ransomware victims found for: {filter_desc}"

    filter_desc = ", ".join(f"{k}={v}" for k, v in params.items())
    lines = [f"Ransomware victims ({filter_desc}):\n"]
    lines.append(f"Total: {len(result)}\n")
    for v in result[:25]:
        lines.append(_format_victim(v))
        lines.append("")

    if len(result) > 25:
        lines.append(f"  ... and {len(result) - 25} more victims")

    return "\n".join(lines)


@tool
def list_ransomware_groups() -> str:
    """List all known ransomware groups with victim counts.

    Shows all ransomware gangs/groups tracked in the database along
    with how many victims each has claimed. Use this to understand
    the threat landscape and identify the most active groups.

    Requires RANSOMWARE_LIVE_API_KEY environment variable.
    """
    result = _api_get("groups")
    err = _check_error(result)
    if err:
        return err
    if not result:
        return "No ransomware group data available."

    groups = result if isinstance(result, list) else []
    if not groups:
        return "No ransomware group data available."

    # Sort by victim count descending if available
    groups.sort(key=lambda g: g.get("victim_count", 0) if isinstance(g, dict) else 0, reverse=True)

    lines = [f"Known ransomware groups ({len(groups)} total):\n"]
    for g in groups:
        if isinstance(g, dict):
            name = _safe(g, "name", "Unknown")
            count = g.get("victim_count", "?")
            lines.append(f"  {name:<30} Victims: {count}")
        else:
            lines.append(f"  {g}")

    return "\n".join(lines)


@tool
def get_ransomware_group_info(group_name: str) -> str:
    """Get details about a specific ransomware group.

    Shows detailed information including TTPs (tactics, techniques,
    procedures), tools used, victim count, activity period, and
    available ransom notes and negotiations.

    Requires RANSOMWARE_LIVE_API_KEY environment variable.

    Args:
        group_name: Name of the ransomware group (e.g. 'lockbit', 'alphv').
    """
    encoded = urllib.parse.quote(group_name.strip().lower())
    result = _api_get(f"group/{encoded}")
    err = _check_error(result)
    if err:
        return err
    if result is None:
        return f"Ransomware group not found: {group_name}"

    # Result may be a list or dict
    g = result[0] if isinstance(result, list) and result else result
    if not isinstance(g, dict):
        return f"Ransomware group not found: {group_name}"

    name = _safe(g, "name", group_name)
    lines = [f"Ransomware group: {name}\n"]

    for field, label in [
        ("description", "Description"),
        ("first_seen", "First seen"),
        ("last_seen", "Last seen"),
        ("victim_count", "Victim count"),
    ]:
        val = _safe(g, field, "")
        if val and val != "N/A":
            if field == "description" and len(str(val)) > 400:
                val = str(val)[:400] + "..."
            lines.append(f"  {label}: {val}")

    # TTPs
    ttps = g.get("ttps")
    if isinstance(ttps, list) and ttps:
        lines.append(f"\n  TTPs ({len(ttps)}):")
        for ttp in ttps[:15]:
            if isinstance(ttp, dict):
                lines.append(f"    {_safe(ttp, 'id', '?')}: {_safe(ttp, 'name', 'Unknown')}")
            else:
                lines.append(f"    {ttp}")

    # Tools
    tools = g.get("tools")
    if isinstance(tools, list) and tools:
        lines.append(f"\n  Tools used: {', '.join(str(t) for t in tools[:20])}")

    # Locations/URLs
    locations = g.get("locations")
    if isinstance(locations, list) and locations:
        lines.append(f"\n  Leak site URLs:")
        for loc in locations[:5]:
            if isinstance(loc, dict):
                slug = loc.get("slug") or loc.get("fqdn", "N/A")
                avail = loc.get("available")
                status = "online" if avail else "offline" if avail is not None else "unknown"
                lines.append(f"    {slug} ({status})")
            else:
                lines.append(f"    {loc}")

    return "\n".join(lines)


@tool
def get_ransomware_iocs(group_name: str = None, ioc_type: str = None) -> str:
    """Get indicators of compromise (IOCs) for ransomware groups.

    Without a group name, lists all groups that have IOCs available.
    With a group name, returns the actual IOC values (hashes, IPs, etc.)
    for that group.

    Requires RANSOMWARE_LIVE_API_KEY environment variable.

    Args:
        group_name: Ransomware group name (omit to list all groups with IOCs).
        ioc_type: Optional IOC type filter (e.g. 'md5', 'sha256', 'ip', 'email', 'btc').
    """
    params = {}
    if ioc_type:
        params["type"] = ioc_type.strip().lower()

    if group_name:
        encoded = urllib.parse.quote(group_name.strip().lower())
        result = _api_get(f"iocs/{encoded}", params if params else None)
    else:
        result = _api_get("iocs", params if params else None)

    err = _check_error(result)
    if err:
        return err
    if result is None or (isinstance(result, list) and len(result) == 0):
        if group_name:
            return f"No IOCs found for group: {group_name}"
        return "No IOC data available."

    if not group_name:
        # Listing groups with IOCs
        lines = ["Ransomware groups with IOCs available:\n"]
        if isinstance(result, list):
            for item in result:
                if isinstance(item, dict):
                    name = _safe(item, "group", _safe(item, "name", "Unknown"))
                    types = item.get("types", item.get("ioc_types", []))
                    count = item.get("count", item.get("ioc_count", "?"))
                    type_str = ", ".join(str(t) for t in types) if isinstance(types, list) else str(types)
                    lines.append(f"  {name:<25} IOCs: {count}  Types: {type_str}")
                else:
                    lines.append(f"  {item}")
        return "\n".join(lines)

    # Specific group IOCs
    lines = [f"IOCs for ransomware group: {group_name}\n"]
    if isinstance(result, list):
        lines.append(f"Total: {len(result)}\n")
        for ioc in result[:50]:
            if isinstance(ioc, dict):
                ioc_val = _safe(ioc, "value", _safe(ioc, "ioc", "N/A"))
                ioc_t = _safe(ioc, "type", _safe(ioc, "ioc_type", "unknown"))
                lines.append(f"  [{ioc_t}] {ioc_val}")
            else:
                lines.append(f"  {ioc}")
        if len(result) > 50:
            lines.append(f"\n  ... and {len(result) - 50} more IOCs")
    elif isinstance(result, dict):
        for ioc_type_key, values in result.items():
            if isinstance(values, list):
                lines.append(f"\n  {ioc_type_key} ({len(values)}):")
                for v in values[:20]:
                    lines.append(f"    {v}")
                if len(values) > 20:
                    lines.append(f"    ... and {len(values) - 20} more")

    return "\n".join(lines)


@tool
def list_sectors() -> str:
    """List all victim sectors/industries tracked in ransomware attacks.

    Shows all unique sectors along with how many victims have been
    reported in each. Useful for understanding which industries are
    most targeted by ransomware groups.

    Requires RANSOMWARE_LIVE_API_KEY environment variable.
    """
    result = _api_get("listsectors")
    err = _check_error(result)
    if err:
        return err
    if not result:
        return "No sector data available."

    lines = ["Ransomware victim sectors:\n"]
    if isinstance(result, list):
        # Sort by count if available
        result.sort(key=lambda s: s.get("count", 0) if isinstance(s, dict) else 0, reverse=True)
        for item in result:
            if isinstance(item, dict):
                sector = _safe(item, "activity", _safe(item, "sector", "Unknown"))
                count = item.get("count", item.get("victim_count", "?"))
                lines.append(f"  {sector:<45} Victims: {count}")
            else:
                lines.append(f"  {item}")

    return "\n".join(lines)


@tool
def get_ransomware_stats() -> str:
    """Get overall ransomware statistics from ransomware.live.

    Returns summary stats including total victim count, number of
    ransomware groups tracked, press entries, and last database update.

    Requires RANSOMWARE_LIVE_API_KEY environment variable.
    """
    result = _api_get("stats")
    err = _check_error(result)
    if err:
        return err
    if not result or not isinstance(result, dict):
        return "No statistics available."

    lines = ["Ransomware.live Statistics:\n"]
    for key, val in result.items():
        label = key.replace("_", " ").title()
        lines.append(f"  {label}: {val}")

    return "\n".join(lines)


@tool
def get_recent_cyberattack_news(country: str = None, count: int = 10) -> str:
    """Get recent cyberattack news articles enriched with ransomware data.

    Shows the latest reported cyberattacks with links to press coverage,
    enriched with ransomware group attribution and infostealer data.

    Requires RANSOMWARE_LIVE_API_KEY environment variable.

    Args:
        country: Optional 2-letter country code filter (e.g. 'US').
        count: Number of articles to show (default 10, max 30).
    """
    count = min(max(count, 1), 30)
    params = {}
    if country:
        params["country"] = country.strip().upper()

    result = _api_get("press/recent", params if params else None)
    err = _check_error(result)
    if err:
        return err
    if not result or not isinstance(result, list):
        return "No recent cyberattack news available."

    lines = [f"Recent cyberattack news:\n"]
    for item in result[:count]:
        if not isinstance(item, dict):
            continue
        title = _safe(item, "title", _safe(item, "post_title", "Untitled"))
        date = _safe(item, "date", _safe(item, "published", "N/A"))
        source = _safe(item, "source", "")
        url = _safe(item, "url", _safe(item, "link", ""))
        country_val = _safe(item, "country", "")

        lines.append(f"  {title}")
        meta = f"    Date: {date}"
        if country_val and country_val != "N/A":
            meta += f"  |  Country: {country_val}"
        if source and source != "N/A":
            meta += f"  |  Source: {source}"
        lines.append(meta)
        if url and url != "N/A":
            lines.append(f"    URL: {url}")
        lines.append("")

    return "\n".join(lines)


WRITE_TOOLS = set()
