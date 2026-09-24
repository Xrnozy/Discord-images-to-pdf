from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ExifTags

from backend.discord_client import DiscordClient
from backend.pdf_generator import _sanitize_folder_name
from backend.store import (
    ROOT,
    add_image,
    clear_watermarks,
    get_image,
    list_images_for_channel,
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


def _screenshot_path(
    channel_name: str, capture_date: str, attachment_id: str, ext: str
) -> Path:
    folder = (
        DOWNLOADS_DIR
        / _sanitize_folder_name(channel_name)
        / capture_date
        / "screenshots"
    )
    return folder / f"{attachment_id}{ext}"


def _path_folder_meta(path: Path) -> dict[str, str]:
    try:
        rel = path.relative_to(DOWNLOADS_DIR)
    except ValueError:
        return {}
    parts = rel.parts
    if len(parts) >= 4 and parts[-2] == "screenshots":
        return {"channel_name": parts[0], "capture_date": parts[1]}
    return {}


def _move_screenshot(path: Path, channel_name: str, capture_date: str) -> Path:
    target = _screenshot_path(channel_name, capture_date, path.stem, path.suffix)
    if path.resolve() == target.resolve():
        return path
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        path.unlink()
    else:
        path.rename(target)
    return target


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

    msg_dt = _discord_timestamp(message["timestamp"])
    date_key = msg_dt.strftime("%Y-%m-%d")
    ext = _extension_from_attachment(attachment)
    dest = _screenshot_path(channel_name, date_key, attachment_id, ext)

    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = await client.download_attachment(attachment["url"])
        dest.write_bytes(data)

    width, height, fmt = _dimensions(dest)
    exif_dt = _exif_capture_datetime(dest)
    capture_dt = exif_dt or msg_dt
    final_date = capture_dt.strftime("%Y-%m-%d")
    if final_date != date_key:
        dest = _move_screenshot(dest, channel_name, final_date)
        date_key = final_date

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
    folder_meta = _path_folder_meta(path)
    record = {
        "attachment_id": attachment_id,
        "filename": existing.get("filename") if existing else path.name,
        "message_id": (existing or {}).get("message_id", ""),
        "channel_id": (existing or {}).get("channel_id", ""),
        "channel_name": (existing or {}).get("channel_name")
        or folder_meta.get("channel_name", "local"),
        "message_timestamp": (existing or {}).get("message_timestamp", ""),
        "source_url": (existing or {}).get("source_url", ""),
        "width": width,
        "height": height,
        "format": fmt,
        "capture_date": folder_meta.get("capture_date")
        or capture_dt.strftime("%Y-%m-%d"),
        "capture_datetime": capture_dt.isoformat(),
        "orientation": _orientation(width, height),
        "local_path": str(path.relative_to(ROOT)),
        "pdf_source_path": str(pdf_path.relative_to(ROOT)),
        "date_source": date_source if not existing else existing.get("date_source", date_source),
    }
    return record


def _is_webp_converted_png(path: Path) -> bool:
    return path.suffix.lower() == ".png" and path.with_suffix(".webp").exists()


def _iter_image_paths(root: Path):
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        if _is_webp_converted_png(path):
            continue
        yield path


def _index_image_file(
    path: Path,
    *,
    channel_name: str = "",
    channel_id: str = "",
) -> bool:
    attachment_id = path.stem
    existing = get_image(attachment_id)
    if existing:
        local = ROOT / existing["local_path"]
        if local.exists() and local.resolve() == path.resolve():
            if channel_id and existing.get("channel_id") != channel_id:
                existing["channel_id"] = channel_id
                existing["channel_name"] = channel_name or existing.get("channel_name", "")
                add_image(existing)
            return False
    record = _record_from_path(path, existing)
    if channel_name:
        record["channel_name"] = channel_name
    if channel_id:
        record["channel_id"] = channel_id
    add_image(record)
    return True


def list_images_by_folder_name(channel_name: str) -> list[dict[str, Any]]:
    key = _sanitize_folder_name(channel_name)
    return [
        img
        for img in load_index()["images"].values()
        if _sanitize_folder_name(img.get("channel_name", "")) == key
    ]


def dates_on_disk(channel_name: str) -> list[str]:
    folder = DOWNLOADS_DIR / _sanitize_folder_name(channel_name)
    if not folder.is_dir():
        return []
    return sorted(
        [d.name for d in folder.iterdir() if d.is_dir()],
        reverse=True,
    )


def sync_channel_from_disk(channel_name: str, channel_id: str = "") -> int:
    """Load screenshots from downloads/{channel}/{date}/screenshots/ into the index."""
    folder = DOWNLOADS_DIR / _sanitize_folder_name(channel_name)
    added = 0
    for path in _iter_image_paths(folder):
        if _index_image_file(path, channel_name=channel_name, channel_id=channel_id):
            added += 1
    return added


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
    """Index all image files under downloads/ (all channel folders + legacy flat files)."""
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    prune_missing_downloads()
    added = 0
    for entry in sorted(DOWNLOADS_DIR.iterdir()):
        if entry.is_dir():
            added += sync_channel_from_disk(entry.name)
            continue
        if entry.is_file() and entry.suffix.lower() in IMAGE_SUFFIXES:
            try:
                if _index_image_file(entry):
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


def delete_all_downloaded_images(
    records: list[dict[str, Any]], *, clear_channel_watermarks: bool = True
) -> int:
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
    if clear_channel_watermarks:
        clear_watermarks(list(channel_ids))
    return deleted


def delete_channel_download_folder(channel_name: str) -> None:
    folder = DOWNLOADS_DIR / _sanitize_folder_name(channel_name)
    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)


