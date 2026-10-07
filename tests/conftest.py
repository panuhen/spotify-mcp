"""Test isolation: a throwaway HOME (no real token cache or favorites) and no internet."""

import os
import socket
import tempfile

# Before spotify_mcp is imported: its token cache and favorites paths come from Path.home().
os.environ["HOME"] = tempfile.mkdtemp(prefix="spotify-mcp-test-home-")
for name in list(os.environ):
    if name.startswith(("SPOTIFY_MCP_", "SPOTIPY_")):
        del os.environ[name]

import json  # noqa: E402

import pytest  # noqa: E402
import requests  # noqa: E402
from requests.adapters import HTTPAdapter  # noqa: E402

_real_getaddrinfo = socket.getaddrinfo


@pytest.fixture(autouse=True)
def no_internet(monkeypatch):
    """Only loopback and documentation addresses resolve; tests never reach Spotify."""

    def guarded(host, *args, **kwargs):
        if host not in ("localhost", "127.0.0.1", "::1"):
            raise socket.gaierror(f"tests may not resolve {host!r}")
        return _real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded)


class FakeAdapter(HTTPAdapter):
    """Plays a script: each step is an exception to raise or (status, json_body, headers)."""

    def __init__(self, script):
        super().__init__()
        self.script = list(script)
        self.sent = []
        self.closed = 0

    def send(self, request, **kwargs):
        self.sent.append(request)
        if not self.script:
            raise AssertionError(f"unexpected request: {request.method} {request.url}")
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        status, body, headers = (step + (None,))[:3] if len(step) == 2 else step
        response = requests.Response()
        response.status_code = status
        response._content = b"" if body is None else json.dumps(body).encode()
        response.headers.update(headers or {})
        response.url = request.url
        response.request = request
        response.encoding = "utf-8"
        return response

    def close(self):
        self.closed += 1


@pytest.fixture
def fake_adapter():
    return FakeAdapter
