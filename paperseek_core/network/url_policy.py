from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Callable, Iterable, Tuple
from urllib.parse import urlsplit


class EndpointPolicyError(ValueError):
    """Raised when a client-controlled outbound endpoint is unsafe."""


@dataclass(frozen=True)
class ValidatedEndpoint:
    url: str
    host: str
    port: int
    addresses: Tuple[str, ...]


Resolver = Callable[[str, int], Iterable[str]]


def _default_resolver(host: str, port: int) -> Iterable[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def _public_address(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_global
    except ValueError:
        return False


def validate_outbound_url(
    url: str,
    *,
    allow_http: bool = False,
    resolver: Resolver | None = None,
) -> ValidatedEndpoint:
    value = (url or "").strip()
    if not value:
        raise EndpointPolicyError("Custom endpoint URL is required.")
    if len(value) > 2048:
        raise EndpointPolicyError("Custom endpoint URL is too long.")

    parsed = urlsplit(value)
    allowed_schemes = {"https"}
    if allow_http:
        allowed_schemes.add("http")
    if parsed.scheme.lower() not in allowed_schemes:
        raise EndpointPolicyError("Custom endpoint must use HTTPS.")
    if parsed.username is not None or parsed.password is not None:
        raise EndpointPolicyError("Custom endpoint must not contain URL credentials.")
    if parsed.fragment:
        raise EndpointPolicyError("Custom endpoint must not contain a fragment.")
    if parsed.query:
        raise EndpointPolicyError("Custom endpoint must not contain a query string.")

    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise EndpointPolicyError("Custom endpoint hostname is required.")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise EndpointPolicyError("Local endpoint hostnames are not allowed.")
    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise EndpointPolicyError("Custom endpoint port is invalid.") from exc

    resolver_fn = resolver or _default_resolver
    try:
        addresses = tuple(dict.fromkeys(str(item) for item in resolver_fn(host, port)))
    except (OSError, socket.gaierror) as exc:
        raise EndpointPolicyError("Custom endpoint hostname could not be resolved.") from exc
    if not addresses:
        raise EndpointPolicyError("Custom endpoint hostname did not resolve to an address.")
    if any(not _public_address(address) for address in addresses):
        raise EndpointPolicyError("Custom endpoint resolves to a non-public network address.")

    return ValidatedEndpoint(url=value.rstrip("/"), host=host, port=port, addresses=addresses)
