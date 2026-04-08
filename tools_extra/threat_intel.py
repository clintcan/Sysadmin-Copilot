"""
Threat intelligence tools for Sysadmin Copilot.

Provides IOC extraction, file hashing, and VirusTotal lookups.
VT tools require a VT_API_KEY environment variable (free tier works fine).
"""

import hashlib
import os
import re
from collections import Counter

from langchain_core.tools import tool
from tools import run_cmd

# ─── Regex patterns for IOC extraction ────────────────────────────────────────

_RE_IPV4 = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\b"
)
_RE_HASH_MD5 = re.compile(r"\b[a-fA-F0-9]{32}\b")
_RE_HASH_SHA1 = re.compile(r"\b[a-fA-F0-9]{40}\b")
_RE_HASH_SHA256 = re.compile(r"\b[a-fA-F0-9]{64}\b")
_RE_URL = re.compile(r"https?://[^\s\"'<>]+")
_RE_EMAIL = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")
_RE_DOMAIN = re.compile(
    r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+"
    r"(?:com|net|org|io|xyz|ru|cn|top|tk|ml|ga|cf|info|biz|"
    r"cc|pw|club|work|de|uk|fr|nl|au|ca|br|in|jp|kr|"
    r"gov|edu|mil|co|us|za|mx|it|es|se|no|fi|pl|cz)\b"
)

# Common false-positive IPs to ignore
_IGNORE_IPS = {"0.0.0.0", "127.0.0.1", "255.255.255.255"}


@tool
def extract_iocs(file_path: str, max_results: int = 50) -> str:
    """Extract Indicators of Compromise (IOCs) from a file.

    Scans a log file or text file and extracts IP addresses, domains, URLs,
    email addresses, and file hashes (MD5, SHA1, SHA256). Useful as a first
    step in incident triage. Note: MD5 matches may include false positives
    from other 32-char hex strings (UUIDs, etc.) — verify before acting.

    Args:
        file_path: Path to the file to scan.
        max_results: Max number of results per IOC type (default 50).
    """
    max_file_size = 50 * 1024 * 1024  # 50 MB
    try:
        size = os.path.getsize(file_path)
        if size > max_file_size:
            return f"[ERROR] File is too large ({size // 1024 // 1024} MB). Max is 50 MB."
        with open(file_path, "r", errors="replace") as f:
            text = f.read()
    except FileNotFoundError:
        return f"[ERROR] File not found: {file_path}"
    except PermissionError:
        return f"[ERROR] Permission denied: {file_path}"

    ips = Counter(
        ip for ip in _RE_IPV4.findall(text) if ip not in _IGNORE_IPS
    )
    urls = Counter(_RE_URL.findall(text))
    emails = Counter(_RE_EMAIL.findall(text))
    domains = Counter(_RE_DOMAIN.findall(text))
    sha256s = Counter(_RE_HASH_SHA256.findall(text))
    # Exclude SHA256 matches from SHA1 and MD5
    sha256_set = set(sha256s)
    sha1s = Counter(h for h in _RE_HASH_SHA1.findall(text) if h not in sha256_set)
    sha1_set = set(sha1s)
    md5s = Counter(
        h for h in _RE_HASH_MD5.findall(text)
        if h not in sha256_set and h not in sha1_set
    )

    # Remove domains already present in URLs to reduce noise
    url_text = " ".join(urls)
    domains = Counter(
        {d: c for d, c in domains.items() if d not in url_text}
    )

    sections = []

    def _fmt(label, counter):
        if not counter:
            return
        items = counter.most_common(max_results)
        lines = [f"{label} ({len(counter)} unique):"]
        for value, count in items:
            lines.append(f"  {value}  (x{count})")
        if len(counter) > max_results:
            lines.append(f"  ... and {len(counter) - max_results} more")
        sections.append("\n".join(lines))

    _fmt("IPs", ips)
    _fmt("Domains", domains)
    _fmt("URLs", urls)
    _fmt("Emails", emails)
    _fmt("SHA256 hashes", sha256s)
    _fmt("SHA1 hashes", sha1s)
    _fmt("MD5 hashes", md5s)

    if not sections:
        return "No IOCs found in the file."

    return "\n\n".join(sections)


@tool
def hash_file(file_path: str) -> str:
    """Compute MD5, SHA1, and SHA256 hashes of a file.

    Useful for verifying file integrity or looking up hashes on VirusTotal
    and other threat intelligence platforms.

    Args:
        file_path: Path to the file to hash.
    """
    try:
        md5 = hashlib.md5()
        sha1 = hashlib.sha1()
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                md5.update(chunk)
                sha1.update(chunk)
                sha256.update(chunk)
        return (
            f"File:   {file_path}\n"
            f"MD5:    {md5.hexdigest()}\n"
            f"SHA1:   {sha1.hexdigest()}\n"
            f"SHA256: {sha256.hexdigest()}"
        )
    except FileNotFoundError:
        return f"[ERROR] File not found: {file_path}"
    except PermissionError:
        return f"[ERROR] Permission denied: {file_path}"


@tool
def vt_hash_lookup(file_hash: str) -> str:
    """Look up a file hash on VirusTotal for malware detection results.

    Requires VT_API_KEY environment variable to be set (free tier is fine).

    Args:
        file_hash: MD5, SHA1, or SHA256 hash to look up.
    """
    api_key = os.environ.get("VT_API_KEY")
    if not api_key:
        return "[ERROR] VT_API_KEY environment variable is not set."

    return run_cmd([
        "curl", "-s",
        "-H", f"x-apikey: {api_key}",
        f"https://www.virustotal.com/api/v3/files/{file_hash}",
    ], 30)


@tool
def vt_ip_lookup(ip_address: str) -> str:
    """Look up an IP address on VirusTotal for reputation and detections.

    Requires VT_API_KEY environment variable to be set.

    Args:
        ip_address: IP address to look up.
    """
    api_key = os.environ.get("VT_API_KEY")
    if not api_key:
        return "[ERROR] VT_API_KEY environment variable is not set."

    return run_cmd([
        "curl", "-s",
        "-H", f"x-apikey: {api_key}",
        f"https://www.virustotal.com/api/v3/ip_addresses/{ip_address}",
    ], 30)


@tool
def vt_domain_lookup(domain: str) -> str:
    """Look up a domain on VirusTotal for reputation and detections.

    Requires VT_API_KEY environment variable to be set.

    Args:
        domain: Domain name to look up.
    """
    api_key = os.environ.get("VT_API_KEY")
    if not api_key:
        return "[ERROR] VT_API_KEY environment variable is not set."

    return run_cmd([
        "curl", "-s",
        "-H", f"x-apikey: {api_key}",
        f"https://www.virustotal.com/api/v3/domains/{domain}",
    ], 30)


REQUIRED_ENV = {"VT_API_KEY"}
WRITE_TOOLS = set()