def delete_channel_date_folder(channel_name: str, capture_date: str) -> None:
    folder = DOWNLOADS_DIR / _sanitize_folder_name(channel_name) / capture_date
    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)


def _collect_channel_images(
    *,
    channel_id: str = "",
    channel_name: str = "",
) -> tuple[list[dict[str, Any]], set[str]]:
    """Images and download folders for one channel only."""
    images: list[dict[str, Any]] = []
    seen: set[str] = set()
    folders: set[str] = set()

    if channel_name:
        sync_channel_from_disk(channel_name, channel_id)
        folders.add(_sanitize_folder_name(channel_name))
        for img in list_images_by_folder_name(channel_name):
            if img["attachment_id"] not in seen:
                images.append(img)
                seen.add(img["attachment_id"])

    if channel_id:
        for img in list_images_for_channel(channel_id):
            if img["attachment_id"] not in seen:
                images.append(img)
                seen.add(img["attachment_id"])
            name = img.get("channel_name", "")
            if name:
                folders.add(_sanitize_folder_name(name))

    return images, folders


def reset_selection_downloads(
    *,
    channel_id: str | None = None,
    channel_name: str | None = None,
    all_in_category: bool = False,
    category_channel_ids: list[str] | None = None,
    capture_date: str | None = None,
) -> int:
    """Delete downloads for the selected channel(s), optionally scoped to one date."""
    images: list[dict[str, Any]] = []
    seen: set[str] = set()
    folders: set[str] = set()
    channel_names: set[str] = set()
    watermark_ids: set[str] = set()
    date_key = (capture_date or "").strip()

    if all_in_category:
        allowed = [cid for cid in (category_channel_ids or []) if cid]
        if not allowed:
            return 0
        index_images = load_index()["images"].values()
        names_by_id: dict[str, str] = {}
        for img in index_images:
            cid = img.get("channel_id", "")
            if cid in allowed and img.get("channel_name"):
                names_by_id.setdefault(cid, img["channel_name"])
        for cid in allowed:
            cname = names_by_id.get(cid, "")
            batch, batch_folders = _collect_channel_images(
                channel_id=cid, channel_name=cname
            )
            for img in batch:
                if img["attachment_id"] not in seen:
                    images.append(img)
                    seen.add(img["attachment_id"])
            folders.update(batch_folders)
            if cname:
                channel_names.add(cname)
            watermark_ids.add(cid)
    else:
        if not channel_id and not channel_name:
            return 0
        batch, batch_folders = _collect_channel_images(
            channel_id=channel_id or "",
            channel_name=channel_name or "",
        )
        images = batch
        folders = batch_folders
        if channel_name:
            channel_names.add(channel_name)
        if channel_id:
            watermark_ids.add(channel_id)

    if date_key:
        images = [img for img in images if img.get("capture_date") == date_key]

    deleted = delete_all_downloaded_images(
        images, clear_channel_watermarks=not date_key
    )

    if date_key:
        names_to_clean = set(channel_names)
        for img in images:
            name = img.get("channel_name", "")
            if name:
                names_to_clean.add(name)
        for name in names_to_clean:
            delete_channel_date_folder(name, date_key)
    else:
        if watermark_ids:
            clear_watermarks(list(watermark_ids))
        for folder in folders:
            path = DOWNLOADS_DIR / folder
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)

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
