"""
LeakCheck breach lookup tools for Sysadmin Copilot.

Checks if emails, usernames, or domains appear in known data breaches.
Uses the LeakCheck API to show which breaches exposed the data and
what types of information were leaked.

Two tiers:
  - Public API (free, no key): shows breach sources and exposed field types
  - Pro API v2 (paid, LEAKCHECK_API_KEY): shows full details including
    usernames, names, and which fields were exposed per breach

Get a Pro API key at: https://leakcheck.io/
"""

import json
import os
import urllib.request
import urllib.parse

from langchain_core.tools import tool


# ─── Helpers ─────────────────────────────────────────────────────────────────

_PUBLIC_URL = "https://leakcheck.io/api/public"
_PRO_URL = "https://leakcheck.io/api/v2"


def _public_get(params, timeout=15):
    """GET request to LeakCheck Public API (free, no key)."""
    query = urllib.parse.urlencode(params)
    url = f"{_PUBLIC_URL}?{query}"
    headers = {
        "Accept": "application/json",
        "User-Agent": "SysadminCopilot-LeakCheck",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return {"error": "Rate limit exceeded. Wait a moment and try again."}
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except urllib.error.URLError as e:
        return {"error": f"Connection failed: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


def _pro_get(endpoint, params=None, timeout=15):
    """GET request to LeakCheck Pro API v2 (requires key)."""
    api_key = os.environ.get("LEAKCHECK_API_KEY")
    if not api_key:
        return {
            "error": (
                "LEAKCHECK_API_KEY environment variable is not set.\n"
                "Get a Pro API key at: https://leakcheck.io/"
            )
        }

    url = f"{_PRO_URL}/{endpoint}"
    if params:
        query = urllib.parse.urlencode(params)
        url = f"{url}?{query}"

    headers = {
        "X-API-Key": api_key,
        "Accept": "application/json",
        "User-Agent": "SysadminCopilot-LeakCheck",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return {"error": "Missing or invalid LEAKCHECK_API_KEY."}
        if e.code == 403:
            return {"error": "LeakCheck plan limit reached or active plan required."}
        if e.code == 429:
            return {"error": "Rate limit exceeded (3 req/sec). Wait and try again."}
        if e.code == 422:
            return {"error": "Could not determine search type. Try specifying the type parameter."}
        if e.code == 400:
            try:
                body = json.loads(e.read())
                msg = body.get("error", e.reason)
            except Exception:
                msg = e.reason
            return {"error": f"Bad request: {msg}"}
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except urllib.error.URLError as e:
        return {"error": f"Connection failed: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


def _check_error(result):
    """Return error string if result is an error, else None."""
    if isinstance(result, dict) and "error" in result:
        return f"[ERROR] {result['error']}"
    if isinstance(result, dict) and not result.get("success", True):
        return "[ERROR] LeakCheck API request failed."
    return None


# ─── Tools ───────────────────────────────────────────────────────────────────

@tool
def leakcheck_public(query: str) -> str:
    """Check if an email or username appears in known data breaches (free).

    Uses the LeakCheck Public API — no API key required. Shows which
    breach sources contained the query and what data fields were exposed
    (e.g. email, password, username). Does NOT show actual leaked values.

    Args:
        query: Email address or username to check (min 3 characters).
    """
    query = query.strip()
    if len(query) < 3:
        return "[ERROR] Query must be at least 3 characters."

    result = _public_get({"check": query})
    err = _check_error(result)
    if err:
        return err

    found = result.get("found", 0)
    if not found:
        return f"No breaches found for: {query}"

    lines = [f"LeakCheck results for: {query}\n"]
    lines.append(f"  Found in {found} breach(es)")

    fields = result.get("fields", [])
    if fields:
        lines.append(f"  Exposed data types: {', '.join(fields)}")

    sources = result.get("sources", [])
    if sources:
        lines.append(f"\n  Breach sources:")
        for s in sources:
            name = s.get("name", "Unknown")
            date = s.get("date", "Unknown date")
            lines.append(f"    {name} ({date})")

    lines.append(f"\n  Powered by LeakCheck (https://leakcheck.io)")
    return "\n".join(lines)


@tool
def leakcheck_lookup(
    query: str,
    search_type: str = "auto",
    limit: int = 20,
) -> str:
    """Search for leaked credentials by email, username, domain, or phone.

    Uses the LeakCheck Pro API for detailed breach data. Shows breach
    source, exposed fields, and associated usernames/names per result.

    Requires LEAKCHECK_API_KEY environment variable.

    Args:
        query: Search term (email, username, domain, or phone number).
        search_type: Type of search — 'auto', 'email', 'username', 'domain',
                     'phone', 'keyword', or 'hash'. Default auto-detects.
        limit: Max results to return (default 20, max 100).
    """
    query = query.strip()
    if len(query) < 3:
        return "[ERROR] Query must be at least 3 characters."

    limit = min(max(limit, 1), 100)
    encoded = urllib.parse.quote(query)
    params = {"limit": limit}
    if search_type and search_type != "auto":
        params["type"] = search_type

    result = _pro_get(f"query/{encoded}", params)
    err = _check_error(result)
    if err:
        return err

    found = result.get("found", 0)
    if not found:
        return f"No breaches found for: {query}"

    quota = result.get("quota", "?")
    lines = [f"LeakCheck Pro results for: {query}\n"]
    lines.append(f"  Found: {found} result(s)  |  Quota remaining: {quota}\n")

    results = result.get("result", [])
    for r in results[:limit]:
        # Build the entry
        email = r.get("email", "")
        username = r.get("username", "")
        first = r.get("first_name", "")
        last = r.get("last_name", "")
        phone = r.get("phone", "")
        fields = r.get("fields", [])

        source = r.get("source", {})
        if isinstance(source, dict):
            src_name = source.get("name", "Unknown")
            src_date = source.get("breach_date", "")
            unverified = source.get("unverified", 0)
            compilation = source.get("compilation", 0)
        else:
            src_name = str(source)
            src_date = ""
            unverified = 0
            compilation = 0

        # Source line
        src_line = f"  {src_name}"
        if src_date:
            src_line += f" ({src_date})"
        if unverified:
            src_line += " [unverified]"
        if compilation:
            src_line += " [compilation]"
        lines.append(src_line)

        # Details
        details = []
        if email:
            details.append(f"Email: {email}")
        if username:
            details.append(f"Username: {username}")
        name = " ".join(filter(None, [first, last]))
        if name:
            details.append(f"Name: {name}")
        if phone:
            details.append(f"Phone: {phone}")
        if details:
            lines.append(f"    {' | '.join(details)}")

        if fields:
            lines.append(f"    Exposed: {', '.join(fields)}")
        lines.append("")

    if found > limit:
        lines.append(f"  ... and {found - limit} more results (increase limit)")

    return "\n".join(lines)


@tool
def leakcheck_domain(domain: str, limit: int = 25) -> str:
    """List all leaked email addresses for a domain.

    Searches the LeakCheck Pro database for all breached email addresses
    under a given domain. Use this to find out which employees or users
    in your organization have been exposed.

    Requires LEAKCHECK_API_KEY environment variable.

    Args:
        domain: Domain to search (e.g. 'example.com').
        limit: Max results to return (default 25, max 100).
    """
    domain = domain.strip()
    limit = min(max(limit, 1), 100)

    encoded = urllib.parse.quote(domain)
    result = _pro_get(f"query/{encoded}", {"type": "domain", "limit": limit})
    err = _check_error(result)
    if err:
        return err

    found = result.get("found", 0)
    if not found:
        return f"No breached emails found for domain: {domain}"

    results = result.get("result", [])
    quota = result.get("quota", "?")

    # Group by email
    email_breaches = {}
    for r in results:
        email = r.get("email", "unknown")
        source = r.get("source", {})
        src_name = source.get("name", "Unknown") if isinstance(source, dict) else str(source)
        fields = r.get("fields", [])
        if email not in email_breaches:
            email_breaches[email] = []
        email_breaches[email].append((src_name, fields))

    lines = [f"Breached emails for domain: {domain}\n"]
    lines.append(f"  Total results: {found}  |  Unique emails: {len(email_breaches)}  |  Quota: {quota}\n")

    for email, breaches in sorted(email_breaches.items()):
        breach_names = [b[0] for b in breaches]
        all_fields = set()
        for _, fields in breaches:
            all_fields.update(fields)
        lines.append(f"  {email}")
        lines.append(f"    Breaches ({len(breaches)}): {', '.join(breach_names[:5])}")
        if len(breach_names) > 5:
            lines[-1] += f" (+{len(breach_names) - 5} more)"
        if all_fields:
            lines.append(f"    Exposed: {', '.join(sorted(all_fields))}")
        lines.append("")

    if found > limit:
        lines.append(f"  ... and more results available (increase limit)")

    return "\n".join(lines)


WRITE_TOOLS = set()
