"""
AbuseIPDB tools for Sysadmin Copilot.

Checks IP address reputation against the AbuseIPDB crowd-sourced database
of reported malicious IPs. Useful for investigating suspicious connections,
brute-force sources, and scanning activity in your server logs.

Requires ABUSEIPDB_API_KEY environment variable.
Get a free key at: https://www.abuseipdb.com/account/api
Free tier: 1,000 checks/day.
"""

import json
import os
import urllib.request
import urllib.parse

from langchain_core.tools import tool


# ─── Helpers ─────────────────────────────────────────────────────────────────

_BASE_URL = "https://api.abuseipdb.com/api/v2"

_ABUSE_CATEGORIES = {
    1: "DNS Compromise",
    2: "DNS Poisoning",
    3: "Fraud Orders",
    4: "DDoS Attack",
    5: "FTP Brute-Force",
    6: "Ping of Death",
    7: "Phishing",
    8: "Fraud VoIP",
    9: "Open Proxy",
    10: "Web Spam",
    11: "Email Spam",
    12: "Blog Spam",
    13: "VPN IP",
    14: "Port Scan",
    15: "Hacking",
    16: "SQL Injection",
    17: "Email Spoofing",
    18: "Brute-Force",
    19: "Bad Web Bot",
    20: "Exploited Host",
    21: "Web App Attack",
    22: "SSH",
    23: "IoT Targeted",
}


def _risk_label(score):
    """Return a human-readable risk label for an abuse confidence score."""
    if score >= 76:
        return "VERY HIGH RISK (actively abusive)"
    if score >= 51:
        return "HIGH RISK"
    if score >= 26:
        return "MODERATE RISK"
    return "LOW RISK"


def _category_names(category_ids):
    """Convert category ID list to human-readable names."""
    return [_ABUSE_CATEGORIES.get(c, f"Category {c}") for c in category_ids]


