"""
Breach and leak monitoring tools for Sysadmin Copilot.

Checks if your organization's emails or domains appear in known data breaches
and credential leaks. Uses the Have I Been Pwned (HIBP) API.

Requires HIBP_API_KEY environment variable for email/domain breach lookups.
Get a key at: https://haveibeenpwned.com/API/Key (paid, supports the project).

Without an API key, only the public breach catalog is available.
"""

import json
import os
import re
import time
import urllib.request
import urllib.parse

from langchain_core.tools import tool

# ─── Helpers ─────────────────────────────────────────────────────────────────

_HIBP_BASE = "https://haveibeenpwned.com/api/v3"
_HIBP_HEADERS = {
    "User-Agent": "SysadminCopilot-BreachCheck",
}


def _hibp_request(endpoint, api_key=None):
    """Make a request to the HIBP API. Returns parsed JSON or error string."""
    url = f"{_HIBP_BASE}/{endpoint}"
    headers = dict(_HIBP_HEADERS)
    if api_key:
        headers["hibp-api-key"] = api_key

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return "[ERROR] Invalid HIBP API key."
        if e.code == 404:
            return None  # not found = no breaches
        if e.code == 429:
            return "[ERROR] HIBP rate limit exceeded. Wait a moment and try again."
        return f"[ERROR] HIBP API returned HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return f"[ERROR] Could not reach HIBP API: {e.reason}"
    except Exception as e:
        return f"[ERROR] {e}"


def _format_breach(b):
    """Format a single breach record into a readable string."""
    lines = [f"  {b.get('Name', 'Unknown')}"]
    if b.get("Title"):
        lines[0] = f"  {b['Title']}"
    if b.get("Domain"):
        lines.append(f"    Domain:       {b['Domain']}")
    if b.get("BreachDate"):
        lines.append(f"    Breach date:  {b['BreachDate']}")
    if b.get("PwnCount"):
        count = b["PwnCount"]
        if count >= 1_000_000:
            lines.append(f"    Records:      {count / 1_000_000:.1f}M")
        elif count >= 1_000:
            lines.append(f"    Records:      {count / 1_000:.0f}K")
        else:
            lines.append(f"    Records:      {count}")
    if b.get("DataClasses"):
        lines.append(f"    Exposed data: {', '.join(b['DataClasses'][:8])}")
    if b.get("Description"):
        desc = re.sub(r"<[^>]+>", "", b["Description"])
        if len(desc) > 200:
            desc = desc[:200] + "..."
        lines.append(f"    Description:  {desc}")
    return "\n".join(lines)


# ─── Tools ───────────────────────────────────────────────────────────────────

@tool
def check_email_breaches(email: str) -> str:
    """Check if an email address appears in known data breaches.

    Queries the Have I Been Pwned database to find breaches that include
    this email address. Use this to check if an employee's or user's
    credentials may have been exposed.

    Requires HIBP_API_KEY environment variable.

    Args:
        email: Email address to check (e.g. 'admin@example.com').
    """
    api_key = os.environ.get("HIBP_API_KEY")
    if not api_key:
        return (
            "[ERROR] HIBP_API_KEY environment variable is not set.\n"
            "Get an API key at: https://haveibeenpwned.com/API/Key"
        )

    encoded = urllib.parse.quote(email)
    result = _hibp_request(
        f"breachedaccount/{encoded}?truncateResponse=false",
        api_key=api_key,
    )

    if isinstance(result, str):
        return result  # error message
    if result is None:
        return f"No breaches found for {email}."

    breaches = result
    lines = [f"Found {len(breaches)} breach(es) for {email}:\n"]
    for b in breaches:
        lines.append(_format_breach(b))
        lines.append("")

    return "\n".join(lines)


@tool
def check_email_pastes(email: str) -> str:
    """Check if an email address has appeared in public paste dumps.

    Pastes are text dumps posted to sites like Pastebin that often contain
    leaked credentials. A hit here means the email (and potentially a password)
    was publicly exposed.

    Requires HIBP_API_KEY environment variable.

    Args:
        email: Email address to check.
    """
    api_key = os.environ.get("HIBP_API_KEY")
    if not api_key:
        return (
            "[ERROR] HIBP_API_KEY environment variable is not set.\n"
            "Get an API key at: https://haveibeenpwned.com/API/Key"
        )

    encoded = urllib.parse.quote(email)
    result = _hibp_request(f"pasteaccount/{encoded}", api_key=api_key)

    if isinstance(result, str):
        return result
    if result is None:
        return f"No pastes found for {email}."

    lines = [f"Found {len(result)} paste(s) containing {email}:\n"]
    for p in result[:20]:
        source = p.get("Source", "Unknown")
        title = p.get("Title") or "(untitled)"
        date = p.get("Date", "Unknown date")
        count = p.get("EmailCount", "?")
        lines.append(f"  {source}: {title}")
        lines.append(f"    Date: {date}  |  Emails in paste: {count}")
        lines.append("")

    if len(result) > 20:
        lines.append(f"  ... and {len(result) - 20} more pastes")

    return "\n".join(lines)


