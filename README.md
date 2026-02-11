# Spotify MCP Server

Control Spotify playback through Claude using the Model Context Protocol (MCP).

**No Spotify Developer account needed!** Uses PKCE authentication - just install and authorize your Spotify account.

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
- `add_to_playlist` - Add tracks to a playlist

## Usage Examples

Once configured, you can ask Claude:

- "What song is currently playing?"
- "Pause the music"
- "Play the next track"
- "Search for 'Bohemian Rhapsody' and add it to the queue"
- "Set the volume to 50%"
- "Turn on shuffle"
- "Show my playlists"

## Advanced: Use Your Own Client ID

If you want to use your own Spotify app (optional):

1. Create an app at https://developer.spotify.com/dashboard
2. Set redirect URI to `http://127.0.0.1:8888/callback`
3. Set environment variable: `SPOTIPY_CLIENT_ID=your_client_id`

## Troubleshooting

### "No active device" error
Make sure Spotify is open on at least one device (phone, desktop app, web player).

### Authentication issues
Delete `~/.spotify-mcp-token` and run `spotify-mcp` again to re-authenticate.

### Token expired
The token auto-refreshes, but if issues persist, delete `~/.spotify-mcp-token` and re-auth.
