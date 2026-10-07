"""HTTP transport for the Spotify Web API and the token endpoint.

spotipy's default session cannot survive two things this server meets every day:

* Stale keep-alive connections. Spotify's edge closes a pooled connection after about ten
  minutes idle, and a request sent on it at that moment fails at once with
  ``RemoteDisconnected``. spotipy's urllib3 ``Retry`` has ``read=False``, so it never retries
  that (urllib3 classes it as a read error).
* A broken IPv6 route. Python connects to each DNS address in turn and waits the full connect
  timeout on each; with IPv6 listed first, every new connection costs that timeout, and the token
  endpoint (no timeout at all in spotipy's PKCE manager) can hang for minutes.

``ResilientSession`` fixes both: it drops pooled connections that sat idle too long, retries
connection-level failures on a fresh connection when that is safe for the HTTP method, honours a
short ``Retry-After`` on 429, and keeps every tool call inside one time budget.
``_FastConnection`` tries IPv4 addresses first and gives each address only a short connect
timeout before moving on to the next.
"""

from __future__ import annotations

import contextlib
import contextvars
import email.utils
import http.client
import logging
import os
import socket
import time
from collections.abc import Iterator
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import (
    ConnectTimeoutError,
    NameResolutionError,
    NewConnectionError,
    ProtocolError,
)
from urllib3.util import connection as u3_connection

log = logging.getLogger("spotify_mcp.net")


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


# Seconds. All of these can be overridden from the environment (see README).
CONNECT_TIMEOUT = _env_float("SPOTIFY_MCP_CONNECT_TIMEOUT", 3.0)
READ_TIMEOUT = _env_float("SPOTIFY_MCP_READ_TIMEOUT", 8.0)
CALL_TIMEOUT = _env_float("SPOTIFY_MCP_CALL_TIMEOUT", 15.0)
IDLE_RESET = _env_float("SPOTIFY_MCP_IDLE_RESET", 120.0)
MAX_RETRY_AFTER = _env_float("SPOTIFY_MCP_MAX_RETRY_AFTER", 5.0)
PER_ADDRESS_CONNECT = 1.5  # before giving up on one address and trying the next

NETWORK_RETRIES = 2  # extra attempts after a connection-level failure
STATUS_RETRIES = 2  # extra attempts after a 429 or a 5xx

# Methods retried after a failure that may have reached Spotify (connection reset or closed
# mid-request, read timeout). Spotify's GETs are reads; its PUTs and DELETEs set state (play,
# pause, volume, shuffle, repeat, seek, transfer, save or remove tracks), so sending one twice
# leaves the same result. POST is left out: POST /me/player/next twice skips two tracks, and
# POST /me/player/queue or /playlists/{id}/tracks twice adds the item twice.
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})
RETRY_STATUSES = frozenset({500, 502, 503, 504})

NOT_SENT = "not_sent"  # failed before the request left this machine: safe to retry any method
MAYBE_SENT = "maybe_sent"  # failed after sending: retry only idempotent methods


class CallTimeout(requests.exceptions.Timeout):
    """The tool call used up its time budget."""


_deadline: contextvars.ContextVar[float | None] = contextvars.ContextVar("spotify_mcp_deadline", default=None)


def _clock() -> float:
    """Seconds on a clock that keeps counting through suspend (idle time includes sleep)."""
    try:
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    except (AttributeError, OSError):
        return time.monotonic()


@contextlib.contextmanager
def call_budget(seconds: float | None = None) -> Iterator[None]:
    """Every HTTP request made inside this block shares one deadline."""
    token = _deadline.set(time.monotonic() + (seconds if seconds is not None else CALL_TIMEOUT))
    try:
        yield
    finally:
        _deadline.reset(token)


def time_left() -> float | None:
    deadline = _deadline.get()
    return None if deadline is None else deadline - time.monotonic()


# --- connecting: IPv4 first, a short timeout per address ---------------------------------