@tool
def check_domain_breaches(domain: str) -> str:
    """Check if a domain appears in known data breaches.

    Searches the Have I Been Pwned breach catalog for breaches that
    affected the given domain. Use this to check if your organization's
    domain has been part of a known breach.

    No API key required (uses the public breach catalog).

    Args:
        domain: Domain to check (e.g. 'example.com').
    """
    # Public endpoint — API key optional but included if available
    api_key = os.environ.get("HIBP_API_KEY")
    result = _hibp_request(
        f"breaches?domain={urllib.parse.quote(domain)}",
        api_key=api_key,
    )

    if isinstance(result, str):
        return result
    if result is None or (isinstance(result, list) and len(result) == 0):
        return f"No breaches found involving domain {domain}."

    breaches = result
    lines = [f"Found {len(breaches)} breach(es) involving {domain}:\n"]
    for b in breaches:
        lines.append(_format_breach(b))
        lines.append("")

    return "\n".join(lines)


@tool
def list_breached_emails(domain: str) -> str:
    """List all email addresses from a domain that appear in known breaches.

    Returns every breached email alias under the given domain along with
    which breaches each address was found in. Use this to identify which
    specific employees or users in your organization have been compromised.

    This is different from check_domain_breaches which shows which breaches
    hit the domain — this tool shows which individual emails were exposed.

    Requires HIBP_API_KEY environment variable.

    Args:
        domain: Domain to search (e.g. 'example.com').
    """
    api_key = os.environ.get("HIBP_API_KEY")
    if not api_key:
        return (
            "[ERROR] HIBP_API_KEY environment variable is not set.\n"
            "Get an API key at: https://haveibeenpwned.com/API/Key"
        )

    result = _hibp_request(
        f"breacheddomain/{urllib.parse.quote(domain)}",
        api_key=api_key,
    )

    if isinstance(result, str):
        return result
    if result is None or (isinstance(result, dict) and len(result) == 0):
        return f"No breached email addresses found for {domain}."

    # Response is a dict: {"alias": ["Breach1", "Breach2"], ...}
    # where alias is the local part (before @)
    total_emails = len(result)
    all_breaches = set()
    for breaches in result.values():
        if isinstance(breaches, list):
            for b in breaches:
                all_breaches.add(str(b) if not isinstance(b, str) else b)
        elif isinstance(breaches, str):
            all_breaches.add(breaches)

    lines = [
        f"Found {total_emails} breached email(s) under {domain}",
        f"Across {len(all_breaches)} breach(es): {', '.join(sorted(all_breaches)[:10])}",
    ]
    if len(all_breaches) > 10:
        lines[-1] += f" (+{len(all_breaches) - 10} more)"
    lines.append("")

    # Sort by number of breaches (most exposed first)
    sorted_emails = sorted(result.items(), key=lambda x: len(x[1]), reverse=True)

    max_display = 50
    for alias, breaches in sorted_emails[:max_display]:
        email = f"{alias}@{domain}"
        breach_list = ", ".join(breaches[:5])
        if len(breaches) > 5:
            breach_list += f" (+{len(breaches) - 5} more)"
        lines.append(f"  {email}")
        lines.append(f"    Breaches ({len(breaches)}): {breach_list}")

    if total_emails > max_display:
        lines.append(f"\n  ... and {total_emails - max_display} more emails")

    return "\n".join(lines)


