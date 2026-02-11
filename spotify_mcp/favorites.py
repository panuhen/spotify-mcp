"""Local favorites management for Spotify MCP."""

import json
import random
from pathlib import Path
from typing import Any

FAVORITES_PATH = Path.home() / ".spotify-mcp-favorites.json"


def _load_favorites() -> list[dict[str, Any]]:
    """Load favorites from disk."""
    if not FAVORITES_PATH.exists():
        return []
    try:
        return json.loads(FAVORITES_PATH.read_text())
    except (json.JSONDecodeError, IOError):
        return []


def _save_favorites(favorites: list[dict[str, Any]]) -> None:
    """Save favorites to disk."""
    FAVORITES_PATH.write_text(json.dumps(favorites, indent=2))


def add_favorite(track: dict[str, Any]) -> dict[str, Any]:
    """Add a track to local favorites.

    Args:
        track: Track info with name, uri, artists, album
    """
    favorites = _load_favorites()

    # Check if already exists
    if any(f["uri"] == track["uri"] for f in favorites):
        return {"success": False, "message": "Track already in favorites"}

    favorites.append({
        "name": track["name"],
        "uri": track["uri"],
        "artists": track["artists"],
        "album": track.get("album", ""),
    })
    _save_favorites(favorites)

    return {"success": True, "message": f"Added '{track['name']}' to favorites"}


def remove_favorite(uri: str) -> dict[str, Any]:
    """Remove a track from favorites by URI."""
    favorites = _load_favorites()
    original_len = len(favorites)

    favorites = [f for f in favorites if f["uri"] != uri]

    if len(favorites) == original_len:
        return {"success": False, "message": "Track not found in favorites"}

    _save_favorites(favorites)
    return {"success": True, "message": "Removed from favorites"}


def get_favorites() -> dict[str, Any]:
    """Get all favorites."""
    favorites = _load_favorites()
    return {"favorites": favorites, "total": len(favorites)}


def get_random_favorite() -> dict[str, Any]:
    """Get a random favorite track."""
    favorites = _load_favorites()
    if not favorites:
        return {"error": "No favorites saved yet"}
    return {"track": random.choice(favorites)}


def clear_favorites() -> dict[str, Any]:
    """Clear all favorites."""
    _save_favorites([])
    return {"success": True, "message": "Cleared all favorites"}
