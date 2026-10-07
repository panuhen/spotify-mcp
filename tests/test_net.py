"""Transport: stale keep-alive connections, resets, 429, time budget, address order."""

import http.client
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests
from requests.adapters import HTTPAdapter
from urllib3.exceptions import ProtocolError
from urllib3.util.retry import Retry

from spotify_mcp import net


def stale(error=None):
    """What requests raises when the server closed a pooled connection as the request went out."""
    error = error or http.client.RemoteDisconnected("Remote end closed connection without response")
    return requests.exceptions.ConnectionError(ProtocolError("Connection aborted.", error))


class _Handler(BaseHTTPRequestHandler):
    """HTTP/1.1 keep-alive server that, like Spotify's edge after ~10 idle minutes, can close a
    reused connection when the next request arrives, without answering it."""

    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.served = 0
        self.server.connections += 1

    def _answer(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.server.requests += 1
        if self.server.drop_reused and self.served > 0:
            self.server.dropped += 1
            self.close_connection = True
            return
        self.served += 1
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_PUT = do_POST = _answer

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.daemon_threads = True
    httpd.connections = httpd.requests = httpd.dropped = 0
    httpd.drop_reused = False
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def _url(httpd, path="/v1/me/player"):
    return f"http://127.0.0.1:{httpd.server_address[1]}{path}"


def _spotipy_default_session():
    """The session spotipy builds for itself (spotipy.client.Spotify._build_session)."""
    session = requests.Session()
    retry = Retry(total=3, connect=None, read=False, allowed_methods=frozenset(["GET", "POST", "PUT", "DELETE"]),
                  status=3, backoff_factor=0.3, status_forcelist=(429, 500, 502, 503, 504))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


def test_spotipy_default_session_fails_on_a_closed_keepalive_connection(server):
    """The bug, reproduced against a real socket: spotipy's Retry(read=False) gives up at once."""
    session = _spotipy_default_session()
    assert session.get(_url(server), timeout=5).json() == {"ok": True}
    server.drop_reused = True
    with pytest.raises(requests.exceptions.ConnectionError) as caught:
        session.get(_url(server), timeout=5)
    assert "RemoteDisconnected" in repr(caught.value)


def test_get_is_retried_on_a_fresh_connection(server):
    session = net.ResilientSession()
    assert session.get(_url(server)).json() == {"ok": True}
    server.drop_reused = True
    assert session.get(_url(server)).json() == {"ok": True}
    assert server.dropped == 1
    assert server.connections == 2


def test_put_player_command_is_retried(server):
    session = net.ResilientSession()
    session.get(_url(server))
    server.drop_reused = True
    assert session.put(_url(server, "/v1/me/player/pause")).status_code == 200
    assert server.connections == 2


def test_post_is_not_retried_after_it_may_have_been_sent(server, monkeypatch):
    """POST /me/player/next twice would skip two tracks, so a maybe-sent POST is not repeated."""
    monkeypatch.setattr(net, "IDLE_RESET", 3600.0)
    session = net.ResilientSession()
    session.get(_url(server))
    server.drop_reused = True
    with pytest.raises(requests.exceptions.ConnectionError):
        session.post(_url(server, "/v1/me/player/next"))
    assert server.requests == 2  # the first GET and the one POST; no second POST


def test_token_session_retries_post(server):
    session = net.ResilientSession(retry_unsafe=True)
    session.get(_url(server))
    server.drop_reused = True
    assert session.post(_url(server, "/api/token"), data={"grant_type": "refresh_token"}).status_code == 200


def test_idle_connections_are_dropped_before_spotify_drops_them(server, monkeypatch):
    """After IDLE_RESET seconds idle the pool is emptied, so even a POST goes out on a new connection."""
    clock = [1000.0]
    monkeypatch.setattr(net, "_clock", lambda: clock[0])
    session = net.ResilientSession()
    session.get(_url(server))
    server.drop_reused = True
    clock[0] += net.IDLE_RESET + 1
    assert session.post(_url(server, "/v1/me/player/next")).status_code == 200
    assert server.dropped == 0
    assert server.connections == 2


def test_connection_reset_is_retried(fake_adapter):
    reset = stale(ConnectionResetError(104, "Connection reset by peer"))
    adapter = fake_adapter([reset, (200, {"ok": True})])
    session = net.ResilientSession()
    session.mount("https://", adapter)
    assert session.get("https://api.spotify.com/v1/me/player").json() == {"ok": True}
    assert len(adapter.sent) == 2
    assert adapter.closed >= 1  # pool reset before the retry


def test_connect_failure_is_retried_even_for_post(fake_adapter):
    from urllib3.exceptions import NewConnectionError

    refused = requests.exceptions.ConnectionError(NewConnectionError(None, "Connection refused"))
    adapter = fake_adapter([refused, (204, None)])
    session = net.ResilientSession()
    session.mount("https://", adapter)
    assert session.post("https://api.spotify.com/v1/me/player/next").status_code == 204


def test_network_retries_are_bounded(fake_adapter):
    adapter = fake_adapter([stale(), stale(), stale(), stale()])
    session = net.ResilientSession()
    session.sleep = lambda s: None
    session.mount("https://", adapter)
    with pytest.raises(requests.exceptions.ConnectionError):
        session.get("https://api.spotify.com/v1/me/player")
    assert len(adapter.sent) == 1 + net.NETWORK_RETRIES


def test_429_with_short_retry_after_waits_and_retries(fake_adapter):
    adapter = fake_adapter([(429, {"error": {"status": 429}}, {"Retry-After": "2"}), (200, {"ok": True})])
    session = net.ResilientSession()
    slept = []
    session.sleep = slept.append
    session.mount("https://", adapter)
    assert session.post("https://api.spotify.com/v1/me/player/queue").status_code == 200
    assert slept == [2.0]


def test_429_with_long_retry_after_returns_at_once(fake_adapter):
    adapter = fake_adapter([(429, {"error": {"status": 429}}, {"Retry-After": "3600"})])
    session = net.ResilientSession()
    session.sleep = lambda s: pytest.fail("must not wait an hour")
    session.mount("https://", adapter)
    assert session.get("https://api.spotify.com/v1/search").status_code == 429


def test_5xx_retried_for_get_not_for_post(fake_adapter):
    adapter = fake_adapter([(503, None), (200, {"ok": True})])
    session = net.ResilientSession()
    session.sleep = lambda s: None
    session.mount("https://", adapter)
    assert session.get("https://api.spotify.com/v1/me").status_code == 200

    adapter = fake_adapter([(503, None)])
    session.mount("https://", adapter)
    assert session.post("https://api.spotify.com/v1/me/player/next").status_code == 503


def test_call_budget_bounds_the_whole_call(fake_adapter):
    timeouts = []

    class Slow(fake_adapter):
        def send(self, request, **kwargs):
            timeouts.append(kwargs["timeout"])
            time.sleep(0.3)
            raise requests.exceptions.ReadTimeout("read timed out")

    session = net.ResilientSession()
    session.sleep = lambda s: None
    session.mount("https://", Slow([]))
    started = time.monotonic()
    with net.call_budget(1.0):
        with pytest.raises(requests.exceptions.Timeout):
            session.get("https://api.spotify.com/v1/me/player")
    assert time.monotonic() - started < 1.5
    assert all(read <= 1.0 for _connect, read in timeouts)


def test_out_of_budget_raises_call_timeout(fake_adapter):
    session = net.ResilientSession()
    session.mount("https://", fake_adapter([]))
    with net.call_budget(0.1):
        with pytest.raises(net.CallTimeout):
            session.get("https://api.spotify.com/v1/me/player")


def test_failure_kinds():
    from urllib3.exceptions import NewConnectionError

    assert net.failure_kind(stale()) == net.MAYBE_SENT
    assert net.failure_kind(requests.exceptions.ReadTimeout()) == net.MAYBE_SENT
    assert net.failure_kind(requests.exceptions.ConnectTimeout()) == net.NOT_SENT
    assert net.failure_kind(requests.exceptions.ConnectionError(NewConnectionError(None, "x"))) == net.NOT_SENT
    assert net.failure_kind(requests.exceptions.SSLError()) is None
    assert net.failure_kind(requests.exceptions.InvalidURL()) is None


def test_ipv4_is_tried_before_ipv6(monkeypatch, server):
    """A broken IPv6 route must not cost the connect timeout on every new connection."""
    port = server.server_address[1]
    tried = []

    def fake_getaddrinfo(host, port_, family=0, type_=0, *args):
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", port_, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port_)),
        ]

    real_connect = socket.socket.connect

    class Spy(socket.socket):
        def connect(self, address):
            tried.append(address[0])
            return real_connect(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(socket, "socket", Spy)
    sock = net.create_connection(("api.example", port), 3.0)
    sock.close()
    assert tried == ["127.0.0.1"]


def test_unreachable_address_costs_a_short_timeout(monkeypatch, server):
    port = server.server_address[1]
    calls = []

    class Blackhole(socket.socket):
        def connect(self, address):
            calls.append((address[0], self.gettimeout()))
            if address[0] == "192.0.2.1":
                raise TimeoutError("timed out")
            return super().connect(address)

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", port)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port)),
    ])
    monkeypatch.setattr(socket, "socket", Blackhole)
    net.create_connection(("api.example", port), 5.0).close()
    assert calls == [("192.0.2.1", net.PER_ADDRESS_CONNECT), ("127.0.0.1", 5.0)]


