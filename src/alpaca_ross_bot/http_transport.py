"""Bounded read-only HTTPS transport for fixed Alpaca data/API hosts."""

import time
from collections.abc import Mapping
from http.client import HTTPException, HTTPSConnection
from urllib.parse import urlsplit


class HttpTransportError(RuntimeError):
    """A read-only request violates host, size, status, or time bounds."""


_ALLOWED_HOSTS = frozenset({"data.alpaca.markets", "paper-api.alpaca.markets"})


def bounded_alpaca_get(
    url: str,
    headers: Mapping[str, str],
    timeout_seconds: float,
    max_bytes: int,
) -> bytes:
    """GET from an allowlisted Alpaca host under a total deadline and byte cap."""
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _ALLOWED_HOSTS
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith("/")
        or parsed.fragment
    ):
        raise HttpTransportError("request URL is outside the Alpaca read-only hosts")
    if timeout_seconds <= 0 or max_bytes < 1:
        raise HttpTransportError("request bounds must be positive")

    deadline = time.monotonic() + timeout_seconds
    connection = HTTPSConnection(parsed.hostname, timeout=timeout_seconds)
    try:
        connection.connect()
        _set_remaining_timeout(connection, deadline)
        path = parsed.path
        if parsed.query:
            path += "?" + parsed.query
        connection.request("GET", path, headers=dict(headers))
        _set_remaining_timeout(connection, deadline)
        response = connection.getresponse()
        if response.status != 200:
            raise HttpTransportError(f"Alpaca REST returned HTTP {response.status}")
        content_length = response.getheader("Content-Length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError as exc:
                raise HttpTransportError("invalid response content length") from exc
            if declared_length < 0 or declared_length > max_bytes:
                raise HttpTransportError("REST response byte budget exceeded")

        body = bytearray()
        while True:
            _set_remaining_timeout(connection, deadline)
            chunk = response.read1(min(65_536, max_bytes + 1 - len(body)))
            if not chunk:
                return bytes(body)
            body.extend(chunk)
            if len(body) > max_bytes:
                raise HttpTransportError("REST response byte budget exceeded")
    except (HTTPException, TimeoutError, OSError) as exc:
        raise HttpTransportError("Alpaca REST request failed or timed out") from exc
    finally:
        connection.close()


def _set_remaining_timeout(connection: HTTPSConnection, deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise HttpTransportError("REST request deadline expired")
    if connection.sock is None:
        raise HttpTransportError("REST connection is unavailable")
    connection.sock.settimeout(remaining)
