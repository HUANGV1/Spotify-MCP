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

        # Player and some playlist mutation endpoints may return 204, empty bodies,
        # or opaque non-JSON success payloads. Treat any 2xx without JSON as success.
        if response.status_code == 204 or not response.text.strip():
            return {}

        try:
            parsed = response.json()
        except json.JSONDecodeError:
            return {}

        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}

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


def create_playlist(
    name: str,
    *,
    description: str | None = None,
    public: bool = False,
    collaborative: bool = False,
) -> dict[str, Any]:
    if not name.strip():
        raise ValueError("name is required.")
    if collaborative and public:
        raise ValueError("collaborative playlists must be private (public=False).")

    body: dict[str, Any] = {
        "name": name.strip(),
        "public": public,
        "collaborative": collaborative,
    }
    if description is not None:
        body["description"] = description

    return _request("POST", "/me/playlists", json_body=body)


def add_items_to_playlist(
    playlist_id: str,
    uris: list[str],
    *,
    position: int | None = None,
) -> dict[str, Any]:
    if not playlist_id.strip():
        raise ValueError("playlist_id is required.")
    if not uris:
        raise ValueError("uris must contain at least one Spotify URI.")

    cleaned_uris = [uri for uri in uris if uri]
    if not cleaned_uris:
        raise ValueError("uris must contain at least one Spotify URI.")

    last_response: dict[str, Any] = {}
    for start in range(0, len(cleaned_uris), 100):
        batch = cleaned_uris[start : start + 100]
        body: dict[str, Any] = {"uris": batch}
        if position is not None and start == 0:
            body["position"] = position
        last_response = _request(
            "POST",
            f"/playlists/{playlist_id}/items",
            json_body=body,
        )
    return last_response


def resolve_song_queries(
    song_queries: list[str],
    *,
    market: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    if not song_queries:
        raise ValueError("song_queries must contain at least one search query.")

    matched: list[dict[str, Any]] = []
    unresolved: list[str] = []

    for query in song_queries:
        cleaned = query.strip()
        if not cleaned:
            continue

        response = search_tracks(query=cleaned, limit=1, market=market)
        tracks = format_track_results(response)
        if not tracks:
            unresolved.append(cleaned)
            continue

        track = tracks[0]
        if not track.get("uri"):
            unresolved.append(cleaned)
            continue

        matched.append(
            {
                "query": cleaned,
                "uri": track["uri"],
                "track": track,
            }
        )

    if not matched:
        raise ValueError("No tracks could be resolved from the provided song queries.")

    return matched, unresolved


def format_playlist(playlist_response: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": playlist_response.get("id"),
        "name": playlist_response.get("name"),
        "description": playlist_response.get("description"),
        "public": playlist_response.get("public"),
        "collaborative": playlist_response.get("collaborative"),
        "uri": playlist_response.get("uri"),
        "url": (playlist_response.get("external_urls") or {}).get("spotify"),
        "owner": (playlist_response.get("owner") or {}).get("display_name"),
        "attribution": "Data provided by Spotify",
    }


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
