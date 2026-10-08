# Spotify MCP Server

Control Spotify playback through Claude using the Model Context Protocol (MCP).

Uses PKCE authentication - just install and authorize your Spotify account.

## Quick Start

### 1. Install

```bash
git clone https://github.com/panuhen/spotify-mcp.git ~/spotify-mcp
cd ~/spotify-mcp
python -m venv .venv
.venv/bin/pip install -e .
```

### 2. First Run - Authenticate

Run once in a terminal to authorize with your Spotify account:

```bash
~/spotify-mcp/.venv/bin/spotify-mcp --login
```

This opens your browser for Spotify login. After authorizing, the token is cached at `~/.spotify-mcp-token`
and refreshed automatically from then on. The server itself never opens a browser: if a tool call
finds no usable token it returns an `auth` error that tells you to run `--login`.

### 3. Add to Claude Code

Add to `~/.mcp.json`:

```json
{
  "mcpServers": {
    "spotify": {
      "command": "/home/YOUR_USER/spotify-mcp/.venv/bin/spotify-mcp"
    }
  }
}
```

Then enable in `~/.claude/settings.local.json`:

```json
{
  "enabledMcpjsonServers": ["spotify"]
}
```

Restart Claude Code and you're ready!

## Available Tools

### Playback Control
- `play` - Resume, or play a track (`uri`), an album/playlist/artist (`context_uri`), or one of
  your playlists by name (`playlist`)
- `play_liked` - Play your Liked Songs, shuffled unless `shuffle: false` (see below)
- `pause` - Pause playback
- `next` - Skip to next track
- `previous` - Go to previous track
- `seek` - Seek to position in track
- `set_volume` - Set volume (0-100)
- `shuffle` - Toggle shuffle mode
- `repeat` - Set repeat mode (off/track/context)

### Information
- `get_current_track` - Get currently playing track info
- `get_playback_state` - Get full playback state
- `get_queue` - Get upcoming tracks
- `get_devices` - List available devices

### Search & Library
- `search` - Search for tracks, albums, artists, playlists (at most 10 per type; Spotify rejects more)
- `add_to_queue` - Add track to queue
- `get_playlists` - List your playlists
- `get_playlist_tracks` - Get tracks from a playlist (Spotify refuses some playlists the user does not own: `forbidden`)
- `add_to_playlist` - Add tracks to a playlist by ID
- `save_tracks` - Save tracks to Liked Songs
- `remove_saved_tracks` - Remove tracks from Liked Songs
- `get_saved_tracks` - Get your liked tracks

### Library & Playlists by Name
Made for voice and small models: one call, the server finds the playing track and the playlist.
- `like_current` - Save the playing track to Liked Songs (`already_liked` if it was there)
- `add_current_to_playlist` - Add the playing track to one of your playlists (`playlist`: name,
  ID, URI or link). Reads the playlist first and skips the add if the track is already there.
  `create_if_missing: true` creates a private playlist when no name matches.
- `find_playlist` - Your 1-5 best matching playlists for a name: name, id, uri, owned, track count
- `remove_from_playlist` - Remove a track (`"current"`, a URI or a link) from one of your playlists
- `create_playlist` - Create a playlist, private unless `public: true`. Refuses a name you already
  own unless `force: true`.

The write tools (`like_current`, `add_current_to_playlist`, `remove_from_playlist`,
`create_playlist`) say so in their descriptions and carry MCP annotations
(`remove_from_playlist` is marked destructive).

Results are short, e.g. `{"liked": "Blue Monday – New Order"}`,
`{"added": "Blue Monday – New Order", "playlist": "Running"}`, `{"already_there": …}`,
`{"removed": …}`, `{"not_in_playlist": …}`, `{"created": "Gym", "id": …, "uri": …}`.

Spotify moved playlist contents to `/playlists/{id}/items`, library writes to `/me/library` and
playlist creation to `/me/playlists` in 2026; the old endpoints answer 403. The server uses the
new ones and falls back to the old ones only when the new one is missing (404).

### Favourites are Liked Songs
"Favourites" are Liked Songs: `like_current` adds the playing track, `play_liked` plays them,
`get_saved_tracks` lists them. (An earlier local favorites file, `~/.spotify-mcp-favorites.json`,
and its five tools were removed; the server no longer reads that file.)

