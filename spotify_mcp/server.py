"""MCP server for Spotify playback control."""

from __future__ import annotations

import json
import logging
import random
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool

from . import favorites, net, uris
from .auth import get_spotify_client
from .errors import BAD_REQUEST, NOT_FOUND, ToolError, from_exception
from .spotify_client import SpotifyClient

log = logging.getLogger("spotify_mcp.server")

# Load environment variables from .env file
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

# Also try home directory
load_dotenv(Path.home() / ".spotify-mcp.env")

server = Server("spotify-mcp")
client: SpotifyClient | None = None


def get_client() -> SpotifyClient:
    """Get or create the Spotify client."""
    global client
    if client is None:
        client = SpotifyClient(get_spotify_client())
    return client


# Tool definitions. Names and argument names are a public interface: clients call them by name.
# Descriptions are short on purpose; small models read all of them on every turn.

_DEVICE = {"type": "string", "description": "Optional device ID from get_devices."}
_NO_ARGS: dict[str, Any] = {"type": "object", "properties": {}}


def _tool(name: str, description: str, properties: dict[str, Any] | None = None,
          required: list[str] | None = None) -> Tool:
    schema: dict[str, Any] = {"type": "object", "properties": properties or {}}
    if required:
        schema["required"] = required
    return Tool(name=name, description=description, inputSchema=schema)


TOOLS = [
    _tool(
        "play",
        "Play or resume music. No arguments: resume. A track: give uri. An album, playlist or artist: "
        "give context_uri. Use URIs from search.",
        {
            "uri": {"type": "string", "description": "Track URI, e.g. spotify:track:<id>."},
            "context_uri": {"type": "string", "description": "Album, playlist or artist URI."},
            "device_id": _DEVICE,
        },
    ),
    _tool("pause", "Pause playback.", {"device_id": _DEVICE}),
    _tool("next", "Skip to the next track.", {"device_id": _DEVICE}),
    _tool("previous", "Go back to the previous track.", {"device_id": _DEVICE}),
    _tool(
        "seek",
        "Jump to a position in the current track. Needs position_ms.",
        {"position_ms": {"type": "integer", "description": "Milliseconds; 60000 = 1 minute."},
         "device_id": _DEVICE},
        ["position_ms"],
    ),
    _tool(
        "set_volume",
        "Set the volume. Needs volume, 0 to 100.",
        {"volume": {"type": "integer", "description": "Percent, 0-100.", "minimum": 0, "maximum": 100},
         "device_id": _DEVICE},
        ["volume"],
    ),
    _tool(
        "shuffle",
        "Turn shuffle on or off. Needs state.",
        {"state": {"type": "boolean", "description": "true = on, false = off."}, "device_id": _DEVICE},
        ["state"],
    ),
    _tool(
        "repeat",
        "Set repeat mode. Needs state.",
        {"state": {"type": "string", "enum": ["off", "track", "context"],
                   "description": "track = repeat one song, context = repeat the album or playlist."},
         "device_id": _DEVICE},
        ["state"],
    ),
    _tool("get_current_track", "The track playing now."),
    _tool("get_playback_state", "Player state: device, volume, shuffle, repeat, track."),
    _tool("get_queue", "The next tracks in the queue."),
    _tool("get_devices", "List Spotify devices: id, name, active, volume."),
    _tool(
        "search",
        "Search Spotify. Needs query. Returns URIs for play and add_to_queue.",
        {
            "query": {"type": "string", "description": "Search words."},
            "types": {"type": "array", "items": {"type": "string", "enum": ["track", "album", "artist", "playlist"]},
                      "description": "Default ['track']."},
            "limit": {"type": "integer", "description": "Per type, 1-10. Default 10.",
                      "minimum": 1, "maximum": 10},
        },
        ["query"],
    ),
    _tool(
        "add_to_queue",
        "Add a track to the queue. Needs uri.",
        {"uri": {"type": "string", "description": "Track URI."}, "device_id": _DEVICE},
        ["uri"],
    ),
    _tool(
        "get_playlists",
        "List the user's playlists.",
        {"limit": {"type": "integer", "description": "Default 50.", "minimum": 1, "maximum": 50}},
    ),
    _tool(
        "get_playlist_tracks",
        "List a playlist's tracks. Needs playlist_id.",
        {"playlist_id": {"type": "string", "description": "Playlist ID, URI or link."},
         "limit": {"type": "integer", "description": "Default 100.", "minimum": 1, "maximum": 100}},
        ["playlist_id"],
    ),
    _tool(
        "add_to_playlist",
        "Add tracks to a playlist. Needs playlist_id and uris.",
        {"playlist_id": {"type": "string", "description": "Playlist ID, URI or link."},
         "uris": {"type": "array", "items": {"type": "string"}, "description": "Track URIs."}},
        ["playlist_id", "uris"],
    ),
    _tool(
        "save_tracks",
        "Add tracks to Liked Songs. Needs track_ids.",
        {"track_ids": {"type": "array", "items": {"type": "string"}, "description": "Track URIs or IDs."}},
        ["track_ids"],
    ),
    _tool(
        "remove_saved_tracks",
        "Remove tracks from Liked Songs. Needs track_ids.",
        {"track_ids": {"type": "array", "items": {"type": "string"}, "description": "Track URIs or IDs."}},
        ["track_ids"],
    ),
    _tool(
        "get_saved_tracks",
        "List Liked Songs, newest first.",
        {"limit": {"type": "integer", "description": "Default 20.", "minimum": 1, "maximum": 50}},
    ),
    # Local favorites (no Spotify API permissions needed)
    _tool("favorite_current", "Add the playing track to local favorites (not Liked Songs)."),
    _tool("get_favorites", "List the local favorites."),
    _tool(
        "remove_favorite",
        "Remove a track from the local favorites. Needs uri.",
        {"uri": {"type": "string", "description": "Track URI."}},
        ["uri"],
    ),
    _tool(
        "play_favorites",
        "Play one random local favorite, or with shuffle=true queue all of them shuffled.",
        {"shuffle": {"type": "boolean", "description": "Default false."}},
    ),
    _tool("clear_favorites", "Delete all local favorites."),
]


