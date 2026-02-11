# Spotify MCP Server

Control Spotify playback through Claude using the Model Context Protocol (MCP).

## Setup

### 1. Create Spotify Developer Application

1. Go to https://developer.spotify.com/dashboard
2. Log in with your Spotify account
3. Click "Create App"
4. Fill in:
   - App name: "Claude Spotify MCP"
   - App description: "MCP server for Claude to control Spotify"
   - Redirect URI: `http://127.0.0.1:8888/callback`
5. Check the "Web API" checkbox
6. Accept terms and create
7. Copy the **Client ID** and **Client Secret**

### 2. Configure Credentials

Create a `.env` file in this directory (or `~/.spotify-mcp.env`):

```bash
cp .env.example .env
# Edit .env with your credentials
```

```
SPOTIPY_CLIENT_ID=your_client_id_here
SPOTIPY_CLIENT_SECRET=your_client_secret_here
SPOTIPY_REDIRECT_URI=http://127.0.0.1:8888/callback
```

### 3. Install

```bash
cd ~/spotify-mcp
pip install -e .
```

### 4. First Run - Authenticate

Run the server once to complete OAuth:

```bash
spotify-mcp
```

This will open your browser for Spotify authorization. After authorizing, the token is cached at `~/.spotify-mcp-token`.

### 5. Add to Claude Code

Add to your Claude Code MCP configuration (`~/.claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "spotify": {
      "command": "spotify-mcp"
    }
  }
}
```

Or with explicit path:

```json
{
  "mcpServers": {
    "spotify": {
      "command": "python",
      "args": ["-m", "spotify_mcp.server"],
      "cwd": "/home/YOUR_USER/spotify-mcp"
    }
  }
}
```

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

## Required Spotify Scopes

The app requests these permissions:
- `user-read-playback-state` - View current playback
- `user-modify-playback-state` - Control playback
- `user-read-currently-playing` - Get current track
- `playlist-read-private` - Access private playlists
- `playlist-modify-public` - Modify public playlists
- `playlist-modify-private` - Modify private playlists
- `user-library-read` - Access saved tracks
- `user-library-modify` - Save/remove tracks

## Troubleshooting

### "No active device" error
Make sure Spotify is open on at least one device (phone, desktop app, web player).

### Authentication issues
Delete `~/.spotify-mcp-token` and run `spotify-mcp` again to re-authenticate.

### Permission denied
Verify your app has the Web API checkbox enabled in the Spotify Developer Dashboard.