`play_liked` starts up to 50 Liked Songs as a track list: by default a random page of 50, shuffled;
with `shuffle: false` the 50 newest in order. Spotify documents only albums, artists and playlists
as playback contexts, and has refused Liked Songs' own URI (`spotify:user:<id>:collection`) with
"Non supported context uri" and `PLAYER_COMMAND_REJECTED`, so the server reads the saved tracks
and plays them by URI. It is one play call: nothing is added to the queue.

## Playlist names

`add_current_to_playlist`, `remove_from_playlist`, `find_playlist` and `play`'s `playlist` take a
name and find the playlist in your library. The first rule that matches anything decides:

1. the same name, ignoring case;
2. the same after removing accents, punctuation, extra spaces and filler words ("my", "playlist");
3. every word you said starts a word of the name ("run" finds "Running Mix"), or every word of
   the name is in what you said;
4. a fuzzy match (Python's `difflib` ratio of at least 0.8, on the text and on a rough
   sound-alike spelling), so speech-to-text errors still land: "shrance" finds "Schranz".

If one rule finds several playlists, the call fails with `bad_request` and lists up to five names
so the model can ask which one. Nothing found gives `not_found` with the closest names.
The write tools only look at playlists you own or that are collaborative; a name that only
matches someone else's playlist gives `forbidden`. Your playlist list is cached for 60 seconds
and re-read after every write.

## URIs and links

Every argument that takes a Spotify URI also takes an `open.spotify.com` link (with or without
`https://`, an `intl-xx/` prefix or a `?si=` query) and the old `spotify:user:<name>:playlist:<id>`
form. Playlist and track ID arguments also take a bare ID.

`play` puts each URI where Spotify needs it, whichever argument it came in:

| You pass | Sent to Spotify |
|---|---|
| a track or episode, in `uri` or `context_uri` | `uris: [track]` |
| an album, playlist, artist or show, in `uri` or `context_uri` | `context_uri` |
| a track in `uri` and an album or playlist in `context_uri` | the album/playlist, starting at that track |
| nothing | resume |

So a track passed as `context_uri` no longer fails with 400 "Non supported context uri".
`add_to_queue` takes tracks and episodes only and says so if given an album.

## Errors

Every failed call returns one JSON object, with the MCP `isError` flag set:

```json
{"error": "No Spotify device is active. Open Spotify on a computer or phone, or name a device_id from get_devices.",
 "code": "no_active_device", "status": 404, "details": "Player command failed: No active device found"}
```

- `error`: one plain sentence, fit to show or speak. Never a traceback, URL or token.
- `code`: a stable category to branch on (below).
- `status`: the HTTP status, when Spotify answered with one.
- `details`: Spotify's own message without the URL, when there is one.

The keys are always a subset of `error`, `code`, `status`, `details`, `message`.

| code | Meaning |
|---|---|
| `no_active_device` | Nothing is playing anywhere and no device was given. |
| `not_found` | No such track/playlist/etc., no playlist matches the name, nothing is playing (`like_current`, `add_current_to_playlist`), or Liked Songs is empty (`play_liked`). |
| `rate_limited` | Spotify sent 429 with a longer wait than the server will sit through; the message says when to retry. |
| `network` | Spotify could not be reached, or did not answer within the call's time budget. |
| `auth` | Not signed in, or the sign-in expired or was revoked. Run `spotify-mcp --login`. |
| `premium_required` | The command needs Spotify Premium. |
| `restricted` | Spotify refused a player command ("Restriction violated"): usually already playing/paused, or the device does not allow it. |
| `forbidden` | Spotify does not let this app do that (403), or a write to a playlist the user does not own. |
| `bad_request` | A missing or invalid argument, several playlists match a name, or Spotify rejected the request (400). |
| `unavailable` | Spotify answered with a 5xx. |
| `internal` | A bug in this server; details go to its stderr log. |

Arguments are checked by the server rather than by the MCP SDK, so a missing argument comes back
in this shape too ("Missing argument: position_ms."). Near misses are accepted: `"50"` or `"50%"`
for a number, `"on"`/`"off"` for a boolean, `"all"`/`"one"` for repeat, a single string for a list.

## Reliability

