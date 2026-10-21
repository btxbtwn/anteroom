from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

BLOCKED_HOSTS = frozenset({
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata.google",
    "instance-data",
})

METADATA_NETWORKS = (
    ipaddress.ip_network("169.254.169.254/32"),
    ipaddress.ip_network("fd00:ec2::254/128"),
)

MAX_REDIRECTS = 3
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
DOWNLOAD_TIMEOUT_SEC = 15.0


class UnsafeURLError(ValueError):
    pass


def is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if any(ip in network for network in METADATA_NETWORKS):
        return True
    return not ip.is_global


def resolve_public_host(host: str) -> list[str]:
    cleaned = (host or "").strip().lower().rstrip(".")
    if not cleaned:
        raise UnsafeURLError("URL is missing a hostname")
    if cleaned in BLOCKED_HOSTS or cleaned.endswith(".localhost"):
        raise UnsafeURLError(f"Host {cleaned} is not allowed")
    try:
        literal = ipaddress.ip_address(cleaned)
    except ValueError:
        literal = None
    if literal is not None:
        if is_blocked_ip(literal):
            raise UnsafeURLError(f"Address {cleaned} is not a public host")
        return [cleaned]
    try:
        results = socket.getaddrinfo(cleaned, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Could not resolve {cleaned}") from exc
    addresses: list[str] = []
    for item in results:
        sockaddr = item[4]
        if not sockaddr:
            continue
        ip_text = str(sockaddr[0])
        ip = ipaddress.ip_address(ip_text)
        if is_blocked_ip(ip):
            raise UnsafeURLError(f"Host {cleaned} resolves to a private or reserved address")
        if ip_text not in addresses:
            addresses.append(ip_text)
    if not addresses:
        raise UnsafeURLError(f"Host {cleaned} did not resolve to a public address")
    return addresses


def resolve_fetch_target(url: str, *, allow_http: bool = True) -> tuple[str, list[str]]:
    raw = (url or "").strip()
    if not raw:
        raise UnsafeURLError("URL is required")
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"https", "http"}:
        raise UnsafeURLError("Only HTTP and HTTPS URLs are allowed")
    if scheme == "http" and not allow_http:
        raise UnsafeURLError("Only HTTPS image URLs are allowed")
    if parsed.username or parsed.password:
        raise UnsafeURLError("URLs with credentials are not allowed")
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise UnsafeURLError("URL is missing a hostname")
    if parsed.port in {22, 25, 3306, 5432, 6379, 11211, 27017}:
        raise UnsafeURLError("URL port is not allowed")
    return raw, resolve_public_host(host)


def validate_fetch_url(url: str, *, allow_http: bool = True) -> str:
    raw, _ = resolve_fetch_target(url, allow_http=allow_http)
    return raw


def is_image_type(content_type: str) -> bool:
    """An image response, or one a CDN left untyped."""
    return not content_type or content_type.startswith("image/") or content_type in {
        "application/octet-stream", "binary/octet-stream",
    }


async def fetch_public(
    client: httpx.AsyncClient,
    url: str,
    *,
    allow_http: bool = False,
    accept: Callable[[str], bool] | None = None,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    headers: dict[str, str] | None = None,
) -> tuple[bytes, str, str]:
    """GET a public URL, connecting only to the address it was checked against.

    Every hop, redirects included, is resolved and checked before connecting,
    and the request goes to that pinned IP with the original Host and SNI, so
    DNS rebinding cannot swap in a private address. ``accept`` is a predicate
    on the response's content type; a response it refuses raises
    ``UnsafeURLError``. Returns ``(body, content_type, final_url)``.
    """
    current = str(url or "").strip()
    for _ in range(MAX_REDIRECTS + 1):
        current, addresses = resolve_fetch_target(current, allow_http=allow_http)
        parsed = urlparse(current)
        host = str(parsed.hostname or "")
        host_header = f"[{host}]" if ":" in host else host
        if parsed.port is not None:
            host_header = f"{host_header}:{parsed.port}"

        response = None
        last_connect_error = None
        for address in addresses:
            pinned_host = f"[{address}]" if ":" in address else address
            if parsed.port is not None:
                pinned_host = f"{pinned_host}:{parsed.port}"
            pinned_url = urlunparse(parsed._replace(netloc=pinned_host))
            request = client.build_request(
                "GET",
                pinned_url,
                headers={**(headers or {}), "Host": host_header, "Connection": "close"},
                extensions={"sni_hostname": host},
            )
            try:
                response = await client.send(request, stream=True, follow_redirects=False)
                break
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_connect_error = exc
        if response is None:
            if last_connect_error is not None:
                raise last_connect_error
            raise UnsafeURLError(f"Could not connect to {host}")

        try:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise UnsafeURLError("Redirect was missing a Location header")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
            if accept is not None and not accept(content_type):
                raise UnsafeURLError(f"URL did not return an accepted type ({content_type or 'none given'})")
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > max_bytes:
                    raise UnsafeURLError("Download exceeded the size limit")
            return bytes(content), content_type, current
        finally:
            await response.aclose()
    raise UnsafeURLError("Too many redirects")
