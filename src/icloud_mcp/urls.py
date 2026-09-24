"""Guard against credential exfiltration through attacker-controlled URLs.

Calendar, event and contact IDs are full URLs that the server later requests
with the user's Basic-auth credentials attached. Every such URL must point at
Apple's infrastructure (or the configured DAV servers), over HTTPS.
"""

from urllib.parse import urlparse

from .config import config

TRUSTED_SUFFIXES = (".icloud.com", ".me.com", ".apple.com")
TRUSTED_HOSTS = {"icloud.com", "me.com", "apple.com"}


def _configured_hosts() -> set[str]:
    hosts = set()
    for server in (config.CALDAV_SERVER, config.CARDDAV_SERVER):
        host = urlparse(server).hostname
        if host:
            hosts.add(host.lower())
    return hosts


def is_trusted_host(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    if not host:
        return False
    if host in TRUSTED_HOSTS or host in _configured_hosts():
        return True
    return any(host.endswith(suffix) for suffix in TRUSTED_SUFFIXES)


def ensure_icloud_url(url: str, what: str = "resource") -> str:
    """Return ``url`` if it is an https URL on a trusted iCloud host, else raise ValueError."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError(f"{what} URL/ID is empty")
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError(
            f"{what} must be a full https URL as returned by the list tools, got: {url!r}"
        )
    if parsed.username or parsed.password:
        raise ValueError(f"{what} URL must not contain credentials")
    if not is_trusted_host(parsed.hostname):
        raise ValueError(
            f"Refusing to use {what} URL on untrusted host {parsed.hostname!r}; "
            "only iCloud hosts are allowed."
        )
    return url.strip()
