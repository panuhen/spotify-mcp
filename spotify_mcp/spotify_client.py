"""Spotify API wrapper with error handling."""

from typing import Any

import spotipy
from spotipy.exceptions import SpotifyException


class SpotifyClient:
    """Wrapper around spotipy with consistent error handling."""

    def __init__(self, spotify: spotipy.Spotify):
        self.sp = spotify

    def _handle_error(self, e: SpotifyException) -> dict[str, Any]:
        """Convert Spotify errors to user-friendly messages."""
        error_messages = {
            401: "Authentication failed. Please re-authenticate.",
            403: "Permission denied. Check app scopes.",
            404: "Resource not found.",
            429: "Rate limited. Please wait and try again.",
        }
        msg = error_messages.get(e.http_status, str(e))
        return {"error": msg, "status": e.http_status, "details": str(e)}

    # Playback Control

    def play(
        self,
        uri: str | None = None,
        context_uri: str | None = None,
        device_id: str | None = None,
        position_ms: int = 0,
    ) -> dict[str, Any]:
        """Resume playback or play specific content.

        Args:
            uri: Spotify URI of track to play
            context_uri: Spotify URI of album/playlist to play
            device_id: Target device ID
            position_ms: Position to start from
        """
        try:
            kwargs: dict[str, Any] = {"device_id": device_id}

            if uri:
                kwargs["uris"] = [uri]
            elif context_uri:
                kwargs["context_uri"] = context_uri
                if position_ms:
                    kwargs["position_ms"] = position_ms

            self.sp.start_playback(**kwargs)
            return {"success": True, "message": "Playback started"}
        except SpotifyException as e:
            return self._handle_error(e)

    def pause(self, device_id: str | None = None) -> dict[str, Any]:
        """Pause playback."""
        try:
            self.sp.pause_playback(device_id=device_id)
            return {"success": True, "message": "Playback paused"}
        except SpotifyException as e:
            return self._handle_error(e)

    def next_track(self, device_id: str | None = None) -> dict[str, Any]:
        """Skip to next track."""
        try:
            self.sp.next_track(device_id=device_id)
            return {"success": True, "message": "Skipped to next track"}
        except SpotifyException as e:
            return self._handle_error(e)

    def previous_track(self, device_id: str | None = None) -> dict[str, Any]:
        """Go to previous track."""
        try:
            self.sp.previous_track(device_id=device_id)
            return {"success": True, "message": "Went to previous track"}
        except SpotifyException as e:
            return self._handle_error(e)

    def seek(self, position_ms: int, device_id: str | None = None) -> dict[str, Any]:
        """Seek to position in current track."""
        try:
            self.sp.seek_track(position_ms, device_id=device_id)
            return {"success": True, "message": f"Seeked to {position_ms}ms"}
        except SpotifyException as e:
            return self._handle_error(e)

    def set_volume(self, volume: int, device_id: str | None = None) -> dict[str, Any]:
        """Set playback volume (0-100)."""
        try:
            volume = max(0, min(100, volume))
            self.sp.volume(volume, device_id=device_id)
            return {"success": True, "message": f"Volume set to {volume}%"}
        except SpotifyException as e:
            return self._handle_error(e)

    def shuffle(self, state: bool, device_id: str | None = None) -> dict[str, Any]:
        """Toggle shuffle mode."""
        try:
            self.sp.shuffle(state, device_id=device_id)
            return {"success": True, "message": f"Shuffle {'on' if state else 'off'}"}
        except SpotifyException as e:
            return self._handle_error(e)

    def repeat(self, state: str, device_id: str | None = None) -> dict[str, Any]:
        """Set repeat mode (off/track/context)."""
        try:
            if state not in ("off", "track", "context"):
                return {"error": "State must be 'off', 'track', or 'context'"}
            self.sp.repeat(state, device_id=device_id)
            return {"success": True, "message": f"Repeat mode set to {state}"}
        except SpotifyException as e:
            return self._handle_error(e)

    # Information

    def get_current_track(self) -> dict[str, Any]:
        """Get currently playing track info."""
        try:
            current = self.sp.current_user_playing_track()
            if not current or not current.get("item"):
                return {"playing": False, "message": "Nothing currently playing"}

            track = current["item"]
            return {
                "playing": current.get("is_playing", False),
                "track": {
                    "name": track["name"],
                    "uri": track["uri"],
                    "artists": [a["name"] for a in track["artists"]],
                    "album": track["album"]["name"],
                    "duration_ms": track["duration_ms"],
                    "progress_ms": current.get("progress_ms", 0),
                },
            }
        except SpotifyException as e:
            return self._handle_error(e)

    def get_playback_state(self) -> dict[str, Any]:
        """Get full playback state."""
        try:
            state = self.sp.current_playback()
            if not state:
                return {"active": False, "message": "No active playback"}

            result: dict[str, Any] = {
                "active": True,
                "is_playing": state.get("is_playing", False),
                "shuffle": state.get("shuffle_state", False),
                "repeat": state.get("repeat_state", "off"),
                "progress_ms": state.get("progress_ms", 0),
            }

            if state.get("device"):
                result["device"] = {
                    "id": state["device"]["id"],
                    "name": state["device"]["name"],
                    "type": state["device"]["type"],
                    "volume": state["device"]["volume_percent"],
                }

            if state.get("item"):
                track = state["item"]
                result["track"] = {
                    "name": track["name"],
                    "uri": track["uri"],
                    "artists": [a["name"] for a in track["artists"]],
                    "album": track["album"]["name"],
                    "duration_ms": track["duration_ms"],
                }

            return result
        except SpotifyException as e:
            return self._handle_error(e)

    def get_queue(self) -> dict[str, Any]:
        """Get upcoming tracks in queue."""
        try:
            queue = self.sp.queue()
            if not queue:
                return {"queue": []}

            tracks = []
            for item in queue.get("queue", [])[:20]:  # Limit to 20
                tracks.append({
                    "name": item["name"],
                    "uri": item["uri"],
                    "artists": [a["name"] for a in item["artists"]],
                })

            result: dict[str, Any] = {"queue": tracks}

            if queue.get("currently_playing"):
                current = queue["currently_playing"]
                result["currently_playing"] = {
                    "name": current["name"],
                    "uri": current["uri"],
                    "artists": [a["name"] for a in current["artists"]],
                }

            return result
        except SpotifyException as e:
            return self._handle_error(e)

    def get_devices(self) -> dict[str, Any]:
        """List available Spotify devices."""
        try:
            devices = self.sp.devices()
            return {
                "devices": [
                    {
                        "id": d["id"],
                        "name": d["name"],
                        "type": d["type"],
                        "is_active": d["is_active"],
                        "volume": d["volume_percent"],
                    }
                    for d in devices.get("devices", [])
                ]
            }
        except SpotifyException as e:
            return self._handle_error(e)

    # Search & Library

    def search(
        self,
        query: str,
        types: list[str] | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Search for tracks, albums, artists, or playlists."""
        try:
            if types is None:
                types = ["track"]
            valid_types = {"track", "album", "artist", "playlist"}
            types = [t for t in types if t in valid_types]
            if not types:
                types = ["track"]

            results = self.sp.search(q=query, type=",".join(types), limit=limit)

            output: dict[str, Any] = {}

            if "tracks" in results:
                output["tracks"] = [
                    {
                        "name": t["name"],
                        "uri": t["uri"],
                        "artists": [a["name"] for a in t["artists"]],
                        "album": t["album"]["name"],
                    }
                    for t in results["tracks"]["items"]
                ]

            if "albums" in results:
                output["albums"] = [
                    {
                        "name": a["name"],
                        "uri": a["uri"],
                        "artists": [art["name"] for art in a["artists"]],
                    }
                    for a in results["albums"]["items"]
                ]

            if "artists" in results:
                output["artists"] = [
                    {
                        "name": a["name"],
                        "uri": a["uri"],
                        "genres": a.get("genres", []),
                    }
                    for a in results["artists"]["items"]
                ]

            if "playlists" in results:
                output["playlists"] = [
                    {
                        "name": p["name"],
                        "uri": p["uri"],
                        "owner": p["owner"]["display_name"],
                        "tracks": p.get("tracks", {}).get("total", 0) if p.get("tracks") else 0,
                    }
                    for p in results["playlists"]["items"]
                    if p is not None
                ]

            return output
        except SpotifyException as e:
            return self._handle_error(e)

    def add_to_queue(self, uri: str, device_id: str | None = None) -> dict[str, Any]:
        """Add track to playback queue."""
        try:
            self.sp.add_to_queue(uri, device_id=device_id)
            return {"success": True, "message": "Added to queue"}
        except SpotifyException as e:
            return self._handle_error(e)

    def get_playlists(self, limit: int = 50) -> dict[str, Any]:
        """List user's playlists."""
        try:
            playlists = self.sp.current_user_playlists(limit=limit)
            return {
                "playlists": [
                    {
                        "name": p["name"],
                        "uri": p["uri"],
                        "id": p["id"],
                        "tracks": p.get("tracks", {}).get("total", 0) if p.get("tracks") else 0,
                        "owner": p["owner"]["display_name"],
                    }
                    for p in playlists.get("items", [])
                    if p is not None
                ]
            }
        except SpotifyException as e:
            return self._handle_error(e)

    def get_playlist_tracks(
        self, playlist_id: str, limit: int = 100
    ) -> dict[str, Any]:
        """Get tracks from a playlist."""
        try:
            results = self.sp.playlist_tracks(playlist_id, limit=limit)
            tracks = []
            for item in results.get("items", []):
                track = item.get("track")
                if track:
                    tracks.append({
                        "name": track["name"],
                        "uri": track["uri"],
                        "artists": [a["name"] for a in track["artists"]],
                        "album": track["album"]["name"],
                        "added_at": item.get("added_at"),
                    })
            return {"tracks": tracks, "total": results.get("total", len(tracks))}
        except SpotifyException as e:
            return self._handle_error(e)

    def add_to_playlist(
        self, playlist_id: str, uris: list[str]
    ) -> dict[str, Any]:
        """Add tracks to a playlist."""
        try:
            self.sp.playlist_add_items(playlist_id, uris)
            return {
                "success": True,
                "message": f"Added {len(uris)} track(s) to playlist",
            }
        except SpotifyException as e:
            return self._handle_error(e)

    def save_tracks(self, track_ids: list[str]) -> dict[str, Any]:
        """Save tracks to user's library (like/heart)."""
        try:
            # Extract track IDs from URIs if needed
            ids = []
            for t in track_ids:
                if t.startswith("spotify:track:"):
                    ids.append(t.split(":")[-1])
                else:
                    ids.append(t)
            self.sp.current_user_saved_tracks_add(ids)
            return {
                "success": True,
                "message": f"Saved {len(ids)} track(s) to your library",
            }
        except SpotifyException as e:
            return self._handle_error(e)

    def remove_saved_tracks(self, track_ids: list[str]) -> dict[str, Any]:
        """Remove tracks from user's library (unlike/unheart)."""
        try:
            ids = []
            for t in track_ids:
                if t.startswith("spotify:track:"):
                    ids.append(t.split(":")[-1])
                else:
                    ids.append(t)
            self.sp.current_user_saved_tracks_delete(ids)
            return {
                "success": True,
                "message": f"Removed {len(ids)} track(s) from your library",
            }
        except SpotifyException as e:
            return self._handle_error(e)

    def get_saved_tracks(self, limit: int = 20) -> dict[str, Any]:
        """Get user's saved/liked tracks."""
        try:
            results = self.sp.current_user_saved_tracks(limit=limit)
            tracks = []
            for item in results.get("items", []):
                track = item.get("track")
                if track:
                    tracks.append({
                        "name": track["name"],
                        "uri": track["uri"],
                        "artists": [a["name"] for a in track["artists"]],
                        "album": track["album"]["name"],
                        "added_at": item.get("added_at"),
                    })
            return {"tracks": tracks, "total": results.get("total", len(tracks))}
        except SpotifyException as e:
            return self._handle_error(e)
