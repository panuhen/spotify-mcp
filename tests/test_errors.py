"""The error shape: short, speakable, categorised, and safe to show."""

import requests
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOauthError

from spotify_mcp import errors, net
from tests.test_net import stale

# The keys a client may see in an error body (Strawberry's looks_like_error accepts only these).
ALLOWED_KEYS = {"error", "status", "details", "code", "message"}


def spotify_error(status, message, reason=None, headers=None, url="https://api.spotify.com/v1/me/player/play"):
    return SpotifyException(status, -1, f"{url}:\n {message}", reason=reason, headers=headers)


def check(body):
    assert set(body) <= ALLOWED_KEYS
    assert body["error"] and body["code"]
    text = " ".join(str(v) for v in body.values())
    assert "http" not in text.lower() and "\n" not in text and "Traceback" not in text
    return body


def test_stale_connection_becomes_a_network_error():
    body = check(errors.from_exception(stale()).as_dict())
    assert body == {"error": "Could not reach Spotify. Check the internet connection and try again.",
                    "code": "network"}


def test_timeouts():
    assert errors.from_exception(requests.exceptions.ReadTimeout("x")).code == "network"
    assert "in time" in errors.from_exception(net.CallTimeout("x")).message


def test_no_active_device_keeps_spotify_words_in_details():
    exc = spotify_error(404, "Player command failed: No active device found", reason="NO_ACTIVE_DEVICE")
    body = check(errors.from_exception(exc).as_dict())
    assert body["code"] == "no_active_device"
    assert body["status"] == 404
    assert "No active device" in body["details"]  # what Strawberry's adapter matches on


def test_restriction_violated():
    body = check(errors.from_exception(spotify_error(403, "Player command failed: Restriction violated",
                                                     reason="UNKNOWN")).as_dict())
    assert body["code"] == "restricted"
    assert body["status"] == 403
    assert "Restriction violated" in body["details"]


def test_premium_and_forbidden():
    assert errors.from_exception(spotify_error(403, "Player command failed: Premium required",
                                               reason="PREMIUM_REQUIRED")).code == "premium_required"
    assert errors.from_exception(spotify_error(403, "Forbidden")).code == "forbidden"


def test_bad_request_names_spotify_reason_without_url():
    body = check(errors.from_exception(spotify_error(400, "Non supported context uri")).as_dict())
    assert body["code"] == "bad_request"
    assert body["error"] == "Spotify rejected the request (Non supported context uri)."


def test_rate_limited_says_when():
    body = check(errors.from_exception(spotify_error(429, "API rate limit exceeded",
                                                     headers={"Retry-After": "30"})).as_dict())
    assert body["code"] == "rate_limited"
    assert "30 seconds" in body["error"]
    assert "later" in errors.from_exception(spotify_error(429, "x", headers={"Retry-After": "7200"})).message


def test_not_found_auth_unavailable():
    assert errors.from_exception(spotify_error(404, "Resource not found")).code == "not_found"
    assert errors.from_exception(spotify_error(401, "The access token expired")).code == "auth"
    assert errors.from_exception(spotify_error(502, "Bad gateway")).code == "unavailable"
    assert errors.from_exception(SpotifyOauthError("x", error="invalid_grant")).code == "auth"


def test_unexpected_exception_is_generic():
    body = check(errors.from_exception(KeyError("secret-looking-internal-detail")).as_dict())
    assert body == {"error": "Something went wrong in the Spotify tool.", "code": "internal"}


def test_clean_removes_urls_and_tokens():
    text = errors.clean("http status: 400, code: -1 - https://api.spotify.com/v1/x?y=1:\n Bad  Bearer abc.def "
                        + "A" * 60)
    assert "http" not in text and "abc.def" not in text and "A" * 40 not in text
    assert text.startswith("Bad")


def test_missing_spotify_message_gives_no_details():
    body = errors.from_exception(SpotifyException(500, -1, "https://api.spotify.com/v1/me:\n None")).as_dict()
    assert "details" not in body
