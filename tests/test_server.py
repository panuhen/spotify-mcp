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


def test_favorite_current_with_nothing_playing(use):
    use([(204, None)])
    data, is_error = server.run_tool("favorite_current", {})
    assert is_error and data == {"error": "Nothing is playing right now.", "code": "not_found"}


def test_local_tools_work_without_spotify(monkeypatch):
    monkeypatch.setattr(server, "get_client", lambda: pytest.fail("local tools must not sign in"))
    data, is_error = server.run_tool("get_favorites", {})
    assert not is_error and data == {"favorites": [], "total": 0}


def test_tool_list_unchanged():
    names = [tool.name for tool in asyncio.run(server.list_tools())]
    assert names == ["play", "pause", "next", "previous", "seek", "set_volume", "shuffle", "repeat",
                     "get_current_track", "get_playback_state", "get_queue", "get_devices", "search",
                     "add_to_queue", "get_playlists", "get_playlist_tracks", "add_to_playlist", "save_tracks",
                     "remove_saved_tracks", "get_saved_tracks", "favorite_current", "get_favorites",
                     "remove_favorite", "play_favorites", "clear_favorites"]


def test_absurd_number_is_a_bad_request(use):
    use([])
    data, is_error = server.run_tool("seek", {"position_ms": "inf"})
    assert is_error and data["code"] == "bad_request"
