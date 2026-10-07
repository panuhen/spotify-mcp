"""Playlists by name, Liked Songs and the playing track: matching, dedupe, ownership, errors."""

import json

import pytest

from spotify_mcp import playlists, server
from spotify_mcp.errors import ToolError
from tests.test_client import PLAYING, TRACK, make  # noqa: F401 - make is a fixture
from tests.test_server import ALLOWED_KEYS, use  # noqa: F401 - use is a fixture

ME = (200, {"id": "me", "display_name": "Me"})
OTHER = "5ZcgXmmyhoAuS0EaB2wcBO"


def pid(n):
    return f"{n:0>22}"


def row(n, name, owner="me", collaborative=False, total=3):
    return {"id": pid(n), "name": name, "uri": f"spotify:playlist:{pid(n)}", "owner": {"id": owner},
            "collaborative": collaborative, "items": {"total": total}}


LIBRARY = [row(1, "Schranz"), row(2, "Running Mix"), row(3, "Chill Vibes"), row(4, "Café Jazz"),
           row(5, "Morning Run"), row(6, "Hard Schranz 2024"), row(7, "Today's Top Hits", owner="spotify"),
           row(8, "Band Practice", owner="friend", collaborative=True)]


def page(rows, next_url=None):
    return (200, {"items": rows, "total": len(rows), "next": next_url})


def items(*uris):
    return (200, {"items": [{"item": {"uri": u, "name": "Song", "artists": [{"name": "Band"}]}} for u in uris],
                  "next": None, "total": len(uris)})


NOTHING = (204, None)


def sent(adapter):
    return [(r.method, r.url.split("/v1/", 1)[1].split("?", 1)[0].rstrip("/")) for r in adapter.sent]


def body(request):
    return json.loads(request.body) if request.body else None


# --- matching ----------------------------------------------------------------------------

NAMES = [{"name": n, "owned": True} for n in ["Schranz", "Running Mix", "Morning Run", "Chill Vibes", "Darkwave",
                                               "Techno Bunker", "Brunch", "Café Jazz", "Hard Schranz 2024"]]


@pytest.mark.parametrize("query, tier, expected", [
    ("Schranz", playlists.EXACT, ["Schranz"]),
    ("SCHRANZ", playlists.EXACT, ["Schranz"]),
    ("cafe jazz", playlists.NORMALIZED, ["Café Jazz"]),
    ("  Café   Jazz!! ", playlists.NORMALIZED, ["Café Jazz"]),
    ("my running mix playlist", playlists.NORMALIZED, ["Running Mix"]),
    ("running", playlists.PARTIAL, ["Running Mix"]),
    ("add it to techno bunker please", playlists.PARTIAL, ["Techno Bunker"]),
    ("shrance", playlists.FUZZY, ["Schranz"]),
    ("hard shrance", playlists.FUZZY, ["Hard Schranz 2024"]),
    ("tecno bunker", playlists.FUZZY, ["Techno Bunker"]),
    ("dark wave", playlists.FUZZY, ["Darkwave"]),
    ("run", playlists.PARTIAL, ["Morning Run", "Running Mix"]),
])
def test_match_tiers(query, tier, expected):
    found_tier, found = playlists.match(query, NAMES)
    assert found_tier == tier
    assert [p["name"] for p in found] == expected


def test_partial_needs_whole_word_starts():
    # "run" is inside "Brunch" but does not start a word of it.
    assert "Brunch" not in [p["name"] for p in playlists.match("run", NAMES)[1]]


def test_no_match_offers_the_closest():
    tier, found = playlists.match("chilout", NAMES)
    assert tier == playlists.NONE and [p["name"] for p in found] == ["Chill Vibes"]
    assert playlists.match("polka", NAMES) == (playlists.NONE, [])


def test_same_name_owned_beats_followed():
    rows = [{"name": "Chill", "owned": False}, {"name": "Chill", "owned": True}]
    assert playlists.match("chill", rows)[1] == [{"name": "Chill", "owned": True}]


# --- like_current --------------------------------------------------------------------------


def test_like_current_saves_the_playing_track(make):
    client, adapter, _ = make([PLAYING, (200, [False]), (200, None)])
    assert client.like_current() == {"liked": "Blue Monday – New Order"}
    assert sent(adapter) == [("GET", "me/player/currently-playing"), ("GET", "me/library/contains"),
                             ("PUT", "me/library")]
    assert "uris=spotify%3Atrack%3A4uLU6hMCjMI75M1A2tKUQC" in adapter.sent[2].url


