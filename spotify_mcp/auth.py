"""OAuth token management for Spotify API using PKCE (no client secret needed)."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import spotipy
from spotipy.cache_handler import CacheFileHandler
from spotipy.oauth2 import SpotifyPKCE

from . import net
from .errors import LoginRequired

log = logging.getLogger("spotify_mcp.auth")

# Bundled client ID - users don't need their own Spotify Developer account
# This is safe to share publicly (PKCE flow doesn't use client secret)
DEFAULT_CLIENT_ID = "1f14edc73f6548dc97f7791dfec833aa"

# Required scopes for full playback control
SCOPES = [
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
    "playlist-read-private",
    "playlist-modify-public",
    "playlist-modify-private",
    "user-library-read",
    "user-library-modify",
]

TOKEN_CACHE_PATH = Path.home() / ".spotify-mcp-token"


class SafeCacheFileHandler(CacheFileHandler):
    """spotipy's file cache, made safe for several server processes sharing one file.

    The format is unchanged (the same json.dumps of the token dict). Writes go to a temporary
    file that replaces the cache in one step, so a reader never sees a half-written file, and a
    read that lands on another writer's half-written file (spotipy's own writer truncates first)
    is retried instead of being taken as "no token".
    """

    def get_cached_token(self) -> dict[str, Any] | None:
        path = Path(self.cache_path)
        for attempt in range(3):
            try:
                text = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return None
            except OSError as exc:
                log.warning("could not read the token cache (%s)", type(exc).__name__)
                return None
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                time.sleep(0.05 * (attempt + 1))
        log.warning("the token cache is not valid JSON")
        return None

    def save_token_to_cache(self, token_info: dict[str, Any]) -> None:
        path = Path(os.path.realpath(self.cache_path))
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(token_info, cls=self.encoder_cls))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except OSError as exc:
            log.warning("could not write the token cache (%s)", type(exc).__name__)
            try:
                tmp.unlink()
            except OSError:
                pass


class ServerPKCE(SpotifyPKCE):
    """SpotifyPKCE that never starts the browser login from inside a tool call.

    In the server a login flow would block the call until someone finishes it in a browser.
    Only `spotify-mcp --login` sets `interactive`.
    """

    interactive = False

    def get_authorization_code(self, response: str | None = None) -> str:
        if response is None and not self.interactive:
            raise LoginRequired()
        return super().get_authorization_code(response)

    def force_refresh(self) -> None:
        """Refresh the access token now, e.g. after Spotify answered 401 to a valid-looking one."""
        token = self.cache_handler.get_cached_token()
        if not token or not token.get("refresh_token"):
            raise LoginRequired()
        self.refresh_access_token(token["refresh_token"])


def make_auth_manager(*, interactive: bool = False) -> ServerPKCE:
    # Allow override via env var, but default to bundled client ID
    client_id = os.getenv("SPOTIPY_CLIENT_ID", DEFAULT_CLIENT_ID)
    manager = ServerPKCE(
        client_id=client_id,
        redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
        scope=" ".join(SCOPES),
        cache_handler=SafeCacheFileHandler(cache_path=str(TOKEN_CACHE_PATH)),
        open_browser=True,
        # spotipy's PKCE manager has no timeout by default; ResilientSession sets one per attempt.
        requests_timeout=(net.CONNECT_TIMEOUT, net.READ_TIMEOUT),
        requests_session=net.ResilientSession(retry_unsafe=True),
    )
    manager.interactive = interactive
    return manager


def get_spotify_client(auth_manager: ServerPKCE | None = None) -> spotipy.Spotify:
    """Create a Spotify client on the resilient session.

    Uses the cached token and refreshes it when needed. Never opens a browser (see ServerPKCE).
    """
    return spotipy.Spotify(
        auth_manager=auth_manager or make_auth_manager(),
        requests_session=net.ResilientSession(),
        requests_timeout=(net.CONNECT_TIMEOUT, net.READ_TIMEOUT),
    )


def login() -> None:
    """Interactive sign-in for `spotify-mcp --login`: opens the browser, caches the token."""
    manager = make_auth_manager(interactive=True)
    manager.get_access_token()
    print(f"Signed in. Token cached at {TOKEN_CACHE_PATH}.")


def validate_credentials() -> bool:
    """Kept for API compatibility. With PKCE the bundled client ID is always available."""
    return True
