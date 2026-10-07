"""SpotifyClient over real spotipy, with Spotify's HTTP answers scripted."""

import json

import pytest
import spotipy
from spotipy.exceptions import SpotifyException

from spotify_mcp import net
from spotify_mcp.errors import ToolError
from spotify_mcp.spotify_client import SpotifyClient
from tests.test_net import stale

TRACK = "spotify:track:4uLU6hMCjMI75M1A2tKUQC"
ALBUM = "spotify:album:1DFixLWuPkv3KT3TnV35m3"
NO_DEVICE = (404, {"error": {"status": 404, "message": "Player command failed: No active device found",
                             "reason": "NO_ACTIVE_DEVICE"}})
PLAYING = (200, {"is_playing": True, "progress_ms": 1000, "item": {
    "name": "Blue Monday", "uri": TRACK, "duration_ms": 448000,
    "artists": [{"name": "New Order"}], "album": {"name": "Power, Corruption & Lies"}}})


class FakeAuth:
    """Stands in for ServerPKCE: hands out a token, counts forced refreshes."""

    def __init__(self):
        self.refreshes = 0

    def get_access_token(self, *args, **kwargs):
        return f"test-token-{self.refreshes}"

    def force_refresh(self):
        self.refreshes += 1


@pytest.fixture
def make(fake_adapter):
    def build(script, auto_device="off", compact=False):
        session = net.ResilientSession()
        session.sleep = lambda s: None
        adapter = fake_adapter(script)
        session.mount("https://", adapter)
        auth = FakeAuth()
        sp = spotipy.Spotify(auth_manager=auth, requests_session=session)
        return SpotifyClient(sp, auto_device=auto_device, compact=compact), adapter, auth

    return build


def body(request):
    return json.loads(request.body) if request.body else None


def test_track_given_as_context_uri_is_played_as_uris(make):
    client, adapter, _ = make([(204, None)])
    assert client.play(context_uri=TRACK)["success"]
    assert body(adapter.sent[0]) == {"uris": [TRACK]}


def test_links_become_uris(make):
    client, adapter, _ = make([(204, None), (204, None)])
    client.play(context_uri="https://open.spotify.com/album/1DFixLWuPkv3KT3TnV35m3?si=xyz")
    assert body(adapter.sent[0]) == {"context_uri": ALBUM}
    client.play(uri="https://open.spotify.com/intl-fi/track/4uLU6hMCjMI75M1A2tKUQC")
    assert body(adapter.sent[1]) == {"uris": [TRACK]}


def test_album_given_as_uri_is_played_as_context(make):
    client, adapter, _ = make([(204, None)])
    client.play(uri=ALBUM)
    assert body(adapter.sent[0]) == {"context_uri": ALBUM}


def test_track_and_album_play_the_album_from_that_track(make):
    client, adapter, _ = make([(204, None)])
    client.play(uri=TRACK, context_uri=ALBUM)
    assert body(adapter.sent[0]) == {"context_uri": ALBUM, "offset": {"uri": TRACK}}


def test_resume_sends_no_body(make):
    client, adapter, _ = make([(204, None)])
    assert client.play() == {"success": True, "message": "Playback resumed"}
    assert adapter.sent[0].method == "PUT" and adapter.sent[0].body is None


def test_bad_uri_is_rejected_before_calling_spotify(make):
    client, adapter, _ = make([])
    with pytest.raises(ToolError) as caught:
        client.play(context_uri="blue monday")
    assert caught.value.code == "bad_request"
    assert adapter.sent == []


def test_400_context_maps_to_bad_request(make):
    from spotify_mcp.errors import from_exception

    client, _, _ = make([(400, {"error": {"status": 400, "message": "Non supported context uri"}})])
    with pytest.raises(SpotifyException) as caught:
        client.play(context_uri="spotify:show:4rOoJ6Egrf8K2IrywzwOMk")
    error = from_exception(caught.value)
    assert error.code == "bad_request" and "Non supported context uri" in error.message


