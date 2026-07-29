"""Spotify Authorization Code with PKCE flow."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import keyring
from dotenv import load_dotenv

import os

KEYRING_SERVICE = "spotify-mcp"
KEYRING_ACCESS_TOKEN = "access_token"
KEYRING_REFRESH_TOKEN = "refresh_token"
KEYRING_EXPIRES_AT = "expires_at"

SPOTIFY_AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"

DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"
DEFAULT_SCOPES = (
    "user-read-currently-playing user-modify-playback-state "
    "playlist-modify-private playlist-modify-public"
)


class SpotifyAuthError(Exception):
    """Raised when Spotify authentication fails."""


@dataclass
class TokenBundle:
    access_token: str
    refresh_token: str | None
    expires_at: float


def _load_env() -> None:
    load_dotenv()
    repo_root = Path(__file__).resolve().parents[2]
    load_dotenv(repo_root / ".env")


def get_client_id() -> str:
    _load_env()
    client_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
    if not client_id:
        raise SpotifyAuthError(
            "SPOTIFY_CLIENT_ID is not set. Copy .env.example to .env and add your client ID."
        )
    return client_id


def get_redirect_uri() -> str:
    _load_env()
    return os.getenv("SPOTIFY_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip()


def generate_code_verifier(length: int = 64) -> str:
    if length < 43 or length > 128:
        raise ValueError("PKCE code verifier must be between 43 and 128 characters.")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


def build_authorize_url(
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    state: str,
    scope: str | None = None,
) -> str:
    params: dict[str, str] = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
        "state": state,
    }
    if scope:
        params["scope"] = scope
    return f"{SPOTIFY_AUTHORIZE_URL}?{urlencode(params)}"


def save_tokens(
    access_token: str,
    refresh_token: str | None,
    expires_in: int,
) -> None:
    expires_at = time.time() + max(expires_in - 60, 0)
    keyring.set_password(KEYRING_SERVICE, KEYRING_ACCESS_TOKEN, access_token)
    if refresh_token:
        keyring.set_password(KEYRING_SERVICE, KEYRING_REFRESH_TOKEN, refresh_token)
    keyring.set_password(KEYRING_SERVICE, KEYRING_EXPIRES_AT, str(expires_at))


def load_tokens() -> TokenBundle | None:
    access_token = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCESS_TOKEN)
    if not access_token:
        return None

    refresh_token = keyring.get_password(KEYRING_SERVICE, KEYRING_REFRESH_TOKEN)
    expires_raw = keyring.get_password(KEYRING_SERVICE, KEYRING_EXPIRES_AT)
    expires_at = float(expires_raw) if expires_raw else 0.0
    return TokenBundle(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
    )


def clear_tokens() -> None:
    for key in (KEYRING_ACCESS_TOKEN, KEYRING_REFRESH_TOKEN, KEYRING_EXPIRES_AT):
        try:
            keyring.delete_password(KEYRING_SERVICE, key)
        except keyring.errors.PasswordDeleteError:
            pass


def exchange_code_for_tokens(
    code: str,
    code_verifier: str,
    client_id: str,
    redirect_uri: str,
) -> dict[str, Any]:
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    response = httpx.post(
        SPOTIFY_TOKEN_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30.0,
    )
    if response.status_code != 200:
        raise SpotifyAuthError(_format_token_error(response))
    return response.json()


def refresh_access_token(refresh_token: str, client_id: str) -> dict[str, Any]:
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    response = httpx.post(
        SPOTIFY_TOKEN_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30.0,
    )
    if response.status_code != 200:
        raise SpotifyAuthError(_format_token_error(response))
    return response.json()


def _format_token_error(response: httpx.Response) -> str:
    try:
        body = response.json()
        error = body.get("error", "unknown_error")
        description = body.get("error_description", response.text)
        return f"Spotify token request failed ({response.status_code}): {error} - {description}"
    except json.JSONDecodeError:
        return f"Spotify token request failed ({response.status_code}): {response.text}"


def _parse_redirect_host_port(redirect_uri: str) -> tuple[str, int, str]:
    parsed = urlparse(redirect_uri)
    if parsed.scheme not in {"http", "https"}:
        raise SpotifyAuthError("Redirect URI must use http or https.")
    host = parsed.hostname or "127.0.0.1"
    if host == "localhost":
        raise SpotifyAuthError(
            "Use http://127.0.0.1 for local development, not http://localhost."
        )
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    return host, port, path


class _CallbackHandler(BaseHTTPRequestHandler):
    auth_code: str | None = None
    auth_error: str | None = None
    expected_state: str = ""
    expected_path: str = "/callback"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != self.expected_path:
            self.send_error(404, "Not Found")
            return

        params = parse_qs(parsed.query)
        state = params.get("state", [""])[0]
        if state != self.expected_state:
            type(self).auth_error = "State mismatch during Spotify authorization."
            self._send_response("Authorization failed. You can close this tab.", 400)
            return

        if "error" in params:
            type(self).auth_error = params["error"][0]
            self._send_response(
                f"Authorization denied: {type(self).auth_error}. You can close this tab.",
                400,
            )
            return

        code = params.get("code", [""])[0]
        if not code:
            type(self).auth_error = "Missing authorization code."
            self._send_response("Authorization failed. You can close this tab.", 400)
            return

        type(self).auth_code = code
        self._send_response(
            "Spotify authorization successful. You can close this tab and return to Cursor.",
            200,
        )

    def _send_response(self, message: str, status: int) -> None:
        body = f"<html><body><h1>{message}</h1></body></html>"
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def login(scope: str | None = None, timeout_seconds: int = 300) -> None:
    client_id = get_client_id()
    redirect_uri = get_redirect_uri()
    host, port, path = _parse_redirect_host_port(redirect_uri)

    code_verifier = generate_code_verifier()
    code_challenge = generate_code_challenge(code_verifier)
    state = secrets.token_urlsafe(16)

    auth_url = build_authorize_url(
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        state=state,
        scope=scope,
    )

    _CallbackHandler.auth_code = None
    _CallbackHandler.auth_error = None
    _CallbackHandler.expected_state = state
    _CallbackHandler.expected_path = path

    server = HTTPServer((host, port), _CallbackHandler)
    server.timeout = 1

    print(f"Listening for Spotify callback on {redirect_uri}")
    print("Opening browser for Spotify authorization...")
    print(f"If the browser does not open, visit:\n{auth_url}\n")
    webbrowser.open(auth_url)

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        server.handle_request()
        if _CallbackHandler.auth_code or _CallbackHandler.auth_error:
            break

    server.server_close()

    if _CallbackHandler.auth_error:
        raise SpotifyAuthError(_CallbackHandler.auth_error)
    if not _CallbackHandler.auth_code:
        raise SpotifyAuthError(
            "Timed out waiting for Spotify authorization callback. "
            "Make sure the Spotify Developer Dashboard contains the exact redirect URI "
            f"{redirect_uri!r}, complete the browser authorization page, and keep this "
            "terminal running until Spotify redirects back to 127.0.0.1."
        )

    token_data = exchange_code_for_tokens(
        code=_CallbackHandler.auth_code,
        code_verifier=code_verifier,
        client_id=client_id,
        redirect_uri=redirect_uri,
    )

    save_tokens(
        access_token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token"),
        expires_in=int(token_data.get("expires_in", 3600)),
    )
    print("Spotify login successful. Tokens stored in your OS keyring.")


def ensure_valid_access_token() -> str:
    tokens = load_tokens()
    if tokens is None:
        raise SpotifyAuthError(
            "Not logged in to Spotify. Run: python -m spotify_mcp.auth login"
        )

    if time.time() < tokens.expires_at:
        return tokens.access_token

    return refresh_stored_access_token(tokens)


def refresh_stored_access_token(tokens: TokenBundle | None = None) -> str:
    if tokens is None:
        tokens = load_tokens()
    if tokens is None:
        raise SpotifyAuthError(
            "Not logged in to Spotify. Run: python -m spotify_mcp.auth login"
        )

    if not tokens.refresh_token:
        raise SpotifyAuthError(
            "Spotify access token expired and no refresh token is available. "
            "Run: python -m spotify_mcp.auth login"
        )

    client_id = get_client_id()
    refreshed = refresh_access_token(tokens.refresh_token, client_id)
    refresh_token = refreshed.get("refresh_token") or tokens.refresh_token
    save_tokens(
        access_token=refreshed["access_token"],
        refresh_token=refresh_token,
        expires_in=int(refreshed.get("expires_in", 3600)),
    )
    return refreshed["access_token"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Spotify MCP authentication")
    subparsers = parser.add_subparsers(dest="command", required=True)

    login_parser = subparsers.add_parser("login", help="Log in to Spotify via PKCE")
    login_parser.add_argument(
        "--scope",
        default=DEFAULT_SCOPES,
        help="Space-separated Spotify scopes to request",
    )
    login_parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Seconds to wait for the browser callback",
    )

    subparsers.add_parser("logout", help="Remove stored Spotify tokens")

    args = parser.parse_args()
    if args.command == "login":
        login(scope=args.scope, timeout_seconds=args.timeout)
    elif args.command == "logout":
        clear_tokens()
        print("Spotify tokens removed from keyring.")


if __name__ == "__main__":
    main()