def test_like_current_already_liked_does_not_write(make):
    client, adapter, _ = make([PLAYING, (200, [True])])
    assert client.like_current() == {"already_liked": "Blue Monday – New Order"}
    assert len(adapter.sent) == 2


def test_like_current_with_nothing_playing(use):  # noqa: F811
    use([NOTHING])
    data, is_error = server.run_tool("like_current", {})
    assert is_error and data == {"error": "Nothing is playing right now.", "code": "not_found"}


# --- add_current_to_playlist ---------------------------------------------------------------


def test_add_current_by_fuzzy_name(make):
    client, adapter, _ = make([PLAYING, ME, page(LIBRARY), items(), (201, {"snapshot_id": "s"})])
    assert client.add_current_to_playlist("shrance") == {"added": "Blue Monday – New Order", "playlist": "Schranz"}
    assert sent(adapter)[-2:] == [("GET", f"playlists/{pid(1)}/items"), ("POST", f"playlists/{pid(1)}/items")]
    assert body(adapter.sent[-1]) == {"uris": [TRACK]}


def test_add_current_skips_a_duplicate(make):
    client, adapter, _ = make([PLAYING, ME, page(LIBRARY), items("spotify:track:x", TRACK)])
    assert client.add_current_to_playlist("schranz") == {"already_there": "Blue Monday – New Order",
                                                          "playlist": "Schranz"}
    assert all(r.method == "GET" for r in adapter.sent)


def test_add_current_reads_every_page_before_deciding(make):
    first = (200, {"items": [{"item": {"uri": "spotify:track:a"}}] * 100, "next": "more", "total": 101})
    client, adapter, _ = make([PLAYING, ME, page(LIBRARY), first, items(TRACK)])
    assert "already_there" in client.add_current_to_playlist("Schranz")
    assert "offset=100" in adapter.sent[-1].url


def test_add_current_reads_every_page_of_playlists(make):
    client, adapter, _ = make([PLAYING, ME, page(LIBRARY[:4], next_url="more"), page(LIBRARY[4:]), items(),
                               (201, {})])
    assert client.add_current_to_playlist("morning run")["playlist"] == "Morning Run"
    assert "offset=4" in adapter.sent[3].url


def test_add_current_to_a_collaborative_playlist(make):
    client, _, _ = make([PLAYING, ME, page(LIBRARY), items(), (201, {})])
    assert client.add_current_to_playlist("band practice")["playlist"] == "Band Practice"


def test_add_current_ambiguous_lists_names(use):  # noqa: F811
    adapter = use([PLAYING, ME, page(LIBRARY)])
    data, is_error = server.run_tool("add_current_to_playlist", {"playlist": "run"})
    assert is_error and data["code"] == "bad_request"
    assert data["error"] == "Several playlists match 'run': Morning Run, Running Mix. Which one?"
    assert set(data) <= ALLOWED_KEYS
    assert all(r.method == "GET" for r in adapter.sent)


def test_ambiguous_lists_at_most_five(make):
    rows = [row(n, f"Run {n}") for n in range(1, 9)]
    client, _, _ = make([PLAYING, ME, page(rows)])
    with pytest.raises(ToolError) as caught:
        client.add_current_to_playlist("run")
    assert caught.value.message.count("Run ") == 5 and "and 3 more" in caught.value.message


def test_identical_names_are_told_apart_by_id(make):
    client, _, _ = make([PLAYING, ME, page([row(1, "Gym"), row(2, "Gym")])])
    with pytest.raises(ToolError) as caught:
        client.add_current_to_playlist("gym")
    assert f"Gym (id {pid(1)})" in caught.value.message and f"Gym (id {pid(2)})" in caught.value.message


def test_add_current_to_someone_elses_playlist_is_forbidden(use):  # noqa: F811
    adapter = use([PLAYING, ME, page(LIBRARY)])
    data, is_error = server.run_tool("add_current_to_playlist", {"playlist": "todays top hits"})
    assert is_error and data == {
        "error": "The playlist 'Today's Top Hits' belongs to someone else. Spotify only lets you change your own "
                 "or collaborative playlists.", "code": "forbidden"}
    assert len(adapter.sent) == 3


def test_add_current_by_uri_of_someone_elses_playlist(make):
    other = (200, {"id": OTHER, "name": "Their Mix", "uri": f"spotify:playlist:{OTHER}", "owner": {"id": "x"}})
    client, adapter, _ = make([PLAYING, ME, page(LIBRARY), other])
    with pytest.raises(ToolError) as caught:
        client.add_current_to_playlist(f"https://open.spotify.com/playlist/{OTHER}?si=1")
    assert caught.value.code == "forbidden" and "Their Mix" in caught.value.message
    assert sent(adapter)[-1] == ("GET", f"playlists/{OTHER}")