def _addresses(host: str, port: int) -> list[tuple[Any, ...]]:
    infos = socket.getaddrinfo(host, port, u3_connection.allowed_gai_family(), socket.SOCK_STREAM)
    ipv4 = [info for info in infos if info[0] == socket.AF_INET]
    return ipv4 + [info for info in infos if info[0] != socket.AF_INET]


def create_connection(
    address: tuple[str, int],
    timeout: Any = None,
    source_address: tuple[str, int] | None = None,
    socket_options: Any = None,
) -> socket.socket:
    """Like urllib3's create_connection, but IPv4 first and a short timeout per address.

    The last address gets the whole connect timeout; the ones before it get at most
    PER_ADDRESS_CONNECT seconds, so one unreachable family costs about a second, not the
    whole timeout (or, with no timeout, the kernel's two minutes of SYN retries).
    """
    host, port = address
    host = host.strip("[]")
    infos = _addresses(host, port)
    if not infos:
        raise OSError("getaddrinfo returned no addresses")
    full = timeout if isinstance(timeout, (int, float)) else None
    error: OSError | None = None
    for index, (family, socktype, proto, _name, sockaddr) in enumerate(infos):
        last = index == len(infos) - 1
        sock = None
        try:
            sock = socket.socket(family, socktype, proto)
            u3_connection._set_socket_options(sock, socket_options)
            per_address = full if last else min(full or PER_ADDRESS_CONNECT, PER_ADDRESS_CONNECT)
            sock.settimeout(per_address)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            sock.settimeout(full)
            return sock
        except OSError as exc:
            error = exc
            if sock is not None:
                sock.close()
    assert error is not None
    raise error


class _ConnectMixin:
    def _new_conn(self) -> socket.socket:  # same exception mapping as urllib3's own
        try:
            return create_connection(
                (self._dns_host, self.port), self.timeout,  # type: ignore[attr-defined]
                source_address=self.source_address,  # type: ignore[attr-defined]
                socket_options=self.socket_options,  # type: ignore[attr-defined]
            )
        except socket.gaierror as exc:
            raise NameResolutionError(self.host, self, exc) from exc  # type: ignore[attr-defined,arg-type]
        except TimeoutError as exc:
            raise ConnectTimeoutError(self, f"Connection to {self.host} timed out.") from exc  # type: ignore[attr-defined]
        except OSError as exc:
            raise NewConnectionError(self, f"Failed to establish a new connection: {exc}") from exc  # type: ignore[arg-type]


class _FastHTTPConnection(_ConnectMixin, HTTPConnection):
    pass


class _FastHTTPSConnection(_ConnectMixin, HTTPSConnection):
    pass


class _HTTPPool(HTTPConnectionPool):
    ConnectionCls = _FastHTTPConnection


class _HTTPSPool(HTTPSConnectionPool):
    ConnectionCls = _FastHTTPSConnection


class _Adapter(HTTPAdapter):
    """No urllib3 retries (ResilientSession decides) and the fast connection classes."""

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {"http": _HTTPPool, "https": _HTTPSPool}


# --- classifying failures ----------------------------------------------------------------