def test_session_uses_fast_connections(server):
    session = net.ResilientSession()
    session.get(_url(server))
    pool = session.get_adapter("http://").poolmanager.connection_from_url(_url(server))
    assert pool.ConnectionCls is net._FastHTTPConnection


def test_retry_after_parsing():
    response = requests.Response()
    response.headers["Retry-After"] = "7"
    assert net.retry_after_seconds(response) == 7.0
    response.headers["Retry-After"] = "soon"
    assert net.retry_after_seconds(response) is None


def test_connect_stays_inside_the_call_budget(monkeypatch):
    timeouts = []

    class Blackhole(socket.socket):
        def connect(self, address):
            timeouts.append(self.gettimeout())
            raise TimeoutError("timed out")

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (f"192.0.2.{i}", 443)) for i in range(1, 5)])
    monkeypatch.setattr(socket, "socket", Blackhole)
    with net.call_budget(0.5):
        with pytest.raises(TimeoutError):
            net.create_connection(("api.example", 443), 3.0)
    assert all(t <= 0.5 for t in timeouts)


def test_body_read_timeout_is_retried_for_get():
    from urllib3.exceptions import ReadTimeoutError

    exc = requests.exceptions.ConnectionError(ReadTimeoutError(None, "/v1/me", "Read timed out."))
    assert net.failure_kind(exc) == net.MAYBE_SENT