def test_add_current_by_link_of_own_playlist(make):
    client, adapter, _ = make([PLAYING, ME, page(LIBRARY), items(), (201, {})])
    assert client.add_current_to_playlist(f"spotify:playlist:{pid(3)}")["playlist"] == "Chill Vibes"
    assert sent(adapter)[-1] == ("POST", f"playlists/{pid(3)}/items")


def test_add_current_not_found_names_the_closest(use):  # noqa: F811
    use([PLAYING, ME, page(LIBRARY)])
    data, is_error = server.run_tool("add_current_to_playlist", {"playlist": "chilout"})
    assert is_error and data == {"error": "No playlist of yours matches 'chilout'. Closest: Chill Vibes.",
                                 "code": "not_found"}


def test_create_if_missing_creates_a_private_playlist_and_adds(make):
    made = (201, {"id": OTHER, "name": "Gym", "uri": f"spotify:playlist:{OTHER}"})
    client, adapter, _ = make([PLAYING, ME, page(LIBRARY), made, (201, {})])
    assert client.add_current_to_playlist("Gym", create_if_missing=True) == {
        "added": "Blue Monday – New Order", "playlist": "Gym", "created": True}
    assert sent(adapter)[-2:] == [("POST", "me/playlists"), ("POST", f"playlists/{OTHER}/items")]
    assert body(adapter.sent[-2]) == {"name": "Gym", "public": False}


def test_create_if_missing_uses_an_existing_match(make):
    client, adapter, _ = make([PLAYING, ME, page(LIBRARY), items(), (201, {})])
    assert "created" not in client.add_current_to_playlist("Schranz", create_if_missing=True)
    assert ("POST", "me/playlists") not in sent(adapter)


def test_create_if_missing_does_not_create_for_a_followed_match(make):
    client, adapter, _ = make([PLAYING, ME, page(LIBRARY)])
    with pytest.raises(ToolError) as caught:
        client.add_current_to_playlist("Today's Top Hits", create_if_missing=True)
    assert caught.value.code == "forbidden"
    assert all(r.method == "GET" for r in adapter.sent)


def test_playlist_list_is_reused_and_refreshed_after_a_write(make):
    client, adapter, _ = make([PLAYING, ME, page(LIBRARY), items(), (201, {}),
                               PLAYING, page(LIBRARY), items(TRACK)])
    client.add_current_to_playlist("Schranz")
    assert "already_there" in client.add_current_to_playlist("Schranz")
    assert [m for m in sent(adapter) if m[1] == "me"] == [("GET", "me")]  # the user's ID once
    assert len([m for m in sent(adapter) if m[1] == "me/playlists"]) == 2


def test_post_is_not_repeated_after_a_lost_answer(make):
    from tests.test_net import stale

    client, adapter, _ = make([PLAYING, ME, page(LIBRARY), items(), stale()])
    with pytest.raises(Exception):
        client.add_current_to_playlist("Schranz")
    assert [r.method for r in adapter.sent].count("POST") == 1


# --- find_playlist and play(playlist=) -----------------------------------------------------


def test_find_playlist_returns_matches_with_owner_flag(use):  # noqa: F811
    use([ME, page(LIBRARY)])
    data, is_error = server.run_tool("find_playlist", {"query": "schranz"})
    assert not is_error
    assert data == {"playlists": [{"name": "Schranz", "id": pid(1), "uri": f"spotify:playlist:{pid(1)}",
                                   "owned": True, "tracks": 3}]}


def test_find_playlist_includes_followed_and_lists_several(make):
    client, _, _ = make([ME, page(LIBRARY)])
    assert client.find_playlist("todays top hits")["playlists"][0]["owned"] is False
    assert [p["name"] for p in client.find_playlist("run")["playlists"]] == ["Morning Run", "Running Mix"]


def test_find_playlist_not_found(make):
    client, _, _ = make([ME, page(LIBRARY)])
    with pytest.raises(ToolError) as caught:
        client.find_playlist("polka")
    assert caught.value.code == "not_found" and caught.value.message == "No playlist in your library matches 'polka'."


def test_play_playlist_by_name(make):
    client, adapter, _ = make([ME, page(LIBRARY), (204, None)])
    client.play(playlist="my running playlist")
    assert body(adapter.sent[-1]) == {"context_uri": f"spotify:playlist:{pid(2)}"}


def test_play_followed_playlist_by_name(use):  # noqa: F811
    adapter = use([ME, page(LIBRARY), (204, None)])
    data, is_error = server.run_tool("play", {"playlist": "today's top hits"})
    assert not is_error
    assert json.loads(adapter.sent[-1].body) == {"context_uri": f"spotify:playlist:{pid(7)}"}