# --- arguments ---------------------------------------------------------------------------
# Input is checked here, not by the MCP SDK's schema validation, so a wrong or missing argument
# comes back in the same error shape as everything else, and near-misses from small models
# ("50", "on", a single string for a list) are accepted.


def _missing(name: str) -> ToolError:
    return ToolError(BAD_REQUEST, f"Missing argument: {name}.")


def _str(args: dict[str, Any], name: str, required: bool = False) -> str | None:
    value = args.get(name)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise _missing(name)
        return None
    if not isinstance(value, (str, int, float)):
        raise ToolError(BAD_REQUEST, f"{name} must be a string.")
    return str(value).strip()


def _int(args: dict[str, Any], name: str, default: int | None = None, required: bool = False) -> int | None:
    value = args.get(name)
    if value is None or value == "":
        if required:
            raise _missing(name)
        return default
    if isinstance(value, bool):
        raise ToolError(BAD_REQUEST, f"{name} must be a number.")
    try:
        return int(round(float(str(value).strip().rstrip("%"))))
    except (ValueError, OverflowError):
        raise ToolError(BAD_REQUEST, f"{name} must be a number.") from None


_TRUE = {"true", "on", "yes", "1", "enable", "enabled"}
_FALSE = {"false", "off", "no", "0", "disable", "disabled"}


def _bool(args: dict[str, Any], name: str, default: bool | None = None, required: bool = False) -> bool | None:
    value = args.get(name)
    if value is None or value == "":
        if required:
            raise _missing(name)
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ToolError(BAD_REQUEST, f"{name} must be true or false.")


def _list(args: dict[str, Any], name: str, required: bool = False) -> list[str] | None:
    value = args.get(name)
    if isinstance(value, str):
        value = [part for part in value.replace(",", " ").split() if part]
    if not value:
        if required:
            raise _missing(name)
        return None
    if not isinstance(value, list):
        raise ToolError(BAD_REQUEST, f"{name} must be a list of strings.")
    return [str(item).strip() for item in value if str(item).strip()]


_REPEAT = {"off": "off", "false": "off", "none": "off", "track": "track", "one": "track", "song": "track",
           "context": "context", "all": "context", "on": "context", "true": "context", "playlist": "context",
           "album": "context"}


def _repeat_state(args: dict[str, Any]) -> str:
    raw = args.get("state")
    if raw is None or raw == "":
        raise _missing("state")
    state = _REPEAT.get(str(raw).strip().lower())
    if state is None:
        raise ToolError(BAD_REQUEST, "state must be off, track or context.")
    return state


# --- dispatch ----------------------------------------------------------------------------


def _favorite_current(sp: SpotifyClient, _args: dict[str, Any]) -> dict[str, Any]:
    current = sp.get_current_track()
    if not current.get("track"):
        raise ToolError(NOT_FOUND, "Nothing is playing right now.")
    return favorites.add_favorite(current["track"])


def _play_favorites(sp: SpotifyClient, args: dict[str, Any]) -> dict[str, Any]:
    tracks = favorites.get_favorites().get("favorites") or []
    if not tracks:
        raise ToolError(NOT_FOUND, "No favorites saved yet.")
    if not _bool(args, "shuffle", default=False):
        track = random.choice(tracks)
        sp.play(uri=track["uri"])
        return {"success": True, "message": f"Playing '{track['name']}'"}
    random.shuffle(tracks)
    queued = 0
    for track in tracks:
        try:
            sp.add_to_queue(track["uri"])
        except Exception:
            if queued == 0:
                raise
            return {"success": True, "message": f"Queued {queued} of {len(tracks)} favorites; then Spotify stopped "
                    "answering"}
        queued += 1
    return {"success": True, "message": f"Queued {queued} favorites"}


