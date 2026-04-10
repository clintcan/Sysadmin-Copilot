"""
DeHashed breach search tools for Sysadmin Copilot.

Searches the DeHashed breach database for leaked credentials, emails,
usernames, IP addresses, and more. Returns actual leaked values
(passwords, hashes) when available — more detailed than HIBP.

Requires DEHASHED_EMAIL and DEHASHED_API_KEY environment variables.
API credits are pay-per-query (~$3 per 100 queries).
Sign up at: https://dehashed.com/
"""

import json
import os
import base64
import urllib.request
import urllib.parse

from langchain_core.tools import tool


# ─── Helpers ─────────────────────────────────────────────────────────────────

_BASE_URL = "https://api.dehashed.com"


def _api_get(endpoint, params=None, timeout=15):
    """GET request to DeHashed API with Basic Auth."""
    email = os.environ.get("DEHASHED_EMAIL")
    api_key = os.environ.get("DEHASHED_API_KEY")
    if not email or not api_key:
        return {
            "error": (
                "DEHASHED_EMAIL and DEHASHED_API_KEY environment variables are required.\n"
                "Sign up at: https://dehashed.com/"
            )
        }

    url = f"{_BASE_URL}/{endpoint}"
    if params:
        query = urllib.parse.urlencode(params)
        url = f"{url}?{query}"

    # Basic auth: base64(email:api_key)
    credentials = base64.b64encode(f"{email}:{api_key}".encode()).decode()
    headers = {
        "Accept": "application/json",
        "Authorization": f"Basic {credentials}",
        "User-Agent": "SysadminCopilot-DeHashed",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return {"error": "Invalid DeHashed credentials (check DEHASHED_EMAIL and DEHASHED_API_KEY)."}
        if e.code == 402:
            return {"error": "Insufficient DeHashed API credits. Purchase more at dehashed.com."}
        if e.code == 429:
            return {"error": "DeHashed rate limit exceeded. Wait and try again."}
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except urllib.error.URLError as e:
        return {"error": f"Connection failed: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


def _check_error(result):
    """Return error string if result is an error, else None."""
    if isinstance(result, dict) and "error" in result:
        return f"[ERROR] {result['error']}"
    if isinstance(result, dict) and result.get("success") is False:
        return "[ERROR] DeHashed query failed."
    return None


def _format_entry(e):
    """Format a single breach entry."""
    lines = []
    email = e.get("email", "")
    username = e.get("username", "")
    name = e.get("name", "")
    password = e.get("password", "")
    hashed_pw = e.get("hashed_password", "")
    ip = e.get("ip_address", "")
    phone = e.get("phone", "")
    address = e.get("address", "")
    source = e.get("obtained_from", "Unknown source")

    # Header: source
    lines.append(f"  [{source}]")

    # Identity fields
    identity = []
    if email:
        identity.append(f"Email: {email}")
    if username:
        identity.append(f"User: {username}")
    if name:
        identity.append(f"Name: {name}")
    if identity:
        lines.append(f"    {' | '.join(identity)}")

    # Credential fields
    if password:
        lines.append(f"    Password: {password}")
    if hashed_pw:
        lines.append(f"    Hash: {hashed_pw}")

    # Other fields
    other = []
    if ip:
        other.append(f"IP: {ip}")
    if phone:
        other.append(f"Phone: {phone}")
    if address:
        other.append(f"Address: {address}")
    if other:
        lines.append(f"    {' | '.join(other)}")

    return "\n".join(lines)


# ─── Tools ───────────────────────────────────────────────────────────────────

@tool
def dehashed_search(query: str, search_type: str = "email") -> str:
    """Search the DeHashed breach database for leaked credentials and data.

    Returns actual leaked values including passwords, hashes, usernames,
    IP addresses, and breach sources. More detailed than HIBP — shows
    the actual compromised data, not just which breaches occurred.

    Requires DEHASHED_EMAIL and DEHASHED_API_KEY environment variables.
    Each query costs 1 API credit (~$0.03).

    Args:
        query: The value to search for.
        search_type: Field to search — 'email', 'username', 'domain', 'ip',
                     'phone', 'name', 'password', 'hashed_password', 'address'.
    """
    query = query.strip()
    if not query:
        return "[ERROR] Query cannot be empty."

    valid_types = {
        "email", "username", "domain", "ip", "ip_address",
        "phone", "name", "password", "hashed_password", "address", "vin",
    }
    if search_type not in valid_types:
        return f"[ERROR] Invalid search_type: {search_type}. Valid: {', '.join(sorted(valid_types))}"

    # Map 'ip' to 'ip_address' for the API
    api_field = "ip_address" if search_type == "ip" else search_type

    result = _api_get("search", {"query": f"{api_field}:{query}"})
    err = _check_error(result)
    if err:
        return err

    entries = result.get("entries") or []
    if not entries:
        return f"No results found for {search_type}:{query}"

    total = result.get("total", len(entries))
    lines = [f"DeHashed results for {search_type}:{query}\n"]
    lines.append(f"  Total matches: {total}  |  Showing: {min(len(entries), 50)}\n")

    # Deduplicate by source+email+username
    seen = set()
    displayed = 0
    for e in entries:
        key = (e.get("obtained_from", ""), e.get("email", ""), e.get("username", ""))
        if key in seen:
            continue
        seen.add(key)
        lines.append(_format_entry(e))
        lines.append("")
        displayed += 1
        if displayed >= 50:
            break

    if total > 50:
        lines.append(f"  ... and {total - 50} more results")

    return "\n".join(lines)


@tool
def dehashed_domain(domain: str) -> str:
    """Search DeHashed for all leaked credentials under a domain.

    Finds all breached email addresses, passwords, and usernames
    associated with a domain. Use this to audit your organization's
    credential exposure across all known breaches.

    Requires DEHASHED_EMAIL and DEHASHED_API_KEY environment variables.

    Args:
        domain: Domain to search (e.g. 'example.com').
    """
    domain = domain.strip()
    if not domain:
        return "[ERROR] Domain cannot be empty."

    result = _api_get("search", {"query": f"domain:{domain}"})
    err = _check_error(result)
    if err:
        return err

    entries = result.get("entries") or []
    if not entries:
        return f"No leaked credentials found for domain: {domain}"

    total = result.get("total", len(entries))

    # Group by email
    by_email = {}
    for e in entries:
        email = e.get("email", "(no email)")
        if email not in by_email:
            by_email[email] = []
        by_email[email].append(e)

    # Count credentials with passwords
    pw_count = sum(1 for e in entries if e.get("password") or e.get("hashed_password"))

    lines = [f"DeHashed domain report: {domain}\n"]
    lines.append(f"  Total records:    {total}")
    lines.append(f"  Unique emails:    {len(by_email)}")
    lines.append(f"  With credentials: {pw_count}\n")

    # Show entries grouped by email, most exposed first
    sorted_emails = sorted(by_email.items(), key=lambda x: len(x[1]), reverse=True)
    displayed = 0
    for email, email_entries in sorted_emails[:30]:
        sources = list({e.get("obtained_from", "?") for e in email_entries})
        has_pw = any(e.get("password") for e in email_entries)
        has_hash = any(e.get("hashed_password") for e in email_entries)

        lines.append(f"  {email}")
        lines.append(f"    Breaches ({len(email_entries)}): {', '.join(sources[:5])}")
        if len(sources) > 5:
            lines[-1] += f" (+{len(sources) - 5} more)"
        flags = []
        if has_pw:
            flags.append("PLAINTEXT PASSWORD EXPOSED")
        if has_hash:
            flags.append("password hash exposed")
        if flags:
            lines.append(f"    WARNING: {', '.join(flags)}")
        lines.append("")
        displayed += 1

    if len(by_email) > 30:
        lines.append(f"  ... and {len(by_email) - 30} more emails")

    return "\n".join(lines)


REQUIRED_ENV = {"DEHASHED_EMAIL", "DEHASHED_API_KEY"}
WRITE_TOOLS = set()
