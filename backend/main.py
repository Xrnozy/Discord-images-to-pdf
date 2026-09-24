from __future__ import annotations

import asyncio
import os
import threading
import webbrowser
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.discord_client import DiscordClient, DiscordError
from backend.env_config import (
    get_retrieval_channel_ids,
    get_retrieval_channel_name,
    get_saved_selection,
    save_retrieval_selection,
)
from backend.image_processor import (
    delete_all_downloaded_images,
    ensure_thumbnail,
    process_attachment,
    prune_missing_downloads,
    sync_downloads_from_disk,
)
from backend.models import (
    ConnectRequest,
    ConnectResponse,
    GeneratePdfRequest,
    JobProgress,
    RetrieveRequest,
    SaveSelectionRequest,
)
from backend.pdf_generator import (
    generate_all_pdfs,
    generate_pdf_for_date,
    make_generation_folder,
)
from backend.store import (
    ROOT,
    ensure_dirs,
    get_watermark,
    list_all_images,
    list_images_for_channel,
    set_watermark,
)

load_dotenv(ROOT / ".env")

FRONTEND_DIR = ROOT / "frontend"
ENV_PATH = ROOT / ".env"

_client: DiscordClient | None = None
_bot_username: str | None = None
_job_lock = asyncio.Lock()
_job: dict[str, Any] = {
    "status": "idle",
    "phase": "",
    "messages_checked": 0,
    "images_found": 0,
    "images_downloaded": 0,
    "progress": 0.0,
    "error": None,
    "channel_ids": [],
    "newest_message_id": None,
}


def _save_token(token: str) -> None:
    from backend.env_config import set_env_values

    set_env_values({"DISCORD_BOT_TOKEN": token})


def _load_token() -> str | None:
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    return token or None


def _get_client() -> DiscordClient:
    global _client
    if _client is None:
        token = _load_token()
        if not token:
            raise HTTPException(status_code=400, detail="Bot token not configured.")
        _client = DiscordClient(token)
    return _client


def _reset_job() -> None:
    _job.update(
        {
            "status": "idle",
            "phase": "Retrieving Discord messages...",
            "messages_checked": 0,
            "images_found": 0,
            "images_downloaded": 0,
            "progress": 0.0,
            "error": None,
            "channel_ids": [],
            "newest_message_id": None,
        }
    )