def test_no_active_device_is_reported_when_auto_is_off(make):
    client, adapter, _ = make([NO_DEVICE])
    with pytest.raises(SpotifyException):
        client.play(uri=TRACK)
    assert len(adapter.sent) == 1


def test_auto_device_moves_playback_to_the_only_device(make):
    devices = (200, {"devices": [{"id": "dev1", "name": "Desktop", "is_active": False, "is_restricted": False}]})
    client, adapter, _ = make([NO_DEVICE, devices, (204, None)], auto_device="auto")
    result = client.play(uri=TRACK)
    assert result["device"] == "Desktop" and result["success"]
    assert "device_id=dev1" in adapter.sent[2].url
    assert body(adapter.sent[2]) == {"uris": [TRACK]}


def test_auto_device_resume_transfers_playback(make):
    devices = (200, {"devices": [{"id": "dev1", "name": "Desktop", "is_active": False}]})
    client, adapter, _ = make([NO_DEVICE, devices, (204, None)], auto_device="auto")
    client.play()
    assert adapter.sent[2].url.endswith("/me/player")
    assert body(adapter.sent[2]) == {"device_ids": ["dev1"], "play": True}


def test_auto_device_does_not_guess_between_several(make):
    devices = (200, {"devices": [{"id": "a", "name": "Desktop"}, {"id": "b", "name": "Kitchen"}]})
    client, _, _ = make([NO_DEVICE, devices], auto_device="auto")
    with pytest.raises(ToolError) as caught:
        client.play(uri=TRACK)
    assert caught.value.code == "no_active_device"
    assert "Desktop, Kitchen" in caught.value.message


def test_auto_device_uses_the_last_active_device(make):
    seen = (200, {"devices": [{"id": "b", "name": "Kitchen", "is_active": True}, {"id": "a", "name": "Desktop"}]})
    later = (200, {"devices": [{"id": "a", "name": "Desktop"}, {"id": "b", "name": "Kitchen"}]})
    client, adapter, _ = make([seen, NO_DEVICE, later, (204, None)], auto_device="auto")
    client.get_devices()
    assert client.play(uri=TRACK)["device"] == "Kitchen"
    assert "device_id=b" in adapter.sent[3].url


def test_401_refreshes_the_token_once_and_retries(make):
    client, adapter, auth = make([(401, {"error": {"status": 401, "message": "The access token expired"}}), PLAYING])
    assert client.get_current_track()["track"]["name"] == "Blue Monday"
    assert auth.refreshes == 1
    assert adapter.sent[0].headers["Authorization"] != adapter.sent[1].headers["Authorization"]


def test_second_401_is_raised(make):
    expired = (401, {"error": {"status": 401, "message": "The access token expired"}})
    client, _, auth = make([expired, expired])
    with pytest.raises(SpotifyException):
        client.get_current_track()
    assert auth.refreshes == 1


def test_stale_connection_get_current_track_succeeds(make):
    client, adapter, _ = make([stale(), PLAYING])
    assert client.get_current_track()["playing"] is True
    assert len(adapter.sent) == 2


def test_stale_connection_next_is_not_repeated(make, monkeypatch):
    monkeypatch.setattr(net, "IDLE_RESET", 3600.0)
    client, adapter, _ = make([stale()])
    with pytest.raises(Exception):
        client.next_track()
    assert len(adapter.sent) == 1


def test_429_then_success(make):
    client, adapter, _ = make([(429, None, {"Retry-After": "1"}), PLAYING])
    assert client.get_current_track()["playing"] is True


def test_add_to_queue_accepts_links_and_rejects_albums(make):
    client, adapter, _ = make([(204, None)])
    client.add_to_queue("https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC?si=1")
    assert adapter.sent[0].url.endswith(f"/me/player/queue?uri={TRACK}")
    with pytest.raises(ToolError):
        client.add_to_queue(ALBUM)


