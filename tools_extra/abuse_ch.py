"""
abuse.ch threat intelligence tools for Sysadmin Copilot.

Provides malware URL checks (URLhaus), malware hash lookups (MalwareBazaar),
and IOC searches (ThreatFox). All three services are free but require an
auth key.

Requires ABUSECH_AUTH_KEY environment variable.
Get a free key at: https://auth.abuse.ch/
"""

import json
import os
import urllib.request
import urllib.parse

from langchain_core.tools import tool


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _check_auth_key():
    """Return the auth key or None."""
    return os.environ.get("ABUSECH_AUTH_KEY")


def _get_auth_headers():
    """Return auth header dict if ABUSECH_AUTH_KEY is set."""
    key = _check_auth_key()
    if key:
        return {"Auth-Key": key}
    return {}


_AUTH_ERROR = (
    "ABUSECH_AUTH_KEY environment variable is not set.\n"
    "Get a free key at: https://auth.abuse.ch/"
)


def _post_form(url, data, timeout=15):
    """POST with application/x-www-form-urlencoded body. Returns parsed JSON."""
    if not _check_auth_key():
        return {"error": _AUTH_ERROR}
    encoded = urllib.parse.urlencode(data).encode()
    headers = {"User-Agent": "SysadminCopilot-AbuseCH"}
    headers.update(_get_auth_headers())
    req = urllib.request.Request(url, data=encoded, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return {"error": "Invalid ABUSECH_AUTH_KEY."}
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except urllib.error.URLError as e:
        return {"error": f"Connection failed: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


def _post_json(url, payload, timeout=15):
    """POST with application/json body. Returns parsed JSON."""
    if not _check_auth_key():
        return {"error": _AUTH_ERROR}
    data = json.dumps(payload).encode()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "SysadminCopilot-AbuseCH",
    }
    headers.update(_get_auth_headers())
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return {"error": "Invalid ABUSECH_AUTH_KEY."}
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except urllib.error.URLError as e:
        return {"error": f"Connection failed: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


# ═════════════════════════════════════════════════════════════════════════════
# URLhaus — Malware URL Database
# ═════════════════════════════════════════════════════════════════════════════

@tool
def check_url_malware(url: str) -> str:
    """Check if a URL is known to distribute malware.

    Queries the URLhaus database to see if a URL has been flagged for
    distributing malware, phishing, or other threats. Use this when you
    find suspicious URLs in logs or emails.

    Requires ABUSECH_AUTH_KEY environment variable (free key from https://auth.abuse.ch/).

    Args:
        url: The URL to check (e.g. 'http://evil.com/payload.exe').
    """
    result = _post_form(
        "https://urlhaus-api.abuse.ch/v1/url/",
        {"url": url},
    )

    if "error" in result:
        return f"[ERROR] {result['error']}"

    status = result.get("query_status", "")
    if status == "no_results":
        return f"URL not found in URLhaus database: {url}\nThis URL has not been reported as malicious."
    if status == "invalid_url":
        return f"[ERROR] Invalid URL format: {url}"
    if status != "ok":
        return f"[ERROR] Unexpected response: {status}"

    lines = [f"URLhaus report for: {url}\n"]
    lines.append(f"  Status:       {result.get('url_status', 'unknown')}")
    lines.append(f"  Threat:       {result.get('threat', 'N/A')}")
    lines.append(f"  Date added:   {result.get('date_added', 'N/A')}")

    blacklists = result.get("blacklists", {})
    if blacklists:
        bl_items = [f"{k}: {v}" for k, v in blacklists.items() if v]
        if bl_items:
            lines.append(f"  Blacklists:   {', '.join(bl_items)}")

    tags = result.get("tags")
    if tags:
        lines.append(f"  Tags:         {', '.join(tags)}")

    payloads = result.get("payloads")
    if payloads:
        lines.append(f"\n  Payloads ({len(payloads)}):")
        for p in payloads[:10]:
            fname = p.get("filename") or "(unnamed)"
            ftype = p.get("file_type") or "unknown"
            sha256 = p.get("response_sha256") or p.get("sha256_hash", "N/A")
            sig = p.get("signature") or "unknown"
            lines.append(f"    {fname} ({ftype})")
            lines.append(f"      SHA256:    {sha256}")
            lines.append(f"      Signature: {sig}")
        if len(payloads) > 10:
            lines.append(f"    ... and {len(payloads) - 10} more payloads")

    return "\n".join(lines)


@tool
def check_host_malware(host: str) -> str:
    """Check if a host or IP is associated with malware distribution.

    Queries URLhaus to see if a hostname or IP address has been used to
    host malicious URLs. Use this to check suspicious IPs found in your
    server logs or network connections.

    Requires ABUSECH_AUTH_KEY environment variable (free key from https://auth.abuse.ch/).

    Args:
        host: Hostname or IP address (e.g. 'evil.com' or '192.168.1.100').
    """
    result = _post_form(
        "https://urlhaus-api.abuse.ch/v1/host/",
        {"host": host},
    )

    if "error" in result:
        return f"[ERROR] {result['error']}"

    status = result.get("query_status", "")
    if status == "no_results":
        return f"Host not found in URLhaus: {host}\nNo malware URLs associated with this host."
    if status != "ok":
        return f"[ERROR] Unexpected response: {status}"

    url_count = result.get("url_count", 0)
    urls_online = result.get("urls_online", 0)

    lines = [f"URLhaus report for host: {host}\n"]
    lines.append(f"  Total malware URLs: {url_count}")
    lines.append(f"  Currently online:   {urls_online}")

    blacklists = result.get("blacklists", {})
    if blacklists:
        bl_items = [f"{k}: {v}" for k, v in blacklists.items() if v]
        if bl_items:
            lines.append(f"  Blacklists:         {', '.join(bl_items)}")

    urls = result.get("urls", [])
    if urls:
        lines.append(f"\n  Recent malware URLs:")
        for u in urls[:20]:
            url_val = u.get("url", "N/A")
            url_status = u.get("url_status", "unknown")
            threat = u.get("threat", "N/A")
            date = u.get("date_added", "N/A")
            lines.append(f"    [{url_status}] {url_val}")
            lines.append(f"      Threat: {threat}  |  Added: {date}")
        if len(urls) > 20:
            lines.append(f"    ... and {len(urls) - 20} more URLs")

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# MalwareBazaar — Malware Sample Database
# ═════════════════════════════════════════════════════════════════════════════

@tool
def lookup_malware_hash(file_hash: str) -> str:
    """Look up a file hash in the MalwareBazaar malware database.

    Checks if a file hash (MD5, SHA1, or SHA256) is a known malware sample.
    Use this after extracting hashes from suspicious files, IOCs, or alerts.
    Returns malware family, file type, tags, and detection stats.

    Requires ABUSECH_AUTH_KEY environment variable (free key from https://auth.abuse.ch/).

    Args:
        file_hash: MD5, SHA1, or SHA256 hash to look up.
    """
    result = _post_form(
        "https://mb-api.abuse.ch/api/v1/",
        {"query": "get_info", "hash": file_hash.strip()},
    )

    if "error" in result:
        return f"[ERROR] {result['error']}"

    status = result.get("query_status", "")
    if status == "hash_not_found":
        return f"Hash not found in MalwareBazaar: {file_hash}\nThis file is not in the malware database (doesn't mean it's safe)."
    if status == "illegal_hash":
        return f"[ERROR] Invalid hash format: {file_hash}"
    if status != "ok":
        return f"[ERROR] Unexpected response: {status}"

    data = result.get("data", [])
    if not data:
        return f"No data returned for hash: {file_hash}"

    d = data[0]
    lines = [f"MalwareBazaar report:\n"]
    lines.append(f"  File name:    {d.get('file_name', 'N/A')}")
    lines.append(f"  File type:    {d.get('file_type_mime', 'N/A')} ({d.get('file_type', 'N/A')})")
    lines.append(f"  File size:    {d.get('file_size', 'N/A')} bytes")
    lines.append(f"  SHA256:       {d.get('sha256_hash', 'N/A')}")
    lines.append(f"  MD5:          {d.get('md5_hash', 'N/A')}")
    lines.append(f"  Signature:    {d.get('signature') or 'Unknown'}")
    lines.append(f"  First seen:   {d.get('first_seen', 'N/A')}")
    lines.append(f"  Last seen:    {d.get('last_seen', 'N/A')}")

    tags = d.get("tags")
    if tags:
        lines.append(f"  Tags:         {', '.join(tags)}")

    # Detection stats from vendor intelligence
    vendor = d.get("vendor_intel", {})
    if vendor:
        detections = []
        for v_name, v_data in vendor.items():
            if isinstance(v_data, dict):
                verdict = v_data.get("verdict") or v_data.get("malware_family") or v_data.get("detection")
                if verdict:
                    detections.append(f"{v_name}: {verdict}")
        if detections:
            lines.append(f"\n  Vendor detections:")
            for det in detections[:10]:
                lines.append(f"    {det}")

    return "\n".join(lines)


@tool
def recent_malware_samples(limit: int = 10) -> str:
    """Show recently submitted malware samples from MalwareBazaar.

    Lists the latest malware samples added to the database. Useful for
    tracking emerging threats and new malware families.

    Requires ABUSECH_AUTH_KEY environment variable (free key from https://auth.abuse.ch/).

    Args:
        limit: Number of samples to show (default 10, max 30).
    """
    limit = min(max(limit, 1), 30)

    result = _post_form(
        "https://mb-api.abuse.ch/api/v1/",
        {"query": "get_recent", "selector": "100"},
    )

    if "error" in result:
        return f"[ERROR] {result['error']}"

    status = result.get("query_status", "")
    if status != "ok":
        return f"[ERROR] Unexpected response: {status}"

    data = result.get("data", [])
    if not data:
        return "No recent malware samples available."

    lines = [f"Most recent {limit} malware samples:\n"]
    for d in data[:limit]:
        name = d.get("file_name") or "(unnamed)"
        sig = d.get("signature") or "unknown"
        ftype = d.get("file_type") or "unknown"
        sha256 = d.get("sha256_hash", "N/A")
        first = d.get("first_seen", "N/A")
        tags = ", ".join(d.get("tags") or []) or "none"
        lines.append(f"  {name}")
        lines.append(f"    Signature: {sig}  |  Type: {ftype}")
        lines.append(f"    SHA256:    {sha256}")
        lines.append(f"    First seen: {first}  |  Tags: {tags}")
        lines.append("")

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# ThreatFox — IOC Database
# ═════════════════════════════════════════════════════════════════════════════

def _format_threatfox_ioc(ioc):
    """Format a single ThreatFox IOC record."""
    lines = []
    lines.append(f"  {ioc.get('ioc', 'N/A')}")
    lines.append(f"    Threat type:  {ioc.get('threat_type_desc') or ioc.get('threat_type', 'N/A')}")
    lines.append(f"    Malware:      {ioc.get('malware_printable') or ioc.get('malware', 'N/A')}")
    lines.append(f"    Confidence:   {ioc.get('confidence_level', 'N/A')}%")
    lines.append(f"    First seen:   {ioc.get('first_seen', 'N/A')}")
    lines.append(f"    Last seen:    {ioc.get('last_seen', 'N/A')}")
    tags = ioc.get("tags")
    if tags:
        lines.append(f"    Tags:         {', '.join(tags)}")
    ref = ioc.get("reference")
    if ref:
        lines.append(f"    Reference:    {ref}")
    return "\n".join(lines)


@tool
def search_threat_ioc(search_term: str) -> str:
    """Search ThreatFox for an indicator of compromise (IOC).

    Checks if an IP address, domain, URL, or hash is associated with
    known malware command-and-control servers, payload delivery, or
    other threats. Use this to investigate suspicious IOCs found in
    your logs or alerts.

    Requires ABUSECH_AUTH_KEY environment variable (free key from https://auth.abuse.ch/).

    Args:
        search_term: IP, domain, URL, or hash to search for.
    """
    result = _post_json(
        "https://threatfox-api.abuse.ch/api/v1/",
        {"query": "search_ioc", "search_term": search_term.strip()},
    )

    if "error" in result:
        return f"[ERROR] {result['error']}"

    status = result.get("query_status", "")
    if status == "no_result":
        return f"IOC not found in ThreatFox: {search_term}\nNo known threats associated with this indicator."
    if status == "illegal_search_term":
        return f"[ERROR] Invalid search term: {search_term}"
    if status != "ok":
        return f"[ERROR] Unexpected response: {status}"

    data = result.get("data", [])
    if not data:
        return f"No IOC data returned for: {search_term}"

    lines = [f"ThreatFox results for: {search_term}\n"]
    lines.append(f"Found {len(data)} IOC(s):\n")
    for ioc in data[:15]:
        lines.append(_format_threatfox_ioc(ioc))
        lines.append("")

    if len(data) > 15:
        lines.append(f"  ... and {len(data) - 15} more IOCs")

    return "\n".join(lines)


@tool
def recent_threat_iocs(days: int = 1, limit: int = 15) -> str:
    """Show recently reported IOCs from ThreatFox.

    Lists the latest indicators of compromise (C2 servers, payload URLs,
    malware hashes) reported to ThreatFox. Useful for proactive threat
    hunting and updating blocklists.

    Requires ABUSECH_AUTH_KEY environment variable (free key from https://auth.abuse.ch/).

    Args:
        days: Number of days to look back (1-7, default 1).
        limit: Max IOCs to display (default 15, max 30).
    """
    days = min(max(days, 1), 7)
    limit = min(max(limit, 1), 30)

    result = _post_json(
        "https://threatfox-api.abuse.ch/api/v1/",
        {"query": "get_iocs", "days": days},
    )

    if "error" in result:
        return f"[ERROR] {result['error']}"

    status = result.get("query_status", "")
    if status == "no_result":
        return f"No IOCs reported in the last {days} day(s)."
    if status != "ok":
        return f"[ERROR] Unexpected response: {status}"

    data = result.get("data", [])
    if not data:
        return f"No IOC data available for the last {days} day(s)."

    lines = [f"Recent IOCs from ThreatFox (last {days} day(s)):\n"]
    lines.append(f"Total available: {len(data)}  |  Showing: {min(limit, len(data))}\n")
    for ioc in data[:limit]:
        lines.append(_format_threatfox_ioc(ioc))
        lines.append("")

    if len(data) > limit:
        lines.append(f"  ... and {len(data) - limit} more IOCs")

    return "\n".join(lines)


WRITE_TOOLS = set()
