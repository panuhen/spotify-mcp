"""One error shape for every tool: short, speakable, and free of URLs, tracebacks and tokens.

    {"error": "<one plain sentence>", "code": "<category>", "status": <HTTP status>, "details": "<Spotify's words>"}

`error` and `code` are always there. `status` and `details` appear when Spotify answered with an
HTTP error; `details` is Spotify's own message with the URL removed, kept because clients match
on it ("No active device", "Restriction violated"). The keys stay within
{"error", "code", "status", "details", "message"}, which is what clients use to tell an error
body from a normal result.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import requests
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOauthError

from . import net

log = logging.getLogger("spotify_mcp.errors")

NO_ACTIVE_DEVICE = "no_active_device"
NOT_FOUND = "not_found"
RATE_LIMITED = "rate_limited"
NETWORK = "network"
AUTH = "auth"
PREMIUM_REQUIRED = "premium_required"
BAD_REQUEST = "bad_request"
RESTRICTED = "restricted"
FORBIDDEN = "forbidden"
UNAVAILABLE = "unavailable"
INTERNAL = "internal"

LOGIN_HINT = "Run spotify-mcp --login in a terminal to sign in again."


class ToolError(Exception):
    """An error with a code and a message fit to show or speak."""

    def __init__(self, code: str, message: str, status: int | None = None, details: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": self.message, "code": self.code}
        if self.status is not None:
            body["status"] = self.status
        if self.details:
            body["details"] = self.details
        return body


class LoginRequired(ToolError):
    def __init__(self, message: str = f"Spotify is not signed in. {LOGIN_HINT}"):
        super().__init__(AUTH, message)


_URL = re.compile(r"https?://\S+")
_PREFIX = re.compile(r"^http status: \d+, code: -?\d+ - ")
_SECRETISH = re.compile(r"(?i)bearer\s+\S+|[A-Za-z0-9_\-]{40,}")


def clean(text: Any, limit: int = 160) -> str:
    """One line, no URLs, nothing that looks like a token, at most `limit` characters."""
    text = _PREFIX.sub("", str(text or ""))
    text = _URL.sub("", text)
    text = _SECRETISH.sub("[redacted]", text)
    text = " ".join(text.replace(":\n", " ").split()).strip(" :,-")
    return text[:limit].rstrip()


def _spotify_message(exc: SpotifyException) -> str:
    """Spotify's own words, without the URL spotipy puts in front of them."""
    msg = str(getattr(exc, "msg", "") or "")
    if ":\n" in msg:
        msg = msg.split(":\n", 1)[1]
    return clean(msg)


def _seconds(value: Any) -> int | None:
    try:
        return max(1, round(float(value)))
    except (TypeError, ValueError):
        return None


def from_spotify(exc: SpotifyException) -> ToolError:
    status = getattr(exc, "http_status", None)
    reason = str(getattr(exc, "reason", "") or "").upper()
    details = _spotify_message(exc)
    lowered = details.lower()

    if reason == "NO_ACTIVE_DEVICE" or "no active device" in lowered:
        return ToolError(NO_ACTIVE_DEVICE, "No Spotify device is active. Open Spotify on a computer or phone, "
                         "or name a device_id from get_devices.", status, details)
    if status == 401:
        return ToolError(AUTH, f"Spotify rejected the sign-in. {LOGIN_HINT}", status, details)
    if status == 403:
        if reason == "PREMIUM_REQUIRED" or "premium" in lowered:
            return ToolError(PREMIUM_REQUIRED, "That needs Spotify Premium.", status, details)
        if "restriction violated" in lowered or reason in ("ALREADY_PAUSED", "NOT_PAUSED", "ENDLESS_CONTEXT",
                                                           "CONTEXT_DISALLOW"):
            return ToolError(RESTRICTED, "Spotify refused that command. The player may already be in that state, "
                             "or this device does not allow it.", status, details)
        return ToolError(FORBIDDEN, "Spotify does not allow this app to do that.", status, details)
    if status == 404:
        return ToolError(NOT_FOUND, "Spotify could not find that.", status, details)
    if status == 429:
        headers = getattr(exc, "headers", None) or {}
        seconds = _seconds(headers.get("Retry-After")) if hasattr(headers, "get") else None
        when = f"in {seconds} seconds" if seconds and seconds < 120 else "later"
        return ToolError(RATE_LIMITED, f"Spotify is limiting requests right now. Try again {when}.", status, details)
    if status is not None and status >= 500:
        return ToolError(UNAVAILABLE, "Spotify is having trouble right now. Try again in a minute.", status, details)
    if status is not None and 400 <= status < 500:
        said = f" ({details.rstrip('.')})" if details else ""
        return ToolError(BAD_REQUEST, f"Spotify rejected the request{said}.", status, details)
    return ToolError(UNAVAILABLE, "Spotify returned an unexpected error.", status, details)


def from_exception(exc: BaseException) -> ToolError:
    """Map anything a tool can raise to a ToolError. Never leaks the raw exception text."""
    if isinstance(exc, ToolError):
        return exc
    if isinstance(exc, SpotifyException):
        return from_spotify(exc)
    if isinstance(exc, SpotifyOauthError):
        kind = str(getattr(exc, "error", "") or "")
        if kind == "invalid_grant":
            return ToolError(AUTH, f"The Spotify sign-in has expired. {LOGIN_HINT}")
        return ToolError(AUTH, f"Spotify sign-in failed. {LOGIN_HINT}")
    if isinstance(exc, (net.CallTimeout, requests.exceptions.Timeout)):
        return ToolError(NETWORK, "Spotify did not answer in time. Try again.")
    if isinstance(exc, requests.exceptions.SSLError):
        return ToolError(NETWORK, "Could not connect to Spotify securely. Check the network.")
    if isinstance(exc, requests.exceptions.RequestException):
        return ToolError(NETWORK, "Could not reach Spotify. Check the internet connection and try again.")
    log.error("unexpected %s in a tool call", type(exc).__name__, exc_info=exc)
    return ToolError(INTERNAL, "Something went wrong in the Spotify tool.")