def test_compact_search_trims(make):
    results = (200, {"tracks": {"items": [{"name": "x", "uri": TRACK, "artists": [{"name": "y"}],
                                           "album": {"name": "z"}}]},
                     "artists": {"items": [{"name": "y", "uri": "spotify:artist:1", "genres": ["synth-pop"]}]}})
    client, adapter, _ = make([results], compact=True)
    out = client.search("x", types=["track", "artist"])
    assert "limit=5" in adapter.sent[0].url
    assert out["artists"] == [{"name": "y", "uri": "spotify:artist:1"}]


def test_playlist_track_count_reads_items_or_tracks(make):
    page = (200, {"items": [{"name": "a", "uri": "spotify:playlist:1", "id": "1", "owner": {"display_name": "me"},
                             "tracks": {"total": 3}},
                            {"name": "b", "uri": "spotify:playlist:2", "id": "2", "owner": {"display_name": "me"},
                             "items": {"total": 4}}, None]})
    client, _, _ = make([page])
    assert [p["tracks"] for p in client.get_playlists()["playlists"]] == [3, 4]


PLAYLIST = "37i9dQZF1DXcBWIGoYBM5M"


def test_playlist_tracks_use_the_items_endpoint(make):
    page = (200, {"total": 1, "items": [{"added_at": "2026-01-01T00:00:00Z", "item": {
        "name": "Blue Monday", "uri": TRACK, "artists": [{"name": "New Order"}], "album": {"name": "Substance"}}}]})
    client, adapter, _ = make([page])
    out = client.get_playlist_tracks(f"https://open.spotify.com/playlist/{PLAYLIST}?si=1", limit=3)
    assert f"/playlists/{PLAYLIST}/items?" in adapter.sent[0].url
    assert out == {"tracks": [{"name": "Blue Monday", "uri": TRACK, "artists": ["New Order"], "album": "Substance",
                               "added_at": "2026-01-01T00:00:00Z"}], "total": 1}


def test_playlist_tracks_fall_back_when_items_is_missing(make):
    missing = (404, {"error": {"status": 404, "message": "Service not found"}})
    page = (200, {"total": 0, "items": []})
    client, adapter, _ = make([missing, page])
    client.get_playlist_tracks(PLAYLIST)
    assert f"/playlists/{PLAYLIST}/tracks?" in adapter.sent[1].url


def test_add_to_playlist_posts_to_items(make):
    client, adapter, _ = make([(201, {"snapshot_id": "x"})])
    client.add_to_playlist(PLAYLIST, [TRACK, "https://open.spotify.com/track/6hHc7Pks7wtBIW8Z6A0iFq"])
    assert adapter.sent[0].method == "POST" and adapter.sent[0].url.endswith(f"/playlists/{PLAYLIST}/items")
    assert json.loads(adapter.sent[0].body) == {"uris": [TRACK, "spotify:track:6hHc7Pks7wtBIW8Z6A0iFq"]}


def test_save_and_remove_use_the_library_endpoint(make):
    client, adapter, _ = make([(200, None), (200, None)])
    client.save_tracks([TRACK])
    client.remove_saved_tracks(["6hHc7Pks7wtBIW8Z6A0iFq"])
    assert adapter.sent[0].method == "PUT" and "/me/library?uris=spotify%3Atrack%3A4uLU6hMCjMI75M1A2tKUQC" in \
        adapter.sent[0].url
    assert adapter.sent[1].method == "DELETE" and "/me/library?uris=" in adapter.sent[1].url


def test_real_not_found_is_kept_over_the_old_endpoints_403(make):
    from spotify_mcp.errors import from_exception

    missing = (404, {"error": {"status": 404, "message": "Resource not found"}})
    client, _, _ = make([missing, (403, {"error": {"status": 403, "message": "Forbidden"}})])
    with pytest.raises(SpotifyException) as caught:
        client.get_playlist_tracks(PLAYLIST)
    assert from_exception(caught.value).code == "not_found"


def test_search_limit_is_capped_at_ten(make):
    client, adapter, _ = make([(200, {"tracks": {"items": []}})])
    client.search("x", limit=50)
    assert "limit=10" in adapter.sent[0].url
