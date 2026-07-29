"""FastMCP server exposing Spotify tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from spotify_mcp.auth import SpotifyAuthError
from spotify_mcp.spotify_client import (
    SpotifyAPIError,
    add_items_to_playlist,
    create_playlist as create_playlist_request,
    format_currently_playing,
    format_playlist,
    format_track_results,
    get_currently_playing as get_currently_playing_request,
    pause_playback,
    resolve_song_queries,
    search_tracks,
    skip_to_next,
    skip_to_previous,
    start_playback,
)

mcp = FastMCP("Spotify MCP")


def _error_response(exc: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "error": str(exc),
    }


def _is_restriction_violation(exc: SpotifyAPIError) -> bool:
    return exc.status_code == 403 and "restriction violated" in str(exc).lower()


@mcp.tool
def search_song(
    query: str,
    limit: int = 5,
    market: str | None = None,
    offset: int = 0,
) -> dict[str, Any]:
    """Search Spotify for tracks matching a query string.

    Args:
        query: Search text, such as a song title, artist, or combined phrase.
        limit: Maximum number of track results to return (0-10).
        market: Optional ISO 3166-1 alpha-2 country code.
        offset: Result offset for pagination (0-1000).

    Returns:
        A dictionary with track results and pagination metadata.
    """
    try:
        response = search_tracks(
            query=query,
            limit=limit,
            offset=offset,
            market=market,
        )
    except (SpotifyAuthError, SpotifyAPIError, ValueError) as exc:
        return _error_response(exc) | {
            "tracks": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
        }

    tracks_section = response.get("tracks", {})
    return {
        "ok": True,
        "tracks": format_track_results(response),
        "total": tracks_section.get("total", 0),
        "limit": tracks_section.get("limit", limit),
        "offset": tracks_section.get("offset", offset),
        "attribution": "Data provided by Spotify",
    }


@mcp.tool
def play(device_id: str | None = None) -> dict[str, Any]:
    """Resume playback on the active Spotify device or an optional device ID."""
    try:
        start_playback(device_id=device_id)
        return {"ok": True, "message": "Playback started."}
    except SpotifyAPIError as exc:
        if _is_restriction_violation(exc):
            try:
                playback = get_currently_playing_request()
                if playback.get("is_playing"):
                    return {
                        "ok": True,
                        "message": "Playback started.",
                        "warning": str(exc),
                        "currently_playing": format_currently_playing(playback),
                    }
            except (SpotifyAuthError, SpotifyAPIError):
                pass
        return _error_response(exc)
    except SpotifyAuthError as exc:
        return _error_response(exc)


@mcp.tool
def pause(device_id: str | None = None) -> dict[str, Any]:
    """Pause playback on the active Spotify device or an optional device ID."""
    try:
        pause_playback(device_id=device_id)
        return {"ok": True, "message": "Playback paused."}
    except (SpotifyAuthError, SpotifyAPIError) as exc:
        return _error_response(exc)


@mcp.tool
def skip_forward(device_id: str | None = None) -> dict[str, Any]:
    """Skip to the next track on the active Spotify device or an optional device ID."""
    try:
        skip_to_next(device_id=device_id)
        return {"ok": True, "message": "Skipped to next track."}
    except (SpotifyAuthError, SpotifyAPIError) as exc:
        return _error_response(exc)


@mcp.tool
def skip_backwards(device_id: str | None = None) -> dict[str, Any]:
    """Skip to the previous track on the active Spotify device or an optional device ID."""
    try:
        skip_to_previous(device_id=device_id)
        return {"ok": True, "message": "Skipped to previous track."}
    except (SpotifyAuthError, SpotifyAPIError) as exc:
        return _error_response(exc)


@mcp.tool
def get_currently_playing(
    market: str | None = None,
    additional_types: str | None = None,
) -> dict[str, Any]:
    """Get the track or episode currently playing on the user's Spotify account."""
    try:
        response = get_currently_playing_request(
            market=market,
            additional_types=additional_types,
        )
        return {"ok": True} | format_currently_playing(response)
    except (SpotifyAuthError, SpotifyAPIError) as exc:
        return _error_response(exc)


@mcp.tool
def create_playlist(
    name: str,
    description: str | None = None,
    public: bool = False,
    collaborative: bool = False,
) -> dict[str, Any]:
    """Create an empty playlist for the current Spotify user."""
    try:
        response = create_playlist_request(
            name=name,
            description=description,
            public=public,
            collaborative=collaborative,
        )
        return {"ok": True, "playlist": format_playlist(response)}
    except (SpotifyAuthError, SpotifyAPIError, ValueError) as exc:
        return _error_response(exc)


@mcp.tool
def add_songs_to_playlist(
    playlist_id: str,
    song_queries: list[str],
    market: str | None = None,
) -> dict[str, Any]:
    """Search for songs and add the best match for each query to a playlist."""
    try:
        matched, unresolved = resolve_song_queries(song_queries, market=market)
        uris = [item["uri"] for item in matched]
        add_items_to_playlist(playlist_id, uris)
        return {
            "ok": True,
            "playlist_id": playlist_id,
            "added_count": len(matched),
            "added_tracks": [item["track"] for item in matched],
            "unresolved_queries": unresolved,
            "attribution": "Data provided by Spotify",
        }
    except (SpotifyAuthError, SpotifyAPIError, ValueError) as exc:
        return _error_response(exc)


@mcp.tool
def create_vibe_playlist(
    name: str,
    vibe: str,
    song_queries: list[str],
    description: str | None = None,
    public: bool = False,
    market: str | None = None,
) -> dict[str, Any]:
    """Create a playlist from a vibe prompt by searching and adding matched songs.

    The LLM should provide song_queries that fit the requested vibe. This tool
    creates the playlist, resolves each query to a Spotify track, and adds them.
    """
    try:
        playlist_description = description or f"Playlist inspired by: {vibe}"
        playlist_response = create_playlist_request(
            name=name,
            description=playlist_description,
            public=public,
        )
        playlist = format_playlist(playlist_response)
        playlist_id = playlist.get("id")
        if not playlist_id:
            raise SpotifyAPIError(500, "Spotify did not return a playlist ID.")

        matched, unresolved = resolve_song_queries(song_queries, market=market)
        uris = [item["uri"] for item in matched]
        add_items_to_playlist(playlist_id, uris)

        return {
            "ok": True,
            "vibe": vibe,
            "playlist": playlist,
            "added_count": len(matched),
            "added_tracks": [item["track"] for item in matched],
            "unresolved_queries": unresolved,
            "attribution": "Data provided by Spotify",
        }
    except (SpotifyAuthError, SpotifyAPIError, ValueError) as exc:
        return _error_response(exc)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
