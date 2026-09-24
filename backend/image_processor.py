from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ExifTags

from backend.discord_client import DiscordClient
from backend.store import (
    ROOT,
    add_image,
    clear_watermarks,
    get_image,
    load_index,
    remove_images,
)

DOWNLOADS_DIR = ROOT / "downloads"
THUMBNAILS_DIR = ROOT / "data" / "thumbnails"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

EXT_BY_FORMAT = {
    "PNG": ".png",
    "JPEG": ".jpg",
    "WEBP": ".webp",
}


def _orientation(width: int, height: int) -> str:
    if width == height:
        return "square"
    return "landscape" if width > height else "portrait"


def _parse_exif_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    text = str(value).strip()
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _exif_capture_datetime(path: Path) -> datetime | None:
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None
            tag_map = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
            for key in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
                dt = _parse_exif_datetime(tag_map.get(key))
                if dt:
                    return dt
    except Exception:
        return None
    return None


def _discord_timestamp(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()


def _extension_from_attachment(attachment: dict[str, Any]) -> str:
    filename = (attachment.get("filename") or "").lower()
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        if filename.endswith(ext):
            return ext
    content_type = (attachment.get("content_type") or "").lower()
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
    }
    return mapping.get(content_type, ".png")


def _pdf_source_path(path: Path, pil_format: str) -> Path:
    """WEBP cannot be embedded directly; convert once to lossless PNG."""
    if pil_format.upper() != "WEBP":
        return path
    png_path = path.with_suffix(".png")
    if png_path.exists():
        return png_path
    with Image.open(path) as img:
        img.save(png_path, format="PNG")
    return png_path


def _dimensions(path: Path) -> tuple[int, int, str]:
    with Image.open(path) as img:
        return img.size[0], img.size[1], (img.format or "UNKNOWN").upper()


async def process_attachment(
    client: DiscordClient,
    attachment: dict[str, Any],
    message: dict[str, Any],
    channel_id: str,
    channel_name: str,
) -> dict[str, Any] | None:
    attachment_id = attachment["id"]
    existing = get_image(attachment_id)
    if existing:
        local = ROOT / existing["local_path"]
        if local.exists():
            return existing

    ext = _extension_from_attachment(attachment)
    dest = DOWNLOADS_DIR / f"{attachment_id}{ext}"
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

    if not dest.exists():
        data = await client.download_attachment(attachment["url"])
        dest.write_bytes(data)

    width, height, fmt = _dimensions(dest)
    exif_dt = _exif_capture_datetime(dest)
    msg_dt = _discord_timestamp(message["timestamp"])
    capture_dt = exif_dt or msg_dt
    date_key = capture_dt.strftime("%Y-%m-%d")

    pdf_path = _pdf_source_path(dest, fmt)
    record = {
        "attachment_id": attachment_id,
        "filename": attachment.get("filename") or dest.name,
        "message_id": message["id"],
        "channel_id": channel_id,
        "channel_name": channel_name,
        "message_timestamp": message["timestamp"],
        "source_url": attachment["url"],
        "width": width,
        "height": height,
        "format": fmt,
        "capture_date": date_key,
        "capture_datetime": capture_dt.isoformat(),
        "orientation": _orientation(width, height),
        "local_path": str(dest.relative_to(ROOT)),
        "pdf_source_path": str(pdf_path.relative_to(ROOT)),
        "date_source": "exif" if exif_dt else "discord",
    }
    add_image(record)
    return record


def _record_from_path(path: Path, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    attachment_id = path.stem
    width, height, fmt = _dimensions(path)
    exif_dt = _exif_capture_datetime(path)
    if exif_dt:
        capture_dt = exif_dt
        date_source = "exif"
    else:
        capture_dt = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        date_source = "file_mtime"
    pdf_path = _pdf_source_path(path, fmt)
    record = {
        "attachment_id": attachment_id,
        "filename": existing.get("filename") if existing else path.name,
        "message_id": (existing or {}).get("message_id", ""),
        "channel_id": (existing or {}).get("channel_id", ""),
        "channel_name": (existing or {}).get("channel_name", "local"),
        "message_timestamp": (existing or {}).get("message_timestamp", ""),
        "source_url": (existing or {}).get("source_url", ""),
        "width": width,
        "height": height,
        "format": fmt,
        "capture_date": capture_dt.strftime("%Y-%m-%d"),
        "capture_datetime": capture_dt.isoformat(),
        "orientation": _orientation(width, height),
        "local_path": str(path.relative_to(ROOT)),
        "pdf_source_path": str(pdf_path.relative_to(ROOT)),
        "date_source": date_source if not existing else existing.get("date_source", date_source),
    }
    return record


def prune_missing_downloads() -> int:
    """Drop index entries and thumbnails when files were removed from downloads/."""
    data = load_index()
    missing: list[str] = []
    for aid, record in data["images"].items():
        local = ROOT / record.get("local_path", "")
        if not local.exists():
            missing.append(aid)
    if not missing:
        return 0
    for aid in missing:
        thumb = THUMBNAILS_DIR / f"{aid}.jpg"
        if thumb.exists():
            thumb.unlink()
    remove_images(missing)
    return len(missing)


def sync_downloads_from_disk() -> int:
    """Index image files already in downloads/ (e.g. from a previous run)."""
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    prune_missing_downloads()
    added = 0
    for path in sorted(DOWNLOADS_DIR.iterdir()):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        attachment_id = path.stem
        existing = get_image(attachment_id)
        if existing:
            local = ROOT / existing["local_path"]
            if local.exists():
                continue
        try:
            record = _record_from_path(path, existing)
            add_image(record)
            added += 1
        except Exception:
            continue
    return added


def delete_image_files(record: dict[str, Any]) -> None:
    for key in ("local_path", "pdf_source_path"):
        rel = record.get(key)
        if not rel:
            continue
        path = ROOT / rel
        if path.exists():
            path.unlink()
    thumb = THUMBNAILS_DIR / f"{record['attachment_id']}.jpg"
    if thumb.exists():
        thumb.unlink()


def delete_all_downloaded_images(records: list[dict[str, Any]]) -> int:
    deleted = 0
    ids: list[str] = []
    channel_ids: set[str] = set()
    for record in records:
        delete_image_files(record)
        ids.append(record["attachment_id"])
        if record.get("channel_id"):
            channel_ids.add(record["channel_id"])
        deleted += 1
    remove_images(ids)
    clear_watermarks(list(channel_ids))
    return deleted


def ensure_thumbnail(attachment_id: str) -> str | None:
    record = get_image(attachment_id)
    if not record:
        return None
    src = ROOT / record["local_path"]
    if not src.exists():
        thumb = THUMBNAILS_DIR / f"{attachment_id}.jpg"
        if thumb.exists():
            thumb.unlink()
        remove_images([attachment_id])
        return None
    THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
    thumb = THUMBNAILS_DIR / f"{attachment_id}.jpg"
    if not thumb.exists():
        with Image.open(src) as img:
            img = img.convert("RGB")
            img.thumbnail((200, 200))
            img.save(thumb, format="JPEG", quality=85)
    return f"/api/thumbnail/{attachment_id}"