def _group_by_date(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for img in images:
        groups[img["capture_date"]].append(img)
    result = []
    for date_key in sorted(groups, reverse=True):
        imgs = sorted(groups[date_key], key=lambda i: i["capture_datetime"])
        result.append(
            {
                "date": date_key,
                "count": len(imgs),
                "images": [
                    {
                        **{k: v for k, v in img.items() if k != "source_url"},
                        "thumbnail_url": ensure_thumbnail(img["attachment_id"]),
                    }
                    for img in imgs
                ],
            }
        )
    return result


def _message_local_date(message: dict[str, Any]) -> str:
    return (
        datetime.fromisoformat(message["timestamp"].replace("Z", "+00:00"))
        .astimezone()
        .strftime("%Y-%m-%d")
    )


def _dates_for_channel(channel_id: str | None) -> list[str]:
    if channel_id:
        images = list_images_for_channel(channel_id)
    else:
        images = _images_for_saved_channel()
    return sorted({img["capture_date"] for img in images}, reverse=True)


def _retrieval_folder_label(
    req: RetrieveRequest,
    channels_data: list[dict[str, Any]],
    channel_ids: list[tuple[str, str]],
) -> str:
    if req.all_in_category and req.category_id:
        if req.category_id == "__none__":
            return "no-category-all"
        cat_name = next(
            (
                c.get("name", "category")
                for c in channels_data
                if c["id"] == req.category_id
            ),
            "category",
        )
        return f"{cat_name}-all"
    if len(channel_ids) == 1:
        return channel_ids[0][1]
    return "all-channels"


async def _run_retrieval(req: RetrieveRequest) -> None:
    global _job
    if _client is None:
        _job["status"] = "error"
        _job["error"] = "Not connected to Discord. Connect your bot token first."
        return
    client = _client
    channels_data = await client.get_channels(req.guild_id)

    channel_ids: list[tuple[str, str]] = []
    if req.all_in_category and req.category_id:
        for ch in client.list_text_channels(channels_data, req.category_id):
            channel_ids.append((ch["id"], ch.get("name", "unknown")))
    elif req.channel_id:
        name = next(
            (c.get("name", "unknown") for c in channels_data if c["id"] == req.channel_id),
            "unknown",
        )
        channel_ids.append((req.channel_id, name))
    else:
        _job["status"] = "error"
        _job["error"] = "Select a channel or enable all channels in category."
        return

    _job["channel_ids"] = [c[0] for c in channel_ids]
    folder_label = req.channel_name or _retrieval_folder_label(
        req, channels_data, channel_ids
    )
    save_retrieval_selection(
        guild_id=req.guild_id,
        category_id=req.category_id or "",
        channel_id=req.channel_id or ",".join(c[0] for c in channel_ids),
        channel_name=folder_label,
        all_in_category=req.all_in_category,
    )
    session_downloads = 0
    target_date = (req.retrieve_date or "all").strip()
    filter_by_date = target_date not in ("", "all")

    try:
        sync_downloads_from_disk()
        for channel_id, channel_name in channel_ids:
            stop_at = None if req.full_rescan else get_watermark(channel_id)
            # ponytail: if images were cleared (e.g. after PDF gen), rescan full history
            if not req.full_rescan and stop_at and not list_images_for_channel(channel_id):
                stop_at = None
            channel_newest: str | None = None
            async for message in client.iter_messages(
                channel_id, stop_at_message_id=stop_at
            ):
                if channel_newest is None:
                    channel_newest = message["id"]

                _job["messages_checked"] += 1
                msg_date = _message_local_date(message)
                if filter_by_date:
                    if msg_date > target_date:
                        if _job["messages_checked"] % 10 == 0:
                            _job["progress"] = min(
                                0.95,
                                _job["messages_checked"]
                                / max(_job["messages_checked"] + 100, 1),
                            )
                        continue
                    if msg_date < target_date:
                        break

                attachments = message.get("attachments") or []
                for attachment in attachments:
                    if not DiscordClient.is_image_attachment(attachment):
                        continue
                    _job["images_found"] += 1
                    from backend.store import image_exists

                    existed = image_exists(attachment["id"])
                    record = await process_attachment(
                        client, attachment, message, channel_id, channel_name
                    )
                    if record and not existed:
                        session_downloads += 1
                        _job["images_downloaded"] = session_downloads

                if _job["messages_checked"] % 10 == 0:
                    _job["progress"] = min(
                        0.95,
                        _job["messages_checked"]
                        / max(_job["messages_checked"] + 100, 1),
                    )

            if channel_newest and not filter_by_date:
                set_watermark(channel_id, channel_newest)

        _job["phase"] = "Processing..."
        _job["progress"] = 1.0
        _job["status"] = "complete"
        _job["newest_message_id"] = channel_newest
        if _job["messages_checked"] == 0 and not req.full_rescan:
            _job["phase"] = (
                "No new messages since last retrieval. "
                "Use Full Rescan to reload entire history."
            )
    except DiscordError as exc:
        _job["status"] = "error"
        _job["error"] = str(exc)
    except Exception as exc:  # ponytail: surface unexpected errors to UI
        _job["status"] = "error"
        _job["error"] = f"Retrieval failed: {exc}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    sync_downloads_from_disk()
    token = _load_token()
    if token:
        try:
            global _client, _bot_username
            _client = DiscordClient(token)
            me = await _client.validate()
            _bot_username = me.get("username", "bot")
        except DiscordError:
            _client = None
            _bot_username = None

    if os.getenv("OPEN_BROWSER", "1") == "1":
        threading.Timer(1.0, lambda: webbrowser.open("http://localhost:8000")).start()

    yield


app = FastAPI(title="Discord Screenshot PDF", lifespan=lifespan)


@app.get("/api/status")
async def api_status():
    token = _load_token()
    placeholder = token in (None, "", "your_bot_token_here")
    return {
        "connected": _client is not None,
        "username": _bot_username,
        "token_saved": token is not None and not placeholder,
        "retrieval_channel_name": get_retrieval_channel_name(),
        "saved_selection": get_saved_selection(),
    }


def _images_for_saved_channel() -> list[dict[str, Any]]:
    images = list_all_images()
    saved = get_saved_selection()
    channel_ids = get_retrieval_channel_ids()
    if not channel_ids:
        return images
    if saved.get("all_in_category"):
        allowed = set(channel_ids)
        return [i for i in images if i.get("channel_id") in allowed]
    if len(channel_ids) == 1:
        cid = channel_ids[0]
        return [i for i in images if i.get("channel_id") == cid]
    return images


def _pdf_folder_label(body_channel_name: str | None) -> str:
    label = get_retrieval_channel_name() or body_channel_name
    if not label or label.lower() in {"screenshots", "channel", "all-channels"}:
        raise HTTPException(
            status_code=400,
            detail="No saved channel. Select a channel and retrieve screenshots first.",
        )
    return label


@app.post("/api/connect", response_model=ConnectResponse)
async def api_connect(body: ConnectRequest):
    global _client, _bot_username
    token = body.token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token is required.")
    client = DiscordClient(token)
    try:
        me = await client.validate()
    except DiscordError as exc:
        raise HTTPException(status_code=exc.status_code or 400, detail=str(exc))
    _client = client
    _bot_username = me.get("username", "bot")
    _save_token(token)
    return ConnectResponse(
        connected=True,
        username=_bot_username,
        retrieval_channel_name=get_retrieval_channel_name(),
        saved_selection=get_saved_selection(),
    )


@app.get("/api/guilds")
async def api_guilds():
    client = _get_client()
    try:
        guilds = await client.get_guilds()
    except DiscordError as exc:
        raise HTTPException(status_code=exc.status_code or 400, detail=str(exc))
    if not guilds:
        raise HTTPException(
            status_code=404,
            detail="Bot is not in any servers. Invite the bot to a server first.",
        )
    return [{"id": g["id"], "name": g["name"]} for g in guilds]


@app.get("/api/guilds/{guild_id}/categories")
async def api_categories(guild_id: str):
    client = _get_client()
    try:
        channels = await client.get_channels(guild_id)
    except DiscordError as exc:
        raise HTTPException(status_code=exc.status_code or 400, detail=str(exc))
    return client.list_categories(channels)


@app.get("/api/dates")
async def api_dates(channel_id: str | None = None):
    return {"dates": _dates_for_channel(channel_id)}


@app.post("/api/save-selection")
async def api_save_selection(body: SaveSelectionRequest):
    if not body.channel_name:
        raise HTTPException(status_code=400, detail="Channel name is required.")
    save_retrieval_selection(
        guild_id=body.guild_id,
        category_id=body.category_id or "",
        channel_id=body.channel_id or "",
        channel_name=body.channel_name,
        all_in_category=body.all_in_category,
    )
    return {"saved": True, "channel_name": body.channel_name}


@app.get("/api/guilds/{guild_id}/channels")
async def api_channels(guild_id: str, category_id: str | None = None):
    client = _get_client()
    try:
        channels = await client.get_channels(guild_id)
    except DiscordError as exc:
        raise HTTPException(status_code=exc.status_code or 400, detail=str(exc))
    text = client.list_text_channels(channels, category_id)
    return [
        {"id": c["id"], "name": c["name"], "parent_id": c.get("parent_id")}
        for c in text
    ]


@app.post("/api/retrieve")
async def api_retrieve(body: RetrieveRequest):
    async with _job_lock:
        if _job["status"] == "running":
            raise HTTPException(status_code=409, detail="A retrieval job is already running.")
        _reset_job()
        _job["status"] = "running"
        asyncio.create_task(_run_retrieval(body))
    return {"started": True}


@app.get("/api/job", response_model=JobProgress)
async def api_job():
    return JobProgress(
        status=_job["status"],
        phase=_job["phase"],
        messages_checked=_job["messages_checked"],
        images_found=_job["images_found"],
        images_downloaded=_job["images_downloaded"],
        progress=_job["progress"],
        error=_job["error"],
    )


@app.get("/api/results")
async def api_results(channel_id: str | None = None):
    sync_downloads_from_disk()
    if channel_id:
        images = list_images_for_channel(channel_id)
    else:
        images = _images_for_saved_channel()
    dates = _group_by_date(images)
    return {
        "messages_checked": _job["messages_checked"],
        "images_found": len(images),
        "images_downloaded": len(images),
        "dates_found": len(dates),
        "dates": dates,
    }


@app.post("/api/generate-pdf")
async def api_generate_pdf(body: GeneratePdfRequest):
    images = _images_for_saved_channel()
    if not images:
        raise HTTPException(status_code=404, detail="No images found for the saved channel.")
    label = _pdf_folder_label(body.channel_name)
    if body.date:
        images = [i for i in images if i["capture_date"] == body.date]
        if not images:
            raise HTTPException(status_code=404, detail=f"No images for {body.date}.")
        output_folder = make_generation_folder(label)
        path = generate_pdf_for_date(images, body.date, output_folder)
        return {
            "generated": 1,
            "output_folder": str(output_folder.relative_to(ROOT)),
            "files": [str(path.relative_to(ROOT))],
        }
    output_folder, paths = generate_all_pdfs(images, label)
    deleted = delete_all_downloaded_images(images)
    return {
        "generated": len(paths),
        "output_folder": str(output_folder.relative_to(ROOT)),
        "files": [str(p.relative_to(ROOT)) for p in paths],
        "deleted_images": deleted,
    }


@app.get("/api/thumbnail/{attachment_id}")
async def api_thumbnail(attachment_id: str):
    from backend.store import get_image

    record = get_image(attachment_id)
    if not record:
        raise HTTPException(status_code=404, detail="Image not found.")
    thumb_path = ROOT / "data" / "thumbnails" / f"{attachment_id}.jpg"
    ensure_thumbnail(attachment_id)
    if not thumb_path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found.")
    return FileResponse(thumb_path, media_type="image/jpeg")


@app.get("/api/image/{attachment_id}")
async def api_image(attachment_id: str):
    from backend.store import get_image

    record = get_image(attachment_id)
    if not record:
        raise HTTPException(status_code=404, detail="Image not found.")
    path = ROOT / record["local_path"]
    if not path.exists():
        prune_missing_downloads()
        raise HTTPException(status_code=404, detail="Image file not found.")
    media = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media, filename=record.get("filename", path.name))


@app.get("/api/download-pdf/{folder_name}/{date_key}")
async def api_download_pdf(folder_name: str, date_key: str):
    pdf_path = ROOT / "output" / folder_name / f"screenshots_{date_key}.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found. Generate it first.")
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=pdf_path.name,
    )


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