def _causes(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    stack: list[Any] = [exc]
    while stack:
        item = stack.pop()
        if not isinstance(item, BaseException) or id(item) in seen:
            continue
        seen.add(id(item))
        yield item
        stack.extend([item.__cause__, item.__context__, getattr(item, "reason", None)])
        stack.extend(arg for arg in item.args if isinstance(arg, BaseException))


def failure_kind(exc: BaseException) -> str | None:
    """NOT_SENT, MAYBE_SENT, or None when retrying would not help (TLS errors, bad URLs, ...)."""
    if isinstance(exc, CallTimeout) or isinstance(exc, requests.exceptions.SSLError):
        return None
    causes = list(_causes(exc))
    if isinstance(exc, requests.exceptions.ConnectTimeout) or any(
        isinstance(c, (NewConnectionError, NameResolutionError, ConnectTimeoutError)) for c in causes
    ):
        return NOT_SENT
    if isinstance(exc, (requests.exceptions.ReadTimeout, requests.exceptions.ChunkedEncodingError)):
        return MAYBE_SENT
    if isinstance(exc, requests.exceptions.ConnectionError) and any(
        isinstance(c, (ProtocolError, http.client.RemoteDisconnected, ConnectionResetError,
                       ConnectionAbortedError, BrokenPipeError))
        for c in causes
    ):
        return MAYBE_SENT
    return None


def retry_after_seconds(response: requests.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, when.timestamp() - time.time())


def _short(exc: BaseException) -> str:
    leaf = [c for c in _causes(exc)][-1]
    return f"{type(exc).__name__}/{type(leaf).__name__}"


# --- the session -------------------------------------------------------------------------


class ResilientSession(requests.Session):
    """A requests Session that survives stale connections and keeps calls bounded.

    retry_unsafe: also retry POSTs after a failure that may have reached the server. Only for
    the token endpoint, where a repeated refresh is no worse than the next call retrying it.
    """

    def __init__(self, *, retry_unsafe: bool = False) -> None:
        super().__init__()
        self.retry_unsafe = retry_unsafe
        self.sleep = time.sleep  # tests replace this
        self._last_used: float | None = None
        adapter = _Adapter(pool_maxsize=4)
        self.mount("https://", adapter)
        self.mount("http://", adapter)

    def reset_connections(self) -> None:
        """Close every pooled connection; the next request opens a fresh one."""
        for adapter in self.adapters.values():
            adapter.close()

    def _timeout(self, left: float | None) -> tuple[float, float]:
        if left is None:
            return (CONNECT_TIMEOUT, READ_TIMEOUT)
        return (max(0.1, min(CONNECT_TIMEOUT, left)), max(0.1, min(READ_TIMEOUT, left)))

    def _fits(self, wait: float) -> bool:
        left = time_left()
        return left is None or wait + 0.5 < left

    def request(self, method: str | bytes, url: str | bytes, *args: Any, **kwargs: Any) -> requests.Response:  # type: ignore[override]
        verb = (method.decode() if isinstance(method, bytes) else method).upper()
        replayable = verb in IDEMPOTENT_METHODS or self.retry_unsafe
        where = str(url).split("?", 1)[0]
        network_tries = status_tries = 0
        while True:
            now = _clock()
            if self._last_used is not None and now - self._last_used > IDLE_RESET:
                self.reset_connections()
            left = time_left()
            if left is not None and left <= 0.2:
                raise CallTimeout("the call ran out of time")
            kwargs["timeout"] = self._timeout(left)
            try:
                response = super().request(verb, url, *args, **kwargs)
            except requests.exceptions.RequestException as exc:
                self._last_used = None
                kind = failure_kind(exc)
                wait = 0.0 if network_tries == 0 else 0.3
                if (network_tries < NETWORK_RETRIES and (kind == NOT_SENT or (kind == MAYBE_SENT and replayable))
                        and self._fits(wait)):
                    network_tries += 1
                    log.warning("%s %s failed (%s); retrying on a fresh connection", verb, where, _short(exc))
                    self.reset_connections()
                    if wait:
                        self.sleep(wait)
                    continue
                raise
            self._last_used = _clock()

            status = response.status_code
            if status == 429 or (status in RETRY_STATUSES and replayable):
                # A 429 was refused before Spotify acted on it, so any method may repeat it.
                if status == 429:
                    wait = retry_after_seconds(response)
                    wait = 1.0 if wait is None else wait
                else:
                    wait = 0.5 * (status_tries + 1)
                if status_tries < STATUS_RETRIES and wait <= MAX_RETRY_AFTER and self._fits(wait):
                    status_tries += 1
                    log.warning("%s %s returned %s; retrying in %.1f s", verb, where, status, wait)
                    response.close()
                    self.sleep(wait)
                    continue
            return response
