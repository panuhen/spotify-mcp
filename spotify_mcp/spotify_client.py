"""Spotify API wrapper.

Methods return plain result dicts and raise on failure; server.py turns every exception into
the error shape in errors.py.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

import spotipy
from spotipy.exceptions import SpotifyException

from . import uris
from .errors import BAD_REQUEST, NO_ACTIVE_DEVICE, ToolError, from_spotify

log = logging.getLogger("spotify_mcp.client")

# What `play` does when no device is active: "off" (default) reports no_active_device;
# "auto" moves playback to the only available device, or to the device this server last saw
# active, and still reports no_active_device when it cannot tell which one the user means.
AUTO_DEVICE = os.environ.get("SPOTIFY_MCP_AUTO_DEVICE", "off").strip().lower()
# "1" trims results for small context windows (see README).
COMPACT = os.environ.get("SPOTIFY_MCP_COMPACT", "").strip().lower() in ("1", "true", "yes", "on")


def _artists(item: dict[str, Any]) -> list[str]:
    return [a.get("name", "") for a in item.get("artists") or [] if a]


def _album_name(item: dict[str, Any]) -> str:
    return (item.get("album") or {}).get("name", "")


def _total(playlist: dict[str, Any]) -> int:
    # Spotify has called the track count both "tracks" and "items".
    for key in ("tracks", "items"):
        value = playlist.get(key)
        if isinstance(value, dict) and isinstance(value.get("total"), int):
            return value["total"]
    return 0


def _is_no_device(exc: SpotifyException) -> bool:
    return from_spotify(exc).code == NO_ACTIVE_DEVICE


class SpotifyClient:
    """Wrapper around spotipy: compact results, one retry after a 401, URI clean-up."""

    def __init__(self, spotify: spotipy.Spotify, auto_device: str | None = None, compact: bool | None = None):
        self.sp = spotify
        self.auto_device = AUTO_DEVICE if auto_device is None else auto_device
        self.compact = COMPACT if compact is None else compact
        self.last_device: dict[str, Any] | None = None  # the last device seen active

    def _api(self, call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Call spotipy; on a 401 refresh the token once and try again."""
        try:
            return call(*args, **kwargs)
        except SpotifyException as exc:
            refresh = getattr(self.sp.auth_manager, "force_refresh", None)
            if exc.http_status != 401 or refresh is None:
                raise
            log.warning("Spotify answered 401; refreshing the token and retrying once")
            refresh()
            return call(*args, **kwargs)

    def _remember(self, device: dict[str, Any] | None) -> None:
        if device and device.get("id") and device.get("is_active"):
            self.last_device = {"id": device["id"], "name": device.get("name", "")}

    # Playback Control

    @staticmethod
    def _play_target(uri: str | None, context_uri: str | None) -> dict[str, Any]:
        """start_playback arguments for a track and/or a context, whichever slot they came in."""
        items: list[str] = []
        context: str | None = None
        for value, argument in ((uri, "uri"), (context_uri, "context_uri")):
            if not value:
                continue
            kind, normalized = uris.to_uri(value, argument=argument)
            if kind in uris.PLAYABLE_ITEMS:
                items.append(normalized)
            elif context is None:
                context = normalized
            else:
                raise ToolError(BAD_REQUEST, "Give one album, playlist or artist to play, not two.")
        if context and items:
            if context.startswith(("spotify:album:", "spotify:playlist:")):
                # Play the album or playlist, starting at that track.
                return {"context_uri": context, "offset": {"uri": items[0]}}
            return {"uris": items}
        if context:
            return {"context_uri": context}
        if items:
            return {"uris": items}
        return {}

    def play(
        self,
        uri: str | None = None,
        context_uri: str | None = None,
        device_id: str | None = None,
        position_ms: int = 0,
    ) -> dict[str, Any]:
        """Resume playback, or play a track (uris) or an album/playlist/artist (context).

        A track given as context_uri (Spotify answers 400 "Non supported context uri") goes in
        `uris` instead, and an album given as uri goes in context_uri. Links become URIs.
        """
        target = self._play_target(uri, context_uri)
        if position_ms:
            target["position_ms"] = position_ms
        message = "Playback started" if target else "Playback resumed"
        try:
            self._api(self.sp.start_playback, device_id=device_id, **target)
        except SpotifyException as exc:
            if device_id or self.auto_device != "auto" or not _is_no_device(exc):
                raise
            device = self._pick_device()
            if device is None:
                raise
            if target:
                self._api(self.sp.start_playback, device_id=device["id"], **target)
            else:
                self._api(self.sp.transfer_playback, device["id"], force_play=True)
            self.last_device = device
            return {"success": True, "message": f"{message} on {device['name']}", "device": device["name"]}
        return {"success": True, "message": message}

    def _pick_device(self) -> dict[str, Any] | None:
        """The device the user most likely means: the only one, or the last one seen active."""
        devices = [d for d in (self._api(self.sp.devices) or {}).get("devices", [])
                   if d and d.get("id") and not d.get("is_restricted")]
        if len(devices) == 1:
            return {"id": devices[0]["id"], "name": devices[0].get("name", "")}
        if self.last_device and any(d["id"] == self.last_device["id"] for d in devices):
            return dict(self.last_device)
        if devices:
            names = ", ".join(d.get("name", "?") for d in devices[:5])
            raise ToolError(NO_ACTIVE_DEVICE, f"No Spotify device is active. Available: {names}. "
                            "Pass device_id from get_devices to choose one.", 404)
        return None

    def pause(self, device_id: str | None = None) -> dict[str, Any]:
        """Pause playback."""
        self._api(self.sp.pause_playback, device_id=device_id)
        return {"success": True, "message": "Playback paused"}

    def next_track(self, device_id: str | None = None) -> dict[str, Any]:
        """Skip to next track."""
        self._api(self.sp.next_track, device_id=device_id)
        return {"success": True, "message": "Skipped to next track"}

    def previous_track(self, device_id: str | None = None) -> dict[str, Any]:
        """Go to previous track."""
        self._api(self.sp.previous_track, device_id=device_id)
        return {"success": True, "message": "Went to previous track"}

    def seek(self, position_ms: int, device_id: str | None = None) -> dict[str, Any]:
        """Seek to position in current track."""
        position_ms = max(0, int(position_ms))
        self._api(self.sp.seek_track, position_ms, device_id=device_id)
        return {"success": True, "message": f"Seeked to {position_ms}ms"}

    def set_volume(self, volume: int, device_id: str | None = None) -> dict[str, Any]:
        """Set playback volume (0-100)."""
        volume = max(0, min(100, int(volume)))
        self._api(self.sp.volume, volume, device_id=device_id)
        return {"success": True, "message": f"Volume set to {volume}%"}

    def shuffle(self, state: bool, device_id: str | None = None) -> dict[str, Any]:
        """Toggle shuffle mode."""
        self._api(self.sp.shuffle, state, device_id=device_id)
        return {"success": True, "message": f"Shuffle {'on' if state else 'off'}"}

    def repeat(self, state: str, device_id: str | None = None) -> dict[str, Any]:
        """Set repeat mode (off/track/context)."""
        if state not in ("off", "track", "context"):
            raise ToolError(BAD_REQUEST, "state must be 'off', 'track' or 'context'.")
        self._api(self.sp.repeat, state, device_id=device_id)
        return {"success": True, "message": f"Repeat mode set to {state}"}

    # Information

    def get_current_track(self) -> dict[str, Any]:
        """Get currently playing track info."""
        current = self._api(self.sp.current_user_playing_track)
        if not current or not current.get("item"):
            return {"playing": False, "message": "Nothing currently playing"}

        track = current["item"]
        return {
            "playing": current.get("is_playing", False),
            "track": {
                "name": track.get("name", ""),
                "uri": track.get("uri", ""),
                "artists": _artists(track),
                "album": _album_name(track),
                "duration_ms": track.get("duration_ms", 0),
                "progress_ms": current.get("progress_ms", 0),
            },
        }

    def get_playback_state(self) -> dict[str, Any]:
        """Get full playback state."""
        state = self._api(self.sp.current_playback)
        if not state:
            return {"active": False, "message": "No active playback"}

        result: dict[str, Any] = {
            "active": True,
            "is_playing": state.get("is_playing", False),
            "shuffle": state.get("shuffle_state", False),
            "repeat": state.get("repeat_state", "off"),
            "progress_ms": state.get("progress_ms", 0),
        }

        device = state.get("device")
        if device:
            self._remember(device)
            result["device"] = {
                "id": device.get("id"),
                "name": device.get("name", ""),
                "type": device.get("type", ""),
                "volume": device.get("volume_percent"),
            }

        if state.get("item"):
            track = state["item"]
            result["track"] = {
                "name": track.get("name", ""),
                "uri": track.get("uri", ""),
                "artists": _artists(track),
                "album": _album_name(track),
                "duration_ms": track.get("duration_ms", 0),
            }

        return result

    def get_queue(self) -> dict[str, Any]:
        """Get upcoming tracks in queue."""
        queue = self._api(self.sp.queue)
        if not queue:
            return {"queue": []}

        cap = 10 if self.compact else 20
        tracks = [
            {"name": item.get("name", ""), "uri": item.get("uri", ""), "artists": _artists(item)}
            for item in (queue.get("queue") or [])[:cap]
            if item
        ]
        result: dict[str, Any] = {"queue": tracks}

        current = queue.get("currently_playing")
        if current:
            result["currently_playing"] = {
                "name": current.get("name", ""),
                "uri": current.get("uri", ""),
                "artists": _artists(current),
            }

        return result

    def get_devices(self) -> dict[str, Any]:
        """List available Spotify devices."""
        devices = (self._api(self.sp.devices) or {}).get("devices", [])
        for device in devices:
            self._remember(device)
        return {
            "devices": [
                {
                    "id": d.get("id"),
                    "name": d.get("name", ""),
                    "type": d.get("type", ""),
                    "is_active": d.get("is_active", False),
                    "volume": d.get("volume_percent"),
                }
                for d in devices
                if d
            ]
        }

    # Search & Library

    def search(
        self,
        query: str,
        types: list[str] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Search for tracks, albums, artists, or playlists."""
        valid_types = ("track", "album", "artist", "playlist")
        types = [t for t in (types or []) if t in valid_types] or ["track"]
        if limit is None:
            limit = 5 if self.compact else 10
        limit = max(1, min(10, int(limit)))  # Spotify answers 400 "Invalid limit" above 10

        results = self._api(self.sp.search, q=query, type=",".join(types), limit=limit) or {}

        output: dict[str, Any] = {}

        if "tracks" in results:
            output["tracks"] = [
                {"name": t.get("name", ""), "uri": t.get("uri", ""), "artists": _artists(t), "album": _album_name(t)}
                for t in (results["tracks"] or {}).get("items") or []
                if t
            ]

        if "albums" in results:
            output["albums"] = [
                {"name": a.get("name", ""), "uri": a.get("uri", ""), "artists": _artists(a)}
                for a in (results["albums"] or {}).get("items") or []
                if a
            ]

        if "artists" in results:
            output["artists"] = []
            for a in (results["artists"] or {}).get("items") or []:
                if not a:
                    continue
                entry: dict[str, Any] = {"name": a.get("name", ""), "uri": a.get("uri", "")}
                if not self.compact:
                    entry["genres"] = a.get("genres", [])
                output["artists"].append(entry)

        if "playlists" in results:
            output["playlists"] = [
                {
                    "name": p.get("name", ""),
                    "uri": p.get("uri", ""),
                    "owner": (p.get("owner") or {}).get("display_name", ""),
                    "tracks": _total(p),
                }
                for p in (results["playlists"] or {}).get("items") or []
                if p
            ]

        return output

    def add_to_queue(self, uri: str, device_id: str | None = None) -> dict[str, Any]:
        """Add a track or episode to the playback queue."""
        _kind, normalized = uris.to_uri(uri, expect=uris.PLAYABLE_ITEMS, bare_type="track")
        self._api(self.sp.add_to_queue, normalized, device_id=device_id)
        return {"success": True, "message": "Added to queue"}

    def get_playlists(self, limit: int = 50) -> dict[str, Any]:
        """List user's playlists."""
        limit = max(1, min(50, int(limit)))
        playlists = self._api(self.sp.current_user_playlists, limit=limit) or {}
        return {
            "playlists": [
                {
                    "name": p.get("name", ""),
                    "uri": p.get("uri", ""),
                    "id": p.get("id", ""),
                    "tracks": _total(p),
                    "owner": (p.get("owner") or {}).get("display_name", ""),
                }
                for p in playlists.get("items") or []
                if p is not None
            ]
        }

    def _track_rows(self, results: dict[str, Any]) -> dict[str, Any]:
        tracks = []
        for item in results.get("items") or []:
            track = (item or {}).get("track") or (item or {}).get("item")
            if not track:
                continue
            row: dict[str, Any] = {"name": track.get("name", ""), "uri": track.get("uri", ""),
                                   "artists": _artists(track)}
            if not self.compact:
                row["album"] = _album_name(track)
                row["added_at"] = item.get("added_at")
            tracks.append(row)
        return {"tracks": tracks, "total": results.get("total", len(tracks))}

    def _current_or_old(self, current: Callable[[], Any], old: Callable[[], Any]) -> Any:
        """Call Spotify's current endpoint; fall back to the older one only if it is missing (404).

        In 2026 Spotify moved playlist contents to /playlists/{id}/items and library writes to
        /me/library; the old /playlists/{id}/tracks and /me/tracks answer 403 for this app.
        A 404 was refused before anything changed, so the fallback is safe for writes too.
        """
        try:
            return current()
        except SpotifyException as exc:
            if exc.http_status != 404 or _is_no_device(exc):
                raise
            try:
                return old()
            except SpotifyException:
                raise exc from None  # a real "not found" reads better than the old endpoint's 403

    def get_playlist_tracks(self, playlist_id: str, limit: int = 100) -> dict[str, Any]:
        """Get tracks from a playlist."""
        playlist = uris.to_id(playlist_id, "playlist", "playlist_id")
        limit = max(1, min(100, int(limit)))
        results = self._current_or_old(
            lambda: self._api(self.sp._get, f"playlists/{playlist}/items", limit=limit, additional_types="track"),
            lambda: self._api(self.sp.playlist_items, playlist, limit=limit, additional_types=("track",)),
        )
        return self._track_rows(results or {})

    def add_to_playlist(self, playlist_id: str, uris_: list[str]) -> dict[str, Any]:
        """Add tracks to a playlist."""
        playlist = uris.to_id(playlist_id, "playlist", "playlist_id")
        items = [uris.to_uri(u, expect=uris.PLAYABLE_ITEMS, bare_type="track", argument="uris")[1] for u in uris_]
        if not items:
            raise ToolError(BAD_REQUEST, "uris must list at least one track.")
        for start in range(0, len(items), 100):
            chunk = items[start:start + 100]
            self._current_or_old(
                lambda: self._api(self.sp._post, f"playlists/{playlist}/items", payload={"uris": chunk}),
                lambda: self._api(self.sp.playlist_add_items, playlist, chunk),
            )
        return {"success": True, "message": f"Added {len(items)} track(s) to playlist"}

    @staticmethod
    def _track_ids(track_ids: list[str]) -> list[str]:
        ids = [uris.to_id(t, "track", "track_ids") for t in track_ids]
        if not ids:
            raise ToolError(BAD_REQUEST, "track_ids must list at least one track.")
        return ids

    def _library(self, verb: str, ids: list[str]) -> None:
        current = self.sp._put if verb == "PUT" else self.sp._delete
        old = self.sp.current_user_saved_tracks_add if verb == "PUT" else self.sp.current_user_saved_tracks_delete
        for start in range(0, len(ids), 40):
            chunk = ids[start:start + 40]
            self._current_or_old(
                lambda: self._api(current, "me/library", uris=",".join(f"spotify:track:{i}" for i in chunk)),
                lambda: self._api(old, chunk),
            )

    def save_tracks(self, track_ids: list[str]) -> dict[str, Any]:
        """Save tracks to user's library (like/heart)."""
        ids = self._track_ids(track_ids)
        self._library("PUT", ids)
        return {"success": True, "message": f"Saved {len(ids)} track(s) to your library"}

    def remove_saved_tracks(self, track_ids: list[str]) -> dict[str, Any]:
        """Remove tracks from user's library (unlike/unheart)."""
        ids = self._track_ids(track_ids)
        self._library("DELETE", ids)
        return {"success": True, "message": f"Removed {len(ids)} track(s) from your library"}

    def get_saved_tracks(self, limit: int = 20) -> dict[str, Any]:
        """Get user's saved/liked tracks."""
        limit = max(1, min(50, int(limit)))
        return self._track_rows(self._api(self.sp.current_user_saved_tracks, limit=limit) or {})