@tool
def check_bulk_breaches(emails_or_file: str) -> str:
    """Check multiple email addresses for breaches in one go.

    Accepts either a comma-separated list of emails or a file path
    containing one email per line. Each email is checked against the
    Have I Been Pwned database with rate limiting between requests.

    Requires HIBP_API_KEY environment variable.

    Args:
        emails_or_file: Comma-separated emails (e.g. 'a@co.com,b@co.com')
                        or path to a file with one email per line.
    """
    api_key = os.environ.get("HIBP_API_KEY")
    if not api_key:
        return (
            "[ERROR] HIBP_API_KEY environment variable is not set.\n"
            "Get an API key at: https://haveibeenpwned.com/API/Key"
        )

    # Determine if input is a file path or comma-separated list
    if os.path.isfile(emails_or_file):
        try:
            with open(emails_or_file) as f:
                emails = [line.strip() for line in f if line.strip() and "@" in line]
        except (PermissionError, OSError) as e:
            return f"[ERROR] Cannot read file: {e}"
    else:
        emails = [e.strip() for e in emails_or_file.split(",") if e.strip() and "@" in e]

    if not emails:
        return "[ERROR] No valid email addresses found in input."

    max_emails = 30
    if len(emails) > max_emails:
        return (
            f"[ERROR] Too many emails ({len(emails)}). "
            f"Maximum is {max_emails} to stay within API rate limits."
        )

    results = []
    breached_count = 0
    clean_count = 0

    for i, email in enumerate(emails):
        if i > 0:
            time.sleep(1.6)  # HIBP rate limit: ~10 requests per rolling minute

        encoded = urllib.parse.quote(email)
        result = _hibp_request(
            f"breachedaccount/{encoded}?truncateResponse=true",
            api_key=api_key,
        )

        if isinstance(result, str) and result.startswith("[ERROR]"):
            results.append(f"  {email}: {result}")
            continue

        if result is None:
            clean_count += 1
            continue

        breach_names = [b.get("Name", "?") for b in result]
        breached_count += 1
        results.append(f"  {email}: {len(result)} breach(es) — {', '.join(breach_names[:5])}")
        if len(breach_names) > 5:
            results[-1] += f" (+{len(breach_names) - 5} more)"

    lines = [f"Breach check results for {len(emails)} email(s):\n"]
    lines.append(f"  Breached: {breached_count}")
    lines.append(f"  Clean:    {clean_count}")
    if results:
        lines.append(f"\nBreached accounts:")
        lines.extend(results)

    return "\n".join(lines)


@tool
def check_password_exposure(password: str) -> str:
    """Check if a password has appeared in known data breaches.

    Uses the HIBP Pwned Passwords API with k-anonymity — only the first 5
    characters of the SHA-1 hash are sent to the API, so the actual password
    is never transmitted. This is safe to use with real passwords.

    No API key required.

    Args:
        password: The password to check (never sent over the network).
    """
    import hashlib

    sha1 = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    prefix = sha1[:5]
    suffix = sha1[5:]

    url = f"https://api.pwnedpasswords.com/range/{prefix}"
    req = urllib.request.Request(url, headers={"User-Agent": "SysadminCopilot-BreachCheck"})

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
    except Exception as e:
        return f"[ERROR] Could not reach Pwned Passwords API: {e}"

    for line in body.splitlines():
        parts = line.strip().split(":")
        if len(parts) == 2 and parts[0] == suffix:
            count = int(parts[1])
            return (
                f"WARNING: This password has been seen {count:,} time(s) in data breaches.\n"
                f"It should be changed immediately.\n\n"
                f"Note: The password itself was NOT sent to the API. Only the first 5 "
                f"characters of its SHA-1 hash were transmitted (k-anonymity model)."
            )

    return (
        "This password has NOT been found in any known data breaches.\n\n"
        "Note: The password was NOT sent to the API (k-anonymity model)."
    )


@tool
def list_recent_breaches(count: int = 10) -> str:
    """List the most recent publicly known data breaches.

    Shows the latest breaches added to the Have I Been Pwned database.
    Useful for staying aware of new breaches that might affect your
    organization or users. No API key required.

    Args:
        count: Number of recent breaches to show (default 10, max 30).
    """
    count = min(max(count, 1), 30)

    result = _hibp_request("breaches")

    if isinstance(result, str):
        return result
    if not result:
        return "No breach data available."

    # Sort by AddedDate descending
    breaches = sorted(result, key=lambda b: b.get("AddedDate", ""), reverse=True)

    lines = [f"Most recent {count} breaches in Have I Been Pwned:\n"]
    for b in breaches[:count]:
        lines.append(_format_breach(b))
        lines.append("")

    lines.append(f"Total breaches in database: {len(result)}")
    return "\n".join(lines)


@tool
def search_breach_by_name(breach_name: str) -> str:
    """Get detailed information about a specific known breach.

    Look up a breach by its name (e.g. 'LinkedIn', 'Adobe', 'Dropbox')
    to see when it happened, what data was exposed, and how many accounts
    were affected. No API key required.

    Args:
        breach_name: Name of the breach (e.g. 'LinkedIn', 'Adobe').
    """
    result = _hibp_request(f"breach/{urllib.parse.quote(breach_name)}")

    if isinstance(result, str):
        return result
    if result is None:
        return f"No breach found with name '{breach_name}'."

    return _format_breach(result)


WRITE_TOOLS = set()
