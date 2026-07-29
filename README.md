# Spotify MCP

A minimal [FastMCP](https://gofastmcp.com/) server for Spotify with Authorization Code + PKCE auth.

## Features

- Search Spotify tracks with `search_song`
- Control playback with `play`, `pause`, `skip_forward`, and `skip_backwards`
- Read currently playing content with `get_currently_playing`
- PKCE login flow with local loopback callback
- Tokens stored in the OS keyring
- Automatic access-token refresh

## Prerequisites

- Python 3.10+
- A Spotify Developer app: [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)

## Spotify App Setup

1. Create an app in the Spotify Developer Dashboard.
2. Add this redirect URI:
   - `http://127.0.0.1:8888/callback`
3. Do **not** use `http://localhost`.
4. Copy your Client ID.

## Local Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
copy .env.example .env
```

Edit `.env`:

```env
SPOTIFY_CLIENT_ID=your_client_id_here
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
```

## Login (PKCE)

Run once before using the MCP tools:

```bash
python -m spotify_mcp.auth login
```

This opens your browser, completes Spotify authorization, and stores tokens in your OS keyring.

By default, login requests the minimum scopes needed by the current tools:

- `user-read-currently-playing`
- `user-modify-playback-state`

If you logged in before playback tools were added, run login again so Spotify grants the new scopes.

If login times out waiting for the callback:

- Make sure the Spotify Developer Dashboard has the exact redirect URI `http://127.0.0.1:8888/callback`.
- Complete the authorization page in the browser before the timeout.
- Keep the terminal running until Spotify redirects back to `127.0.0.1`.
- If you need more time, run `python -m spotify_mcp.auth login --timeout 600`.

To remove stored tokens:

```bash
python -m spotify_mcp.auth logout
```

## Run the MCP Server

```bash
python -m spotify_mcp.server
```

Or:

```bash
spotify-mcp
```

## Cursor MCP Config

Add this to your Cursor MCP settings (`.cursor/mcp.json` or Cursor Settings > MCP):

```json
{
  "mcpServers": {
    "spotify": {
      "command": "C:\\Users\\vihoh\\Coding Projects\\Spotify-MCP\\.venv\\Scripts\\python.exe",
      "args": ["-m", "spotify_mcp.server"],
      "cwd": "C:\\Users\\vihoh\\Coding Projects\\Spotify-MCP"
    }
  }
}
```

Adjust the Python path if your virtual environment lives elsewhere.

## Tool: `search_song`

Searches Spotify tracks via `GET /v1/search` with `type=track`.

Parameters:

- `query` (required): search text
- `limit` (optional, default `5`, range `0-10`)
- `market` (optional): ISO country code
- `offset` (optional, default `0`, range `0-1000`)

Example prompt in Cursor:

```text
Use search_song to find "Blinding Lights" by The Weeknd.
```

## Playback Tools

Playback endpoints require a Spotify Premium account and an active Spotify device.

- `play(device_id: str | None = None)`: resume playback.
- `pause(device_id: str | None = None)`: pause playback.
- `skip_forward(device_id: str | None = None)`: skip to the next track.
- `skip_backwards(device_id: str | None = None)`: skip to the previous track.
- `get_currently_playing(market: str | None = None, additional_types: str | None = None)`: get the current track or episode.

Example prompts in Cursor:

```text
Use get_currently_playing to tell me what's playing.
```

```text
Pause Spotify.
```

## Notes

- Search uses PKCE user auth even though catalog search is public data. This keeps auth ready for future playlist and library tools.
- Playback uses Spotify's documented Player endpoints and requests only `user-read-currently-playing` and `user-modify-playback-state`.
- If auth expires or is revoked, run `python -m spotify_mcp.auth login` again.
- Spotify content is attributed in tool responses and not cached beyond immediate use.

## Project Layout

```text
src/spotify_mcp/
  auth.py           # PKCE login, token storage, refresh helpers
  spotify_client.py # Spotify API requests and error handling
  server.py         # FastMCP server and tools
```

## License

Use in compliance with the [Spotify Developer Terms](https://developer.spotify.com/terms).
