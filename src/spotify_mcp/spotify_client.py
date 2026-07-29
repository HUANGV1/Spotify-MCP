"""Spotify Web API client with token refresh and rate-limit handling."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from spotify_mcp.auth import (
    SpotifyAuthError,
    ensure_valid_access_token,
    refresh_stored_access_token,
)

SPOTIFY_API_BASE = "https://api.spotify.com/v1"
MAX_RETRIES = 3


class SpotifyAPIError(Exception):
    """Raised when a Spotify API request fails."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"Spotify API error ({status_code}): {message}")


def _parse_error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
        if isinstance(body, dict) and "error" in body:
            error = body["error"]
            if isinstance(error, dict):
                return error.get("message") or json.dumps(error)
            return str(error)
        return response.text
    except json.JSONDecodeError:
        return response.text or "Unknown error"


def _request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    retry_auth: bool = True,
) -> dict[str, Any]:
    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES):
        try:
            access_token = ensure_valid_access_token()
        except SpotifyAuthError:
            raise

        headers = {"Authorization": f"Bearer {access_token}"}
        url = f"{SPOTIFY_API_BASE}{path}"

        with httpx.Client(timeout=30.0) as client:
            response = client.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
            )

        if response.status_code == 401 and retry_auth:
            refresh_stored_access_token()
            retry_auth = False
            continue

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            wait_seconds = float(retry_after) if retry_after else min(2**attempt, 8)
            time.sleep(wait_seconds)
            last_error = SpotifyAPIError(429, _parse_error_message(response))
            continue

        if response.status_code >= 400:
            raise SpotifyAPIError(response.status_code, _parse_error_message(response))

        if response.status_code == 204 or not response.text.strip():
            return {}

        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise SpotifyAPIError(
                response.status_code,
                f"Spotify returned a non-JSON response: {response.text!r}",
            ) from exc

    if last_error:
        raise last_error
    raise SpotifyAPIError(429, "Rate limited by Spotify after retries.")


def search_tracks(
    query: str,
    *,
    limit: int = 5,
    offset: int = 0,
    market: str | None = None,
) -> dict[str, Any]:
    if limit < 0 or limit > 10:
        raise ValueError("limit must be between 0 and 10.")
    if offset < 0 or offset > 1000:
        raise ValueError("offset must be between 0 and 1000.")

    params: dict[str, Any] = {
        "q": query,
        "type": "track",
        "limit": limit,
        "offset": offset,
    }
    if market:
        params["market"] = market

    return _request("GET", "/search", params=params)


def get_currently_playing(
    *,
    market: str | None = None,
    additional_types: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if market:
        params["market"] = market
    if additional_types:
        params["additional_types"] = additional_types

    return _request("GET", "/me/player/currently-playing", params=params)


def start_playback(device_id: str | None = None) -> dict[str, Any]:
    params = {"device_id": device_id} if device_id else None
    return _request("PUT", "/me/player/play", params=params)


def pause_playback(device_id: str | None = None) -> dict[str, Any]:
    params = {"device_id": device_id} if device_id else None
    return _request("PUT", "/me/player/pause", params=params)


def skip_to_next(device_id: str | None = None) -> dict[str, Any]:
    params = {"device_id": device_id} if device_id else None
    return _request("POST", "/me/player/next", params=params)


def skip_to_previous(device_id: str | None = None) -> dict[str, Any]:
    params = {"device_id": device_id} if device_id else None
    return _request("POST", "/me/player/previous", params=params)


def format_track_results(search_response: dict[str, Any]) -> list[dict[str, Any]]:
    tracks = search_response.get("tracks", {})
    items = tracks.get("items", [])
    formatted: list[dict[str, Any]] = []

    for track in items:
        if not track:
            continue
        artists = [artist.get("name") for artist in track.get("artists", []) if artist.get("name")]
        album = track.get("album") or {}
        formatted.append(
            {
                "id": track.get("id"),
                "name": track.get("name"),
                "artists": artists,
                "album": album.get("name"),
                "uri": track.get("uri"),
                "url": (track.get("external_urls") or {}).get("spotify"),
                "duration_ms": track.get("duration_ms"),
                "explicit": track.get("explicit"),
                "popularity": track.get("popularity"),
            }
        )

    return formatted


def format_currently_playing(playback_response: dict[str, Any]) -> dict[str, Any]:
    if not playback_response:
        return {
            "is_playing": False,
            "message": "Nothing is currently playing.",
        }

    item = playback_response.get("item") or {}
    currently_playing_type = playback_response.get("currently_playing_type")
    formatted_item: dict[str, Any] = {
        "type": currently_playing_type,
        "id": item.get("id"),
        "name": item.get("name"),
        "uri": item.get("uri"),
        "url": (item.get("external_urls") or {}).get("spotify"),
        "duration_ms": item.get("duration_ms"),
    }

    if currently_playing_type == "track":
        album = item.get("album") or {}
        formatted_item.update(
            {
                "artists": [
                    artist.get("name")
                    for artist in item.get("artists", [])
                    if artist.get("name")
                ],
                "album": album.get("name"),
                "explicit": item.get("explicit"),
                "popularity": item.get("popularity"),
            }
        )

    return {
        "is_playing": playback_response.get("is_playing", False),
        "progress_ms": playback_response.get("progress_ms"),
        "currently_playing_type": currently_playing_type,
        "item": formatted_item,
        "attribution": "Data provided by Spotify",
    }