- **Stale connections.** Spotify's edge closes an idle keep-alive connection after about ten
  minutes, and a request sent on it at that moment fails at once with `RemoteDisconnected`.
  The server closes pooled connections that have been idle for 120 seconds (time asleep counts),
  so the next call opens a fresh one, and if a connection still fails it retries:
  - before anything was sent (refused, DNS, connect timeout): any request, up to twice;
  - after the request may have been sent (reset, closed without answer, read timeout): only
    GET, PUT and DELETE, which set state and are safe to repeat (play, pause, volume, shuffle,
    repeat, seek, transfer, play_liked, save/remove tracks, remove from a playlist). POST is not repeated,
    because `next`, `previous`, `add_to_queue`, `add_to_playlist`, `add_current_to_playlist`
    and `create_playlist` would act twice;
  - the token refresh (a POST) is retried, because the next call would retry it anyway.
- **IPv4 first.** New connections try IPv4 addresses first and give each address 1.5 seconds,
  so a broken IPv6 route costs nothing instead of a full timeout per connection.
- **Rate limits.** A 429 with `Retry-After` of up to 5 seconds is waited out and retried (twice at
  most). A longer wait returns `rate_limited` at once. A 5xx on GET/PUT/DELETE is retried up to twice.
- **Bounded calls.** Every tool call has a 15-second budget shared by all of its requests;
  connect timeout 3 s and read timeout 8 s per attempt, never past the budget. The token refresh
  has the same limits (spotipy's own has no timeout).
- **401.** A 401 on a token that should still be valid forces one refresh and one retry.
- **Shared token cache.** Several server processes (for example one per client) can share
  `~/.spotify-mcp-token`: it is written atomically in spotipy's format, and a read that catches
  another process mid-write is retried.

## Settings

Environment variables, all optional:

| Variable | Default | Effect |
|---|---|---|
| `SPOTIFY_MCP_AUTO_DEVICE` | `off` | `auto`: when `play` finds no active device, start on the only available device, or on the device this server last saw active. With several devices and no known last one it lists them in the error instead of guessing. |
| `SPOTIFY_MCP_COMPACT` | off | `1`: smaller results for small context windows (search returns 5 per type by default, no artist genres, no album or `added_at` in playlist and Liked Songs listings, queue capped at 10). |
| `SPOTIFY_MCP_CALL_TIMEOUT` | `15` | Seconds one tool call may take in total. |
| `SPOTIFY_MCP_CONNECT_TIMEOUT` | `3` | Connect timeout per attempt, seconds. |
| `SPOTIFY_MCP_READ_TIMEOUT` | `8` | Read timeout per attempt, seconds. |
| `SPOTIFY_MCP_IDLE_RESET` | `120` | Seconds idle after which pooled connections are dropped. |
| `SPOTIFY_MCP_MAX_RETRY_AFTER` | `5` | Longest `Retry-After` the server waits out, seconds. |

Results are compact JSON (no indentation) in every mode.

## Usage Examples

Once configured, you can ask Claude:

- "What song is currently playing?"
- "Pause the music"
- "Play the next track"
- "Search for 'Bohemian Rhapsody' and add it to the queue"
- "Set the volume to 50%"
- "Turn on shuffle"
- "Show my playlists"
- "I like this song" or "add this to my favourites" (`like_current`)
- "Play my favourites" (`play_liked`)
- "Add this to my running playlist" (`add_current_to_playlist`)
- "Play my Schranz playlist" (`play` with `playlist`)

## Setup Your Own Spotify App

Optional. Every tool, writes included, works with the bundled client ID. To use your own app instead:

1. Create an app at https://developer.spotify.com/dashboard
2. In your app settings, add redirect URI: `http://127.0.0.1:8888/callback`
3. Copy your Client ID and add to `~/spotify-mcp/.env`:
   ```
   SPOTIPY_CLIENT_ID=your_client_id_here
   ```
4. Delete the cached token and sign in again:
   ```bash
   rm ~/.spotify-mcp-token
   ~/spotify-mcp/.venv/bin/spotify-mcp --login
   ```
5. Restart the MCP server

## Troubleshooting

### "No active device" error
Make sure Spotify is open on at least one device (phone, desktop app, web player), or set
`SPOTIFY_MCP_AUTO_DEVICE=auto` to let `play` start on the only available device.

### Authentication issues (`"code": "auth"`)
Run `spotify-mcp --login` in a terminal. If that does not help, delete `~/.spotify-mcp-token` first.

### Token expired
The token auto-refreshes. If Spotify revokes it, calls return an `auth` error; run `spotify-mcp --login`.

### Logs
The server logs one line per failed call and per retry to stderr, never tokens or request bodies.

## Development

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

The tests run offline: a throwaway `HOME`, no DNS for anything but localhost, and Spotify's
answers scripted at the HTTP adapter or served by a local keep-alive server.
