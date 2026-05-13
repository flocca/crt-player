from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube"]


class YouTubeAuthError(Exception):
    """Raised when OAuth token is missing or invalid."""


@dataclass(frozen=True)
class PlaylistEntry:
    video_id: str
    title: str
    position: int
    playlist_item_id: str


class YouTubeClient:
    def __init__(self, api_service):
        """api_service is a googleapiclient resource. In production built via build()."""
        self._api = api_service

    def list_playlist_items(self, playlist_id: str) -> list[PlaylistEntry]:
        try:
            return self._list_inner(playlist_id)
        except HttpError as e:
            status = getattr(e.resp, "status", None)
            if status in (401, 403):
                raise YouTubeAuthError(f"YouTube auth error ({status}): {e}") from e
            raise

    def _list_inner(self, playlist_id: str) -> list[PlaylistEntry]:
        entries: list[PlaylistEntry] = []
        page_token = None
        while True:
            request = self._api.playlistItems().list(
                part="snippet",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=page_token,
            )
            resp = request.execute()
            for raw in resp.get("items", []):
                snippet = raw["snippet"]
                entries.append(PlaylistEntry(
                    video_id=snippet["resourceId"]["videoId"],
                    title=snippet["title"],
                    position=snippet["position"],
                    playlist_item_id=raw["id"],
                ))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return entries

    def delete_playlist_item(self, playlist_item_id: str) -> None:
        try:
            self._api.playlistItems().delete(id=playlist_item_id).execute()
        except HttpError as e:
            status = getattr(e.resp, "status", None)
            if status in (401, 403):
                raise YouTubeAuthError(f"YouTube auth error ({status}): {e}") from e
            if status == 404:
                log.info("playlist item %s already gone (404)", playlist_item_id)
                return
            raise

    @classmethod
    def from_token_file(cls, token_file: str, client_secrets_file: str) -> "YouTubeClient":
        if not os.path.isfile(token_file):
            raise YouTubeAuthError(
                f"OAuth token file missing: {token_file}. Run `crt-bootstrap` first."
            )
        with open(token_file) as f:
            token_data = json.load(f)
        creds = Credentials.from_authorized_user_info(token_data, SCOPES)
        api = build("youtube", "v3", credentials=creds, cache_discovery=False)
        return cls(api_service=api)
