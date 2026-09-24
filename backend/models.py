from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ConnectRequest(BaseModel):
    token: str = Field(min_length=1)


class ConnectResponse(BaseModel):
    connected: bool
    username: str
    retrieval_channel_name: str | None = None
    saved_selection: dict[str, Any] | None = None


class GuildItem(BaseModel):
    id: str
    name: str


class CategoryItem(BaseModel):
    id: str
    name: str


class ChannelItem(BaseModel):
    id: str
    name: str
    parent_id: str | None = None


class SaveSelectionRequest(BaseModel):
    guild_id: str
    category_id: str | None = None
    channel_id: str | None = None
    channel_name: str | None = None
    all_in_category: bool = False


class RetrieveRequest(BaseModel):
    guild_id: str
    category_id: str | None = None
    channel_id: str | None = None
    channel_name: str | None = None
    all_in_category: bool = False
    full_rescan: bool = False
    retrieve_date: str | None = None  # "all" or YYYY-MM-DD


class GeneratePdfRequest(BaseModel):
    date: str | None = None
    channel_name: str | None = None


class ImageRecord(BaseModel):
    attachment_id: str
    filename: str
    message_id: str
    channel_id: str
    channel_name: str
    message_timestamp: str
    source_url: str
    width: int
    height: int
    format: str
    capture_date: str
    capture_datetime: str
    orientation: Literal["landscape", "portrait", "square"]
    local_path: str


class JobProgress(BaseModel):
    status: Literal[
        "idle", "running", "paused", "stopped", "complete", "error"
    ]
    phase: str = ""
    messages_checked: int = 0
    images_found: int = 0
    images_downloaded: int = 0
    progress: float = 0.0
    error: str | None = None
    paused: bool = False


class ResetDownloadsRequest(BaseModel):
    channel_id: str | None = None
    channel_name: str | None = None
    all_in_category: bool = False
    capture_date: str | None = None  # YYYY-MM-DD; omit to reset entire selection


class RetrievalSummary(BaseModel):
    messages_checked: int
    images_found: int
    images_downloaded: int
    dates_found: int
    dates: list[dict[str, Any]]
