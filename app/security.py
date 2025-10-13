from urllib.parse import urlparse
import socket
import ipaddress
import bleach


ALLOWED_URL_SCHEMES = {"http", "https"}
ALLOWED_HTML_TAGS = [
    "b", "i", "u", "a", "strong", "em", "p", "br",
    "ul", "ol", "li", "code", "pre", "h1", "h2", "h3"
]
ALLOWED_HTML_ATTRS = {"a": ["href", "title", "target", "rel"]}
ALLOWED_HTML_PROTOCOLS = ["http", "https", "mailto", "tel"]


def _is_private_ip(host: str) -> bool:
    try:
        # Resolve host to all IPs and check any are private/loopback/link-local/etc
        infos = socket.getaddrinfo(host, None)
        for family, _, _, _, sockaddr in infos:
            ip_str = sockaddr[0]
            ip = ipaddress.ip_address(ip_str)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
                or ip.is_unspecified
            ):
                return True
        return False
    except Exception:
        # On resolution failure, treat as unsafe
        return True


def is_http_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme in ALLOWED_URL_SCHEMES and bool(p.netloc)
    except Exception:
        return False


def is_url_safe_for_fetch(url: str) -> bool:
    """Validate URL for server-side fetching/downloading to mitigate SSRF.
    - Scheme must be http/https
    - Host must resolve to non-private/non-loopback IP
    """
    if not is_http_url(url):
        return False
    try:
        p = urlparse(url)
        host = p.hostname or ""
        if not host:
            return False
        if _is_private_ip(host):
            return False
        return True
    except Exception:
        return False


def sanitize_html(value: str) -> str:
    return bleach.clean(
        value or "",
        tags=ALLOWED_HTML_TAGS,
        attributes=ALLOWED_HTML_ATTRS,
        protocols=ALLOWED_HTML_PROTOCOLS,
        strip=True,
    )


def sanitize_text(value: str) -> str:
    # Plain-text sanitize: strip tags and trim whitespace
    return bleach.clean(value or "", strip=True).strip()