HANDLERS: dict[str, Callable[[SpotifyClient, dict[str, Any]], dict[str, Any]]] = {
    "play": lambda sp, a: sp.play(uri=_str(a, "uri"), context_uri=_str(a, "context_uri"),
                                  device_id=_str(a, "device_id")),
    "pause": lambda sp, a: sp.pause(device_id=_str(a, "device_id")),
    "next": lambda sp, a: sp.next_track(device_id=_str(a, "device_id")),
    "previous": lambda sp, a: sp.previous_track(device_id=_str(a, "device_id")),
    "seek": lambda sp, a: sp.seek(position_ms=_int(a, "position_ms", required=True), device_id=_str(a, "device_id")),
    "set_volume": lambda sp, a: sp.set_volume(volume=_int(a, "volume", required=True),
                                              device_id=_str(a, "device_id")),
    "shuffle": lambda sp, a: sp.shuffle(state=_bool(a, "state", required=True), device_id=_str(a, "device_id")),
    "repeat": lambda sp, a: sp.repeat(state=_repeat_state(a), device_id=_str(a, "device_id")),
    "get_current_track": lambda sp, a: sp.get_current_track(),
    "get_playback_state": lambda sp, a: sp.get_playback_state(),
    "get_queue": lambda sp, a: sp.get_queue(),
    "get_devices": lambda sp, a: sp.get_devices(),
    "search": lambda sp, a: sp.search(query=_str(a, "query", required=True), types=_list(a, "types"),
                                      limit=_int(a, "limit")),
    "add_to_queue": lambda sp, a: sp.add_to_queue(uri=_str(a, "uri", required=True), device_id=_str(a, "device_id")),
    "get_playlists": lambda sp, a: sp.get_playlists(limit=_int(a, "limit", 100)),
    "get_playlist_tracks": lambda sp, a: sp.get_playlist_tracks(playlist_id=_str(a, "playlist_id", required=True),
                                                                limit=_int(a, "limit", 100)),
    "add_to_playlist": lambda sp, a: sp.add_to_playlist(_str(a, "playlist_id", required=True),
                                                        _list(a, "uris", required=True)),
    "save_tracks": lambda sp, a: sp.save_tracks(track_ids=_list(a, "track_ids", required=True)),
    "remove_saved_tracks": lambda sp, a: sp.remove_saved_tracks(track_ids=_list(a, "track_ids", required=True)),
    "get_saved_tracks": lambda sp, a: sp.get_saved_tracks(limit=_int(a, "limit", 20)),
    "favorite_current": _favorite_current,
    "play_favorites": _play_favorites,
}

def _favorite_uri(args: dict[str, Any]) -> str:
    raw = _str(args, "uri", required=True)
    parsed = uris.parse(raw)
    return parsed[1] if parsed else raw


# Local-only tools: they never touch the Spotify API, so they work even when signing in does not.
LOCAL_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "get_favorites": lambda a: favorites.get_favorites(),
    "remove_favorite": lambda a: favorites.remove_favorite(_favorite_uri(a)),
    "clear_favorites": lambda a: favorites.clear_favorites(),
}


def _dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def run_tool(name: str, arguments: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
    """(result, is_error). Never raises; every failure becomes the error shape in errors.py."""
    arguments = arguments if isinstance(arguments, dict) else {}
    try:
        with net.call_budget():
            if name in LOCAL_HANDLERS:
                return LOCAL_HANDLERS[name](arguments), False
            handler = HANDLERS.get(name)
            if handler is None:
                raise ToolError(BAD_REQUEST, f"Unknown tool: {name}.")
            return handler(get_client(), arguments), False
    except Exception as exc:  # noqa: BLE001 - this is the one place errors are shaped
        error = from_exception(exc)
        log.warning("%s failed: %s (%s)", name, error.code, error.status or "-")
        return error.as_dict(), True


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available Spotify tools."""
    return TOOLS


@server.call_tool(validate_input=False)
async def call_tool(name: str, arguments: dict) -> CallToolResult:
    """Execute a Spotify tool."""
    result, is_error = run_tool(name, arguments)
    return CallToolResult(content=[TextContent(type="text", text=_dumps(result))], isError=is_error)


async def run_server():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    """Entry point. `spotify-mcp` serves MCP over stdio; `spotify-mcp --login` signs in."""
    import asyncio

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr,
                        format="spotify-mcp %(levelname)s %(name)s: %(message)s")
    # spotipy logs every HTTP error with its URL at ERROR; the server logs one line per failed call.
    logging.getLogger("spotipy").setLevel(logging.CRITICAL)

    if "--login" in sys.argv[1:]:
        from .auth import login

        login()
        return
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
