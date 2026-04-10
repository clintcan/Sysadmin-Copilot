"""
DeHashed breach search tools for Sysadmin Copilot.

Searches the DeHashed breach database for leaked credentials, emails,
usernames, IP addresses, and more. Returns actual leaked values
(passwords, hashes) when available — more detailed than HIBP.

Requires DEHASHED_API_KEY environment variable.
API credits are pay-per-query (~$3 per 100 queries).
Sign up at: https://dehashed.com/
"""

import json
import os
import urllib.request
import urllib.parse

from langchain_core.tools import tool


# ─── Helpers ─────────────────────────────────────────────────────────────────

_BASE_URL = "https://api.dehashed.com/v2"


def _api_post(endpoint, body, timeout=15):
    """POST JSON to DeHashed API v2."""
    api_key = os.environ.get("DEHASHED_API_KEY")
    if not api_key:
        return {
            "error": (
                "DEHASHED_API_KEY environment variable is not set.\n"
                "Sign up at: https://dehashed.com/"
            )
        }

    url = f"{_BASE_URL}/{endpoint}"
    data = json.dumps(body).encode()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "DeHashed-Api-Key": api_key,
        "User-Agent": "SysadminCopilot-DeHashed",
    }
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return {"error": "Invalid DeHashed API key."}
        if e.code == 402:
            return {"error": "Insufficient DeHashed API credits. Purchase more at dehashed.com."}
        if e.code == 429:
            return {"error": "DeHashed rate limit exceeded. Wait and try again."}
        try:
            body = json.loads(e.read())
            return {"error": body.get("error", f"HTTP {e.code}: {e.reason}")}
        except Exception:
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
    if isinstance(source, list):
        source = ", ".join(str(s) for s in source)

    lines.append(f"  [{source}]")

    identity = []
    if email:
        identity.append(f"Email: {email}")
    if username:
        identity.append(f"User: {username}")
    if name:
        identity.append(f"Name: {name}")
    if identity:
        lines.append(f"    {' | '.join(identity)}")

    if password:
        lines.append(f"    Password: {password}")
    if hashed_pw:
        lines.append(f"    Hash: {hashed_pw}")

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
def dehashed_search(query: str, search_type: str = "email", size: int = 50) -> str:
    """Search the DeHashed breach database for leaked credentials and data.

    Returns actual leaked values including passwords, hashes, usernames,
    IP addresses, and breach sources. More detailed than HIBP — shows
    the actual compromised data, not just which breaches occurred.

    Requires DEHASHED_API_KEY environment variable.
    Each query costs 1 API credit (~$0.03).

    Args:
        query: The value to search for.
        search_type: Field to search — 'email', 'username', 'domain', 'ip',
                     'phone', 'name', 'password', 'hashed_password', 'address'.
        size: Number of results to return (default 50, max 10000).
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

    api_field = "ip_address" if search_type == "ip" else search_type
    size = min(max(size, 1), 10000)

    result = _api_post("search", {
        "query": f'{api_field}:"{query}"',
        "size": size,
        "de_dupe": True,
    })
    err = _check_error(result)
    if err:
        return err

    entries = result.get("entries") or []
    if not entries:
        return f"No results found for {search_type}:{query}"

    total = result.get("total", len(entries))
    lines = [f"DeHashed results for {search_type}:{query}\n"]
    lines.append(f"  Total matches: {total}  |  Showing: {min(len(entries), 50)}\n")

    for e in entries[:50]:
        lines.append(_format_entry(e))
        lines.append("")

    if total > 50:
        lines.append(f"  ... and {total - 50} more results")

    return "\n".join(lines)


@tool
def dehashed_domain(domain: str) -> str:
    """Search DeHashed for all leaked credentials under a domain.

    Finds all breached email addresses, passwords, and usernames
    associated with a domain. Use this to audit your organization's
    credential exposure across all known breaches.

    Requires DEHASHED_API_KEY environment variable.

    Args:
        domain: Domain to search (e.g. 'example.com').
    """
    domain = domain.strip()
    if not domain:
        return "[ERROR] Domain cannot be empty."

    result = _api_post("search", {
        "query": f'domain:"{domain}"',
        "size": 100,
        "de_dupe": True,
    })
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

    pw_count = sum(1 for e in entries if e.get("password") or e.get("hashed_password"))

    lines = [f"DeHashed domain report: {domain}\n"]
    lines.append(f"  Total records:    {total}")
    lines.append(f"  Unique emails:    {len(by_email)}")
    lines.append(f"  With credentials: {pw_count}\n")

    sorted_emails = sorted(by_email.items(), key=lambda x: len(x[1]), reverse=True)
    for email, email_entries in sorted_emails[:30]:
        sources = list({
            str(e.get("obtained_from", "?")) for e in email_entries
        })
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

    if len(by_email) > 30:
        lines.append(f"  ... and {len(by_email) - 30} more emails")

    return "\n".join(lines)


REQUIRED_ENV = {"DEHASHED_API_KEY"}
# Note: DEHASHED_EMAIL is no longer needed — v2 API uses only the API key.
WRITE_TOOLS = set()
