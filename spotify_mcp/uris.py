"""Turn whatever a model or a user pastes into a Spotify URI.

Accepted: spotify:<type>:<id>, the old spotify:user:<name>:playlist:<id>, and open.spotify.com
links (with or without https://, an intl-xx/ prefix, an embed/ prefix, or a ?si= query).
A bare ID is accepted only where the caller says which type it must be.
"""

from __future__ import annotations

import re

from .errors import BAD_REQUEST, ToolError

TYPES = ("track", "album", "playlist", "artist", "episode", "show", "audiobook")
PLAYABLE_ITEMS = ("track", "episode")  # go in `uris`
CONTEXTS = ("album", "playlist", "artist", "show", "audiobook")  # go in `context_uri`

_TYPE = "|".join(TYPES)
_URI = re.compile(rf"^spotify:(?:user:[^:\s]+:)?(?P<type>{_TYPE}):(?P<id>[A-Za-z0-9]+)$", re.IGNORECASE)
_LINK = re.compile(
    rf"^(?:https?://)?open\.spotify\.com/(?:intl-[a-z]{{2}}(?:-[a-z]{{2}})?/)?(?:embed/)?(?:user/[^/\s]+/)?"
    rf"(?P<type>{_TYPE})/(?P<id>[A-Za-z0-9]+)/?(?:[?#].*)?$",
    re.IGNORECASE,
)
_BARE_ID = re.compile(r"^[A-Za-z0-9]{22}$")


def parse(value: str | None) -> tuple[str, str] | None:
    """(type, uri) for a URI or link, or None when it is neither."""
    text = (value or "").strip().strip("<>\"'")
    match = _URI.match(text) or _LINK.match(text)
    if not match:
        return None
    kind = match.group("type").lower()
    return kind, f"spotify:{kind}:{match.group('id')}"


def to_uri(value: str | None, *, expect: tuple[str, ...] = TYPES, bare_type: str | None = None,
           argument: str = "uri") -> tuple[str, str]:
    """(type, uri), or a bad_request ToolError naming what was wrong."""
    text = (value or "").strip()
    parsed = parse(text)
    if parsed is None and bare_type and _BARE_ID.match(text):
        parsed = (bare_type, f"spotify:{bare_type}:{text}")
    if parsed is None:
        raise ToolError(BAD_REQUEST, f"{argument} must be a Spotify URI like spotify:track:<id> or an "
                        "open.spotify.com link. Use search to find one.")
    if parsed[0] not in expect:
        article = "an" if parsed[0][0] in "aeiou" else "a"
        raise ToolError(BAD_REQUEST, f"{argument} must be a {' or '.join(expect)}, not {article} {parsed[0]}.")
    return parsed


def to_id(value: str | None, kind: str, argument: str) -> str:
    """The bare ID for an ID, URI or link of the given type."""
    return to_uri(value, expect=(kind,), bare_type=kind, argument=argument)[1].rsplit(":", 1)[1]
