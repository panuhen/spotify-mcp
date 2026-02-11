"""OAuth token management for Spotify API."""

import os
from pathlib import Path

import spotipy
from spotipy.oauth2 import SpotifyOAuth

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
    """Create an authenticated Spotify client.

    Uses cached token if available, otherwise initiates OAuth flow.
    Token is automatically refreshed when needed.
    """
    auth_manager = SpotifyOAuth(
        client_id=os.getenv("SPOTIPY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
        redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
        scope=" ".join(SCOPES),
        cache_path=str(TOKEN_CACHE_PATH),
        open_browser=True,
    )

    return spotipy.Spotify(auth_manager=auth_manager)


def validate_credentials() -> bool:
    """Check if Spotify credentials are configured."""
    client_id = os.getenv("SPOTIPY_CLIENT_ID")
    client_secret = os.getenv("SPOTIPY_CLIENT_SECRET")

    if not client_id or not client_secret:
        return False

    if client_id == "your_client_id_here" or client_secret == "your_client_secret_here":
        return False

    return True
