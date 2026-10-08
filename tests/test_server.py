"""run_tool and the MCP handler: result shape, isError, argument coercion."""

import asyncio
import json

import pytest
import spotipy

from spotify_mcp import net, server
from spotify_mcp.spotify_client import SpotifyClient
from tests.test_client import NO_DEVICE, PLAYING, TRACK, FakeAuth
from tests.test_net import stale

ALLOWED_KEYS = {"error", "status", "details", "code", "message"}


@pytest.fixture
def use(monkeypatch, fake_adapter):
    def install(script, **kwargs):
        session = net.ResilientSession()
        session.sleep = lambda s: None
        adapter = fake_adapter(script)
        session.mount("https://", adapter)
        client = SpotifyClient(spotipy.Spotify(auth_manager=FakeAuth(), requests_session=session), **kwargs)
        monkeypatch.setattr(server, "client", client)
        return adapter

    return install


def test_success_is_compact_json_and_not_an_error(use):
    use([PLAYING])
    result = asyncio.run(server.call_tool("get_current_track", {}))
    assert result.isError is False
    text = result.content[0].text
    assert "\n" not in text and ": " not in text
    assert json.loads(text)["track"]["artists"] == ["New Order"]


def test_error_result_shape_and_flag(use):
    use([NO_DEVICE])
    result = asyncio.run(server.call_tool("play", {"uri": TRACK}))
    assert result.isError is True
    data = json.loads(result.content[0].text)
    assert set(data) <= ALLOWED_KEYS
    assert data["code"] == "no_active_device"
    assert "No active device" in data["details"]


def test_stale_connection_is_invisible_to_the_client(use):
    use([stale(), PLAYING])
    data, is_error = server.run_tool("get_current_track", {})
    assert not is_error and data["playing"] is True


def test_network_failure_reads_cleanly(use, monkeypatch):
    monkeypatch.setattr(net, "IDLE_RESET", 3600.0)
    use([stale()])
    data, is_error = server.run_tool("next", {})
    assert is_error
    assert data == {"error": "Could not reach Spotify. Check the internet connection and try again.",
                    "code": "network"}


def test_missing_argument(use):
    use([])
    data, is_error = server.run_tool("seek", {})
    assert is_error and data == {"error": "Missing argument: position_ms.", "code": "bad_request"}


def test_near_miss_arguments_are_accepted(use):
    adapter = use([(204, None), (204, None), (204, None)])
    assert server.run_tool("set_volume", {"volume": "50%"})[0]["message"] == "Volume set to 50%"
    assert server.run_tool("shuffle", {"state": "on"})[0]["message"] == "Shuffle on"
    assert server.run_tool("repeat", {"state": "all"})[0]["message"] == "Repeat mode set to context"
    assert "volume_percent=50" in adapter.sent[0].url


def test_volume_is_clamped(use):
    adapter = use([(204, None)])
    server.run_tool("set_volume", {"volume": 150})
    assert "volume_percent=100" in adapter.sent[0].url


def test_play_context_uri_track_end_to_end(use):
    adapter = use([(204, None)])
    data, is_error = server.run_tool("play", {"context_uri": f"https://open.spotify.com/track/{TRACK.split(':')[2]}"})
    assert not is_error
    assert json.loads(adapter.sent[0].body) == {"uris": [TRACK]}


def test_unknown_tool(use):
    use([])
    data, is_error = server.run_tool("dance", {})
    assert is_error and data["code"] == "bad_request"


# Every tool and argument main had, as (arguments, required), less the retired local favorites.
# Clients call these by name, and Strawberry lists its careful tools by name: none may disappear,
# be renamed, or become required.
MAIN_TOOLS = {
    "play": (["context_uri", "device_id", "uri"], []),
    "pause": (["device_id"], []),
    "next": (["device_id"], []),
    "previous": (["device_id"], []),
    "seek": (["device_id", "position_ms"], ["position_ms"]),
    "set_volume": (["device_id", "volume"], ["volume"]),
    "shuffle": (["device_id", "state"], ["state"]),
    "repeat": (["device_id", "state"], ["state"]),
    "get_current_track": ([], []),
    "get_playback_state": ([], []),
    "get_queue": ([], []),
    "get_devices": ([], []),
    "search": (["limit", "query", "types"], ["query"]),
    "add_to_queue": (["device_id", "uri"], ["uri"]),
    "get_playlists": (["limit"], []),
    "get_playlist_tracks": (["limit", "playlist_id"], ["playlist_id"]),
    "add_to_playlist": (["playlist_id", "uris"], ["playlist_id", "uris"]),
    "save_tracks": (["track_ids"], ["track_ids"]),
    "remove_saved_tracks": (["track_ids"], ["track_ids"]),
    "get_saved_tracks": (["limit"], []),
}
NEW_TOOLS = ["like_current", "add_current_to_playlist", "find_playlist", "remove_from_playlist", "create_playlist",
             "play_liked"]
# The local favorites file is gone: Liked Songs (like_current, play_liked) and playlists replace it.
RETIRED_TOOLS = ["favorite_current", "get_favorites", "remove_favorite", "play_favorites", "clear_favorites"]


def test_existing_tools_keep_their_names_and_arguments():
    tools = {tool.name: tool.inputSchema for tool in asyncio.run(server.list_tools())}
    for name, (arguments, required) in MAIN_TOOLS.items():
        assert name in tools, name
        assert set(arguments) <= set(tools[name]["properties"]), name
        assert sorted(tools[name].get("required", [])) == required, name


def test_tool_list_is_main_plus_the_new_tools():
    names = [tool.name for tool in asyncio.run(server.list_tools())]
    assert sorted(names) == sorted(list(MAIN_TOOLS) + NEW_TOOLS)
    assert len(set(names)) == len(names)
    assert set(server.HANDLERS) == set(names)
    assert len(names) == 26


def test_retired_favorites_tools_are_unknown(use):
    use([])
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    for name in RETIRED_TOOLS:
        assert name not in names
        data, is_error = server.run_tool(name, {})
        assert is_error and data == {"error": f"Unknown tool: {name}.", "code": "bad_request"}


def test_write_tools_say_they_change_the_library():
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    for name in ["like_current", "add_current_to_playlist", "remove_from_playlist", "create_playlist"]:
        assert "changes the user's library" in tools[name].description.lower(), name
        assert tools[name].annotations.readOnlyHint is False
    assert tools["remove_from_playlist"].annotations.destructiveHint is True
    assert tools["find_playlist"].annotations.readOnlyHint is True


def test_absurd_number_is_a_bad_request(use):
    use([])
    data, is_error = server.run_tool("seek", {"position_ms": "inf"})
    assert is_error and data["code"] == "bad_request"
