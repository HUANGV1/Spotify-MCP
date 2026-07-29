"""FastMCP server exposing Spotify tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from spotify_mcp.auth import SpotifyAuthError
from spotify_mcp.spotify_client import (
    SpotifyAPIError,
    format_currently_playing,
    format_track_results,
    get_currently_playing as get_currently_playing_request,
    pause_playback,
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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