def _api_get(endpoint, params=None):
    """Make an authenticated GET request to AbuseIPDB. Returns parsed JSON."""
    api_key = os.environ.get("ABUSEIPDB_API_KEY")
    if not api_key:
        return {
            "error": (
                "ABUSEIPDB_API_KEY environment variable is not set.\n"
                "Get a free key at: https://www.abuseipdb.com/account/api"
            )
        }

    if params:
        query_string = urllib.parse.urlencode(params)
        url = f"{_BASE_URL}/{endpoint}?{query_string}"
    else:
        url = f"{_BASE_URL}/{endpoint}"

    headers = {
        "Key": api_key,
        "Accept": "application/json",
        "User-Agent": "SysadminCopilot-AbuseIPDB",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return {"error": "Invalid AbuseIPDB API key."}
        if e.code == 429:
            return {"error": "AbuseIPDB rate limit exceeded (1,000 checks/day on free tier)."}
        if e.code == 422:
            body = e.read().decode(errors="replace")
            return {"error": f"Invalid request: {body[:200]}"}
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except urllib.error.URLError as e:
        return {"error": f"Connection failed: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


# ─── Tools ───────────────────────────────────────────────────────────────────

@tool
def check_ip_reputation(ip_address: str) -> str:
    """Check an IP address reputation on AbuseIPDB.

    Returns the abuse confidence score (0-100), country, ISP, total
    reports, and recent abuse report comments. Use this to investigate
    suspicious IPs found in SSH logs, web server logs, or firewall alerts.

    Requires ABUSEIPDB_API_KEY environment variable.

    Args:
        ip_address: IPv4 or IPv6 address to check (e.g. '118.25.6.39').
    """
    result = _api_get("check", {
        "ipAddress": ip_address.strip(),
        "maxAgeInDays": 90,
        "verbose": "",
    })

    if "error" in result:
        return f"[ERROR] {result['error']}"

    data = result.get("data", {})
    if not data:
        return f"No data returned for IP: {ip_address}"

    score = data.get("abuseConfidenceScore", 0)
    lines = [f"AbuseIPDB report for: {data.get('ipAddress', ip_address)}\n"]
    lines.append(f"  Abuse score:     {score}% — {_risk_label(score)}")
    lines.append(f"  Country:         {data.get('countryName', 'N/A')} ({data.get('countryCode', '??')})")
    lines.append(f"  ISP:             {data.get('isp', 'N/A')}")
    lines.append(f"  Domain:          {data.get('domain', 'N/A')}")
    lines.append(f"  Usage type:      {data.get('usageType', 'N/A')}")
    lines.append(f"  Total reports:   {data.get('totalReports', 0)}")
    lines.append(f"  Distinct users:  {data.get('numDistinctUsers', 0)}")
    lines.append(f"  Last reported:   {data.get('lastReportedAt') or 'Never'}")
    lines.append(f"  Whitelisted:     {'Yes' if data.get('isWhitelisted') else 'No'}")

    hostnames = data.get("hostnames", [])
    if hostnames:
        lines.append(f"  Hostnames:       {', '.join(hostnames[:5])}")

    reports = data.get("reports", [])
    if reports:
        lines.append(f"\n  Recent reports ({min(len(reports), 5)} of {len(reports)}):")
        for r in reports[:5]:
            date = r.get("reportedAt", "N/A")
            if "T" in date:
                date = date.split("T")[0]
            cats = _category_names(r.get("categories", []))
            comment = r.get("comment", "")
            if comment and len(comment) > 100:
                comment = comment[:100] + "..."
            lines.append(f"    [{date}] {', '.join(cats)}")
            if comment:
                lines.append(f"      {comment}")

    return "\n".join(lines)


@tool
def check_ip_block(network: str) -> str:
    """Check abuse reports for an IP subnet on AbuseIPDB.

    Analyzes an entire subnet (e.g. /24) to find which IPs within it
    have been reported for abuse. Maximum subnet size is /24.

    Requires ABUSEIPDB_API_KEY environment variable.

    Args:
        network: CIDR notation subnet (e.g. '192.168.1.0/24').
    """
    result = _api_get("check-block", {
        "network": network.strip(),
        "maxAgeInDays": 30,
    })

    if "error" in result:
        return f"[ERROR] {result['error']}"

    data = result.get("data", {})
    if not data:
        return f"No data returned for network: {network}"

    reported = data.get("reportedAddress", [])
    lines = [f"AbuseIPDB subnet report for: {data.get('networkAddress', network)}\n"]
    lines.append(f"  Network:          {data.get('networkAddress', 'N/A')}/{data.get('netmask', '?')}")
    lines.append(f"  Total IPs:        {data.get('numPossibleHosts', 'N/A')}")
    lines.append(f"  Reported IPs:     {len(reported)}")

    if reported:
        # Sort by abuse score descending
        reported.sort(key=lambda x: x.get("abuseConfidenceScore", 0), reverse=True)
        lines.append(f"\n  Most abusive IPs:")
        for ip in reported[:20]:
            addr = ip.get("ipAddress", "N/A")
            score = ip.get("abuseConfidenceScore", 0)
            count = ip.get("numReports", 0)
            country = ip.get("countryCode", "??")
            lines.append(f"    {addr:<18} Score: {score}%  |  Reports: {count}  |  {country}")

        if len(reported) > 20:
            lines.append(f"    ... and {len(reported) - 20} more IPs")
    else:
        lines.append("\n  No IPs in this subnet have been reported.")

    return "\n".join(lines)


@tool
def get_abusive_ips(confidence_minimum: int = 90, limit: int = 20) -> str:
    """Get a list of the most abusive IP addresses from AbuseIPDB.

    Returns IPs with an abuse confidence score above the given threshold.
    Useful for building blocklists or checking if known-bad IPs are
    hitting your infrastructure.

    Requires ABUSEIPDB_API_KEY environment variable.

    Args:
        confidence_minimum: Minimum abuse score (0-100, default 90).
        limit: Number of IPs to return (default 20, max 100).
    """
    limit = min(max(limit, 1), 100)
    confidence_minimum = min(max(confidence_minimum, 25), 100)

    result = _api_get("blacklist", {
        "confidenceMinimum": confidence_minimum,
        "limit": limit,
    })

    if "error" in result:
        return f"[ERROR] {result['error']}"

    data = result.get("data", [])
    if not data:
        return f"No IPs found with abuse score >= {confidence_minimum}%."

    lines = [f"AbuseIPDB blacklist (score >= {confidence_minimum}%):\n"]
    lines.append(f"  Showing {len(data)} IPs:\n")
    for ip in data:
        addr = ip.get("ipAddress", "N/A")
        score = ip.get("abuseConfidenceScore", 0)
        country = ip.get("countryCode", "??")
        lines.append(f"    {addr:<40} Score: {score}%  |  {country}")

    return "\n".join(lines)


WRITE_TOOLS = set()
