import pytest

from spotify_mcp import uris
from spotify_mcp.errors import ToolError

ID = "4uLU6hMCjMI75M1A2tKUQC"


@pytest.mark.parametrize("value, expected", [
    (f"spotify:track:{ID}", ("track", f"spotify:track:{ID}")),
    (f"https://open.spotify.com/track/{ID}?si=abc123", ("track", f"spotify:track:{ID}")),
    (f"open.spotify.com/album/{ID}", ("album", f"spotify:album:{ID}")),
    (f"https://open.spotify.com/intl-fi/playlist/{ID}", ("playlist", f"spotify:playlist:{ID}")),
    (f"https://open.spotify.com/intl-pt-br/artist/{ID}/", ("artist", f"spotify:artist:{ID}")),
    (f"https://open.spotify.com/embed/episode/{ID}", ("episode", f"spotify:episode:{ID}")),
    (f"spotify:user:someone:playlist:{ID}", ("playlist", f"spotify:playlist:{ID}")),
    (f"  <spotify:track:{ID}>  ", ("track", f"spotify:track:{ID}")),
])
def test_parse(value, expected):
    assert uris.parse(value) == expected


@pytest.mark.parametrize("value", ["", None, "blue monday", "https://example.com/track/x", "spotify:track:"])
def test_parse_rejects(value):
    assert uris.parse(value) is None


def test_to_uri_bare_id_only_with_type():
    assert uris.to_uri(ID, bare_type="track") == ("track", f"spotify:track:{ID}")
    with pytest.raises(ToolError) as caught:
        uris.to_uri(ID)
    assert caught.value.code == "bad_request"


def test_to_uri_wrong_type():
    with pytest.raises(ToolError) as caught:
        uris.to_uri(f"spotify:album:{ID}", expect=("track", "episode"))
    assert "track or episode" in caught.value.message


def test_to_id():
    assert uris.to_id(f"https://open.spotify.com/playlist/{ID}?si=x", "playlist", "playlist_id") == ID
    assert uris.to_id(ID, "playlist", "playlist_id") == ID
