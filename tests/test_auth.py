"""Token cache and refresh: same file format, no torn reads, no browser from the server."""

import json
import os
import stat
from pathlib import Path

import pytest

from spotify_mcp import auth, net
from spotify_mcp.errors import LoginRequired, from_exception
from tests.test_net import stale

TOKEN = {"access_token": "old-access", "token_type": "Bearer", "expires_in": 3600, "scope": " ".join(auth.SCOPES),
         "expires_at": 1, "refresh_token": "old-refresh"}


def manager(tmp_path: Path, fake_adapter, script):
    m = auth.make_auth_manager()
    m.cache_handler = auth.SafeCacheFileHandler(cache_path=str(tmp_path / "token"))
    session = net.ResilientSession(retry_unsafe=True)
    session.sleep = lambda s: None
    adapter = fake_adapter(script)
    session.mount("https://", adapter)
    m._session = session
    return m, adapter


def test_cache_format_is_spotipys(tmp_path):
    from spotipy.cache_handler import CacheFileHandler

    ours, theirs = tmp_path / "ours", tmp_path / "theirs"
    auth.SafeCacheFileHandler(cache_path=str(ours)).save_token_to_cache(TOKEN)
    CacheFileHandler(cache_path=str(theirs)).save_token_to_cache(TOKEN)
    assert ours.read_bytes() == theirs.read_bytes()
    assert stat.S_IMODE(os.stat(ours).st_mode) == 0o600
    assert auth.SafeCacheFileHandler(cache_path=str(theirs)).get_cached_token() == TOKEN
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []  # no temp file left


def test_half_written_cache_is_read_again(tmp_path, monkeypatch):
    path = tmp_path / "token"
    path.write_text(json.dumps(TOKEN))
    reads = iter(["", '{"access_tok', json.dumps(TOKEN)])
    monkeypatch.setattr(Path, "read_text", lambda self, encoding=None: next(reads))
    assert auth.SafeCacheFileHandler(cache_path=str(path)).get_cached_token() == TOKEN


def test_refresh_survives_a_stale_connection(tmp_path, fake_adapter):
    fresh = {"access_token": "new-access", "token_type": "Bearer", "expires_in": 3600, "scope": TOKEN["scope"]}
    m, adapter = manager(tmp_path, fake_adapter, [stale(), (200, fresh)])
    m.cache_handler.save_token_to_cache(TOKEN)
    assert m.get_access_token() == "new-access"
    assert len(adapter.sent) == 2 and adapter.sent[1].method == "POST"
    saved = json.loads((tmp_path / "token").read_text())
    assert saved["refresh_token"] == "old-refresh"  # kept when Spotify sends none
    assert list(saved) == list(TOKEN)


def test_refresh_has_a_timeout(tmp_path, fake_adapter):
    seen = []

    class Recorder(fake_adapter):
        def send(self, request, **kwargs):
            seen.append(kwargs.get("timeout"))
            return super().send(request, **kwargs)

    fresh = {"access_token": "new", "token_type": "Bearer", "expires_in": 3600, "scope": TOKEN["scope"]}
    m, _ = manager(tmp_path, fake_adapter, [])
    m._session.mount("https://", Recorder([(200, fresh)]))
    m.cache_handler.save_token_to_cache(TOKEN)
    m.get_access_token()
    assert seen == [(net.CONNECT_TIMEOUT, net.READ_TIMEOUT)]


def test_revoked_refresh_token_is_an_auth_error(tmp_path, fake_adapter):
    m, _ = manager(tmp_path, fake_adapter, [(400, {"error": "invalid_grant", "error_description": "Refresh token revoked"})])
    m.cache_handler.save_token_to_cache(TOKEN)
    with pytest.raises(Exception) as caught:
        m.get_access_token()
    error = from_exception(caught.value)
    assert error.code == "auth" and "--login" in error.message


def test_no_cache_never_opens_a_browser(tmp_path, fake_adapter, monkeypatch):
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda *a, **k: pytest.fail("opened a browser"))
    m, adapter = manager(tmp_path, fake_adapter, [])
    with pytest.raises(LoginRequired):
        m.get_access_token()
    assert adapter.sent == []


def test_force_refresh(tmp_path, fake_adapter):
    fresh = {"access_token": "forced", "token_type": "Bearer", "expires_in": 3600, "scope": TOKEN["scope"]}
    m, _ = manager(tmp_path, fake_adapter, [(200, fresh)])
    m.cache_handler.save_token_to_cache({**TOKEN, "expires_at": 2**40})
    m.force_refresh()
    assert m.cache_handler.get_cached_token()["access_token"] == "forced"


def test_login_recovers_from_a_revoked_refresh_token(monkeypatch):
    calls = []

    def fake_get_access_token(self, code=None, check_cache=True):
        calls.append(check_cache)
        if check_cache:
            from spotipy.oauth2 import SpotifyOauthError
            raise SpotifyOauthError("x", error="invalid_grant")
        return "new"

    monkeypatch.setattr(auth.ServerPKCE, "get_access_token", fake_get_access_token)
    auth.login()
    assert calls == [True, False]


def test_failed_save_leaves_no_temp_file(tmp_path):
    handler = auth.SafeCacheFileHandler(cache_path=str(tmp_path / "token"))
    with pytest.raises(TypeError):
        handler.save_token_to_cache({"bad": object()})
    assert list(tmp_path.iterdir()) == []
