"""OAuth token management for Spotify API using PKCE (no client secret needed)."""

import os
from pathlib import Path

import spotipy
from spotipy.oauth2 import SpotifyPKCE

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


def get_spotify_client() -> spotipy.Spotify:
    """Create an authenticated Spotify client using PKCE flow.

    Uses cached token if available, otherwise initiates OAuth flow.
    Token is automatically refreshed when needed.
    No client secret required - uses PKCE for secure public client auth.
    """
    # Allow override via env var, but default to bundled client ID
    client_id = os.getenv("SPOTIPY_CLIENT_ID", DEFAULT_CLIENT_ID)

    auth_manager = SpotifyPKCE(
        client_id=client_id,
        redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
        scope=" ".join(SCOPES),
        cache_path=str(TOKEN_CACHE_PATH),
        open_browser=True,
    )

    return spotipy.Spotify(auth_manager=auth_manager)


def validate_credentials() -> bool:
    """Check if Spotify credentials are available.

    With PKCE flow, we always have valid credentials (bundled client ID).
    This function now just returns True, kept for API compatibility.
    """
    return True
