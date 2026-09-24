from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

import httpx

DISCORD_API = "https://discord.com/api/v10"

CATEGORY_TYPE = 4
TEXT_CHANNEL_TYPES = {0, 5}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
IMAGE_CONTENT_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
}


class DiscordError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class DiscordClient:
    def __init__(self, token: str):
        self._token = token.strip()
        self._headers = {
            "Authorization": f"Bot {self._token}",
            "User-Agent": "DiscordScreenshotPDF/1.0",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        max_retries: int = 5,
    ) -> Any:
        url = f"{DISCORD_API}{path}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            for attempt in range(max_retries):
                response = await client.request(
                    method, url, headers=self._headers, params=params
                )
                if response.status_code == 429:
                    payload = response.json()
                    retry_after = float(payload.get("retry_after", 1.0))
                    await asyncio.sleep(retry_after)
                    continue
                if response.status_code == 401:
                    raise DiscordError("Invalid bot token.", 401)
                if response.status_code == 403:
                    raise DiscordError(
                        "Missing permissions or channel not accessible.", 403
                    )
                if response.status_code == 404:
                    raise DiscordError("Resource not found.", 404)
                if response.status_code >= 400:
                    raise DiscordError(
                        f"Discord API error ({response.status_code}).",
                        response.status_code,
                    )
                if response.status_code == 204:
                    return None
                return response.json()
        raise DiscordError("Discord rate limit exceeded. Try again shortly.", 429)

    async def validate(self) -> dict[str, Any]:
        return await self._request("GET", "/users/@me")

    async def get_guilds(self) -> list[dict[str, Any]]:
        guilds = await self._request("GET", "/users/@me/guilds")
        return sorted(guilds, key=lambda g: g.get("name", "").lower())

    async def get_channels(self, guild_id: str) -> list[dict[str, Any]]:
        channels = await self._request("GET", f"/guilds/{guild_id}/channels")
        return sorted(channels, key=lambda c: (c.get("position", 0), c.get("name", "")))

    def list_categories(self, channels: list[dict[str, Any]]) -> list[dict[str, str]]:
        categories = [
            {"id": c["id"], "name": c["name"]}
            for c in channels
            if c.get("type") == CATEGORY_TYPE
        ]
        categories.append({"id": "__none__", "name": "No category"})
        return categories

    def list_text_channels(
        self, channels: list[dict[str, Any]], category_id: str | None
    ) -> list[dict[str, Any]]:
        parent = None if category_id in (None, "", "__none__") else category_id
        result = []
        for ch in channels:
            if ch.get("type") not in TEXT_CHANNEL_TYPES:
                continue
            ch_parent = ch.get("parent_id")
            if parent is None:
                if ch_parent is not None:
                    continue
            elif ch_parent != parent:
                continue
            result.append(ch)
        return result

    async def iter_messages(
        self,
        channel_id: str,
        *,
        stop_at_message_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        before: str | None = None
        while True:
            params: dict[str, Any] = {"limit": 100}
            if before:
                params["before"] = before
            batch = await self._request(
                "GET", f"/channels/{channel_id}/messages", params=params
            )
            if not batch:
                break
            for message in batch:
                if stop_at_message_id and message["id"] == stop_at_message_id:
                    return
                yield message
            before = batch[-1]["id"]
            if len(batch) < 100:
                break

    @staticmethod
    def is_image_attachment(attachment: dict[str, Any]) -> bool:
        filename = (attachment.get("filename") or "").lower()
        content_type = (attachment.get("content_type") or "").lower()
        if any(filename.endswith(ext) for ext in IMAGE_EXTENSIONS):
            return True
        return content_type in IMAGE_CONTENT_TYPES

    async def download_attachment(self, url: str) -> bytes:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            for _ in range(5):
                response = await client.get(url)
                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", "1"))
                    await asyncio.sleep(retry_after)
                    continue
                if response.status_code >= 400:
                    raise DiscordError(
                        f"Failed to download attachment ({response.status_code}).",
                        response.status_code,
                    )
                return response.content
        raise DiscordError("Failed to download attachment after retries.", 429)