def test_play_playlist_and_context_uri_conflict(make):
    client, adapter, _ = make([])
    with pytest.raises(ToolError):
        client.play(playlist="x", context_uri="spotify:album:1DFixLWuPkv3KT3TnV35m3")
    assert adapter.sent == []


# --- remove_from_playlist ------------------------------------------------------------------


def test_remove_current(make):
    client, adapter, _ = make([PLAYING, ME, page(LIBRARY), items(TRACK), (200, {"snapshot_id": "s"})])
    assert client.remove_from_playlist("schranz", "current") == {"removed": "Blue Monday – New Order",
                                                                  "playlist": "Schranz"}
    assert sent(adapter)[-1] == ("DELETE", f"playlists/{pid(1)}/items")
    assert body(adapter.sent[-1]) == {"items": [{"uri": TRACK}]}


def test_remove_by_link_names_the_track_from_the_playlist(make):
    client, adapter, _ = make([ME, page(LIBRARY), items(TRACK), (200, {})])
    result = client.remove_from_playlist("Schranz", "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC")
    assert result == {"removed": "Song – Band", "playlist": "Schranz"}


def test_remove_a_track_that_is_not_there_writes_nothing(make):
    client, adapter, _ = make([ME, page(LIBRARY), items()])
    assert client.remove_from_playlist("Schranz", TRACK) == {"not_in_playlist": TRACK, "playlist": "Schranz"}
    assert all(r.method == "GET" for r in adapter.sent)


def test_remove_from_someone_elses_playlist_is_forbidden(make):
    client, adapter, _ = make([ME, page(LIBRARY)])
    with pytest.raises(ToolError) as caught:
        client.remove_from_playlist("Today's Top Hits", TRACK)
    assert caught.value.code == "forbidden"


def test_remove_rejects_an_album(use):  # noqa: F811
    use([])
    data, is_error = server.run_tool("remove_from_playlist", {"playlist": "Schranz",
                                                              "track": "spotify:album:1DFixLWuPkv3KT3TnV35m3"})
    assert is_error and data["code"] == "bad_request"


# --- create_playlist -----------------------------------------------------------------------


def test_create_playlist_private_by_default(make):
    made = (201, {"id": OTHER, "name": "Gym", "uri": f"spotify:playlist:{OTHER}"})
    client, adapter, _ = make([ME, page(LIBRARY), made])
    assert client.create_playlist("Gym", "lifting") == {"created": "Gym", "id": OTHER,
                                                       "uri": f"spotify:playlist:{OTHER}"}
    assert sent(adapter)[-1] == ("POST", "me/playlists")
    assert body(adapter.sent[-1]) == {"name": "Gym", "public": False, "description": "lifting"}


def test_create_playlist_refuses_a_duplicate_name(use):  # noqa: F811
    adapter = use([ME, page(LIBRARY)])
    data, is_error = server.run_tool("create_playlist", {"name": "cafe jazz"})
    assert is_error and data == {"error": "You already have a playlist called 'Café Jazz'. Use it, or pass "
                                          "force=true to make another.", "code": "bad_request"}
    assert all(r.method == "GET" for r in adapter.sent)


def test_create_playlist_force_skips_the_check(use):  # noqa: F811
    adapter = use([(201, {"id": OTHER, "name": "Café Jazz"})])
    data, is_error = server.run_tool("create_playlist", {"name": "Café Jazz", "force": "yes", "public": True})
    assert not is_error and data["created"] == "Café Jazz"
    assert json.loads(adapter.sent[0].body) == {"name": "Café Jazz", "public": True}


def test_create_playlist_name_of_a_followed_playlist_is_fine(make):
    client, _, _ = make([ME, page(LIBRARY), (201, {"id": OTHER, "name": "Today's Top Hits"})])
    assert client.create_playlist("Today's Top Hits")["created"] == "Today's Top Hits"


@pytest.mark.parametrize("tool, args, missing", [
    ("add_current_to_playlist", {}, "playlist"),
    ("find_playlist", {"query": " "}, "query"),
    ("remove_from_playlist", {"playlist": "x"}, "track"),
    ("create_playlist", {}, "name"),
])
def test_missing_arguments(use, tool, args, missing):  # noqa: F811
    use([])
    assert server.run_tool(tool, args) == ({"error": f"Missing argument: {missing}.", "code": "bad_request"}, True)




def test_get_playlists_default_limit_is_fifty(use):  # noqa: F811
    adapter = use([page([])])
    server.run_tool("get_playlists", {})
    assert "limit=50" in adapter.sent[0].url
