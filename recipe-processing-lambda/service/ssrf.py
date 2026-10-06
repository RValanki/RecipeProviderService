"""
SSRF guards for user-supplied import URLs.

Two surfaces are protected:
  • Media imports (TikTok / Instagram) — host allow-list: the URL must be on the
    platform's own domain.
  • Generic web recipe imports — can't be allow-listed (a recipe lives on any
    blog), so instead we block URLs that resolve to private/reserved/internal
    addresses, non-standard ports, or non-http(s) schemes.

Used by the producer (early reject before enqueue) and the worker (redirect-safe
fetch, defense in depth).
"""
import re
import socket
import ipaddress
from urllib.parse import urlparse, urljoin

# Media imports may only ever hit these domains.
_TIKTOK_HOSTS = (".tiktok.com",)
_INSTAGRAM_HOSTS = (".instagram.com",)
# Standard web ports allowed for generic recipe fetches (None = default port).
_ALLOWED_PORTS = {80, 443, None}

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


class UnsafeURLError(Exception):
    """Raised when an import URL is disallowed (bad host, private target, etc.)."""
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def extract_url(text: str) -> str | None:
    match = _URL_RE.search(text or "")
    return match.group(0) if match else None


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _host_matches(host: str, suffixes) -> bool:
    return any(host == s.lstrip(".") or host.endswith(s) for s in suffixes)


def _resolves_to_private(host: str) -> bool:
    """True if the host is, or resolves to, any private/reserved/loopback/
    link-local address. Unresolvable hosts are treated as unsafe."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return True
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return True
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return True
    return False


def platform_for_url(url: str) -> str | None:
    """Return 'tiktok' or 'instagram' when the URL's HOST is that platform's own
    domain, else None. Host-based (never substring) so a public URL that merely
    mentions 'tiktok.com'/'instagram.com' in a path or query isn't misrouted to
    the media (yt-dlp) path. Callers use this to route; the same host rule backs
    the SSRF allow-list, so routing and the guard can never disagree."""
    host = _host(url)
    if _host_matches(host, _TIKTOK_HOSTS):
        return "tiktok"
    if _host_matches(host, _INSTAGRAM_HOSTS):
        return "instagram"
    return None


def assert_safe_media_host(url: str, platform: str) -> None:
    """platform: 'tiktok' | 'instagram'. Raises if the host isn't that platform's
    own domain."""
    host = _host(url)
    suffixes = _TIKTOK_HOSTS if platform == "tiktok" else _INSTAGRAM_HOSTS
    if not host or not _host_matches(host, suffixes):
        raise UnsafeURLError(f"Host '{host}' is not an allowed {platform} domain")


def assert_safe_web_url(url: str) -> None:
    """Generic web-fetch guard: http(s) only, standard port, and not a
    private/reserved/internal target."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURLError("Only http(s) URLs are allowed")
    host = (parsed.hostname or "").lower()
    if not host:
        raise UnsafeURLError("URL has no host")
    try:
        if parsed.port not in _ALLOWED_PORTS:
            raise UnsafeURLError(f"Port {parsed.port} is not allowed")
    except ValueError:
        raise UnsafeURLError("Invalid port in URL")
    if _resolves_to_private(host):
        raise UnsafeURLError("URL resolves to a private or disallowed address")


def assert_safe_import(user_input: str) -> None:
    """Validate any URL contained in the import input. No-op for pure-text imports
    (no URL present). Classifies by HOST (not substring) so a public URL that
    merely mentions 'tiktok.com' in a path/query isn't misrouted."""
    url = extract_url(user_input)
    if not url:
        return
    host = _host(url)
    if _host_matches(host, _TIKTOK_HOSTS):
        assert_safe_media_host(url, "tiktok")
    elif _host_matches(host, _INSTAGRAM_HOSTS):
        assert_safe_media_host(url, "instagram")
    else:
        assert_safe_web_url(url)


def safe_get(url: str, headers: dict | None = None, timeout: int = 15, max_redirects: int = 5):
    """requests.get that validates the host isn't private at EVERY hop — follows
    redirects manually so a 3xx to an internal address can't bypass the check.
    Raises UnsafeURLError on an unsafe (initial or redirected) target."""
    import requests  # lazy: the producer imports this module but doesn't need requests

    current = url
    for _ in range(max_redirects + 1):
        assert_safe_web_url(current)
        resp = requests.get(current, headers=headers, timeout=timeout, allow_redirects=False)
        if resp.is_redirect or resp.is_permanent_redirect:
            location = resp.headers.get("Location")
            if not location:
                return resp
            current = urljoin(current, location)
            continue
        return resp
    raise UnsafeURLError("Too many redirects")
