"""MCP server for Spotify playback control."""

import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .auth import get_spotify_client
from .spotify_client import SpotifyClient

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


# Tool definitions
TOOLS = [
    Tool(
        name="play",
        description="Resume playback or play a specific track/album/playlist. "
        "Provide a Spotify URI to play specific content, or call without arguments to resume.",
        inputSchema={
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "description": "Spotify URI of track to play (e.g., spotify:track:xxx)",
                },
                "context_uri": {
                    "type": "string",
                    "description": "Spotify URI of album/playlist to play (e.g., spotify:album:xxx)",
                },
                "device_id": {
                    "type": "string",
                    "description": "Target device ID (optional)",
                },
            },
        },
    ),
    Tool(
        name="pause",
        description="Pause Spotify playback.",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Target device ID (optional)",
                },
            },
        },
    ),
    Tool(
        name="next",
        description="Skip to the next track.",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Target device ID (optional)",
                },
            },
        },
    ),
    Tool(
        name="previous",
        description="Go to the previous track.",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Target device ID (optional)",
                },
            },
        },
    ),
    Tool(
        name="seek",
        description="Seek to a position in the current track.",
        inputSchema={
            "type": "object",
            "properties": {
                "position_ms": {
                    "type": "integer",
                    "description": "Position in milliseconds",
                },
                "device_id": {
                    "type": "string",
                    "description": "Target device ID (optional)",
                },
            },
            "required": ["position_ms"],
        },
    ),
    Tool(
        name="set_volume",
        description="Set the playback volume (0-100).",
        inputSchema={
            "type": "object",
            "properties": {
                "volume": {
                    "type": "integer",
                    "description": "Volume level (0-100)",
                    "minimum": 0,
                    "maximum": 100,
                },
                "device_id": {
                    "type": "string",
                    "description": "Target device ID (optional)",
                },
            },
            "required": ["volume"],
        },
    ),
    Tool(
        name="shuffle",
        description="Toggle shuffle mode on or off.",
        inputSchema={
            "type": "object",
            "properties": {
                "state": {
                    "type": "boolean",
                    "description": "True to enable shuffle, false to disable",
                },
                "device_id": {
                    "type": "string",
                    "description": "Target device ID (optional)",
                },
            },
            "required": ["state"],
        },
    ),
    Tool(
        name="repeat",
        description="Set repeat mode.",
        inputSchema={
            "type": "object",
            "properties": {
                "state": {
                    "type": "string",
                    "enum": ["off", "track", "context"],
                    "description": "Repeat mode: 'off', 'track' (repeat one), or 'context' (repeat all)",
                },
                "device_id": {
                    "type": "string",
                    "description": "Target device ID (optional)",
                },
            },
            "required": ["state"],
        },
    ),
    Tool(
        name="get_current_track",
        description="Get information about the currently playing track.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="get_playback_state",
        description="Get the full playback state including device, progress, shuffle, and repeat settings.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="get_queue",
        description="Get the upcoming tracks in the playback queue.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="get_devices",
        description="List available Spotify devices for playback.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="search",
        description="Search Spotify for tracks, albums, artists, or playlists.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query",
                },
                "types": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["track", "album", "artist", "playlist"],
                    },
                    "description": "Types to search for (default: ['track'])",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results per type (default: 10)",
                    "minimum": 1,
                    "maximum": 50,
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="add_to_queue",
        description="Add a track to the playback queue.",
        inputSchema={
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "description": "Spotify URI of the track to add",
                },
                "device_id": {
                    "type": "string",
                    "description": "Target device ID (optional)",
                },
            },
            "required": ["uri"],
        },
    ),
    Tool(
        name="get_playlists",
        description="List the user's playlists.",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max playlists to return (default: 50)",
                    "minimum": 1,
                    "maximum": 50,
                },
            },
        },
    ),
    Tool(
        name="get_playlist_tracks",
        description="Get tracks from a specific playlist.",
        inputSchema={
            "type": "object",
            "properties": {
                "playlist_id": {
                    "type": "string",
                    "description": "Playlist ID or URI",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max tracks to return (default: 100)",
                    "minimum": 1,
                    "maximum": 100,
                },
            },
            "required": ["playlist_id"],
        },
    ),
    Tool(
        name="add_to_playlist",
        description="Add one or more tracks to a playlist.",
        inputSchema={
            "type": "object",
            "properties": {
                "playlist_id": {
                    "type": "string",
                    "description": "Playlist ID or URI",
                },
                "uris": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of Spotify track URIs to add",
                },
            },
            "required": ["playlist_id", "uris"],
        },
    ),
    Tool(
        name="save_tracks",
        description="Save tracks to user's library (like/heart). Use this to add tracks to Liked Songs.",
        inputSchema={
            "type": "object",
            "properties": {
                "track_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of Spotify track URIs or IDs to save",
                },
            },
            "required": ["track_ids"],
        },
    ),
    Tool(
        name="remove_saved_tracks",
        description="Remove tracks from user's library (unlike/unheart).",
        inputSchema={
            "type": "object",
            "properties": {
                "track_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of Spotify track URIs or IDs to remove",
                },
            },
            "required": ["track_ids"],
        },
    ),
    Tool(
        name="get_saved_tracks",
        description="Get user's saved/liked tracks from their library.",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max tracks to return (default: 20)",
                    "minimum": 1,
                    "maximum": 50,
                },
            },
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available Spotify tools."""
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute a Spotify tool."""
    try:
        sp = get_client()

        if name == "play":
            result = sp.play(
                uri=arguments.get("uri"),
                context_uri=arguments.get("context_uri"),
                device_id=arguments.get("device_id"),
            )
        elif name == "pause":
            result = sp.pause(device_id=arguments.get("device_id"))
        elif name == "next":
            result = sp.next_track(device_id=arguments.get("device_id"))
        elif name == "previous":
            result = sp.previous_track(device_id=arguments.get("device_id"))
        elif name == "seek":
            result = sp.seek(
                position_ms=arguments["position_ms"],
                device_id=arguments.get("device_id"),
            )
        elif name == "set_volume":
            result = sp.set_volume(
                volume=arguments["volume"],
                device_id=arguments.get("device_id"),
            )
        elif name == "shuffle":
            result = sp.shuffle(
                state=arguments["state"],
                device_id=arguments.get("device_id"),
            )
        elif name == "repeat":
            result = sp.repeat(
                state=arguments["state"],
                device_id=arguments.get("device_id"),
            )
        elif name == "get_current_track":
            result = sp.get_current_track()
        elif name == "get_playback_state":
            result = sp.get_playback_state()
        elif name == "get_queue":
            result = sp.get_queue()
        elif name == "get_devices":
            result = sp.get_devices()
        elif name == "search":
            result = sp.search(
                query=arguments["query"],
                types=arguments.get("types"),
                limit=arguments.get("limit", 10),
            )
        elif name == "add_to_queue":
            result = sp.add_to_queue(
                uri=arguments["uri"],
                device_id=arguments.get("device_id"),
            )
        elif name == "get_playlists":
            result = sp.get_playlists(limit=arguments.get("limit", 50))
        elif name == "get_playlist_tracks":
            result = sp.get_playlist_tracks(
                playlist_id=arguments["playlist_id"],
                limit=arguments.get("limit", 100),
            )
        elif name == "add_to_playlist":
            result = sp.add_to_playlist(
                playlist_id=arguments["playlist_id"],
                uris=arguments["uris"],
            )
        elif name == "save_tracks":
            result = sp.save_tracks(track_ids=arguments["track_ids"])
        elif name == "remove_saved_tracks":
            result = sp.remove_saved_tracks(track_ids=arguments["track_ids"])
        elif name == "get_saved_tracks":
            result = sp.get_saved_tracks(limit=arguments.get("limit", 20))
        else:
            result = {"error": f"Unknown tool: {name}"}

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    except Exception as e:
        error_result = {"error": str(e)}
        return [TextContent(type="text", text=json.dumps(error_result, indent=2))]


async def run_server():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    """Entry point."""
    import asyncio

    asyncio.run(run_server())


if __name__ == "__main__":
    main()
