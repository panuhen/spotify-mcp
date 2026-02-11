# Spotify MCP Server

Control Spotify playback through Claude using the Model Context Protocol (MCP).

Uses PKCE authentication - just install and authorize your Spotify account.

> **Note:** The bundled client ID works for playback control and reading data. For **write operations** (adding to playlists, saving to library), you'll need to [set up your own Spotify app](#setup-your-own-spotify-app).

## Quick Start

### 1. Install

```bash
git clone https://github.com/panuhen/spotify-mcp.git ~/spotify-mcp
cd ~/spotify-mcp
python -m venv .venv
.venv/bin/pip install -e .
```

### 2. First Run - Authenticate

Run once to authorize with your Spotify account:

```bash
~/spotify-mcp/.venv/bin/spotify-mcp
```

This opens your browser for Spotify login. After authorizing, the token is cached at `~/.spotify-mcp-token`.

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
- `play` - Resume or play specific track/album/playlist
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
- `search` - Search for tracks, albums, artists, playlists
- `add_to_queue` - Add track to queue
- `get_playlists` - List your playlists
- `get_playlist_tracks` - Get tracks from a playlist
- `add_to_playlist` - Add tracks to a playlist *(requires own app)*
- `save_tracks` - Save tracks to your library *(requires own app)*
- `remove_saved_tracks` - Remove tracks from library *(requires own app)*
- `get_saved_tracks` - Get your liked tracks

### Local Favorites
No Spotify API permissions needed - stored locally in `~/.spotify-mcp-favorites.json`:
- `favorite_current` - Add currently playing track to favorites
- `get_favorites` - List all favorited tracks
- `remove_favorite` - Remove a track from favorites
- `play_favorites` - Play random favorite or queue all favorites
- `clear_favorites` - Clear all favorites

## Usage Examples

Once configured, you can ask Claude:

- "What song is currently playing?"
- "Pause the music"
- "Play the next track"
- "Search for 'Bohemian Rhapsody' and add it to the queue"
- "Set the volume to 50%"
- "Turn on shuffle"
- "Show my playlists"

## Setup Your Own Spotify App

**Required for:** `add_to_playlist`, `save_tracks`, `remove_saved_tracks`

The bundled client ID is in Spotify's Development Mode, which restricts write operations. To use all features:

1. Create an app at https://developer.spotify.com/dashboard
2. In your app settings, add redirect URI: `http://127.0.0.1:8888/callback`
3. Copy your Client ID and add to `~/spotify-mcp/.env`:
   ```
   SPOTIPY_CLIENT_ID=your_client_id_here
   ```
4. Delete cached token and re-auth:
   ```bash
   rm ~/.spotify-mcp-token
   ```
5. Restart the MCP server

## Troubleshooting

### "No active device" error
Make sure Spotify is open on at least one device (phone, desktop app, web player).

### Authentication issues
Delete `~/.spotify-mcp-token` and run `spotify-mcp` again to re-authenticate.

### Token expired
The token auto-refreshes, but if issues persist, delete `~/.spotify-mcp-token` and re-auth.
