from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
INDEX_PATH = DATA_DIR / "index.json"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (ROOT / "downloads").mkdir(parents=True, exist_ok=True)
    (ROOT / "output").mkdir(parents=True, exist_ok=True)


def _default_index() -> dict[str, Any]:
    return {"images": {}, "channel_watermarks": {}}


def load_index() -> dict[str, Any]:
    ensure_dirs()
    if not INDEX_PATH.exists():
        return _default_index()
    with INDEX_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("images", {})
    data.setdefault("channel_watermarks", {})
    return data


def save_index(data: dict[str, Any]) -> None:
    ensure_dirs()
    with INDEX_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def get_watermark(channel_id: str) -> str | None:
    return load_index()["channel_watermarks"].get(channel_id)


def set_watermark(channel_id: str, message_id: str) -> None:
    data = load_index()
    data["channel_watermarks"][channel_id] = message_id
    save_index(data)


def add_image(record: dict[str, Any]) -> None:
    data = load_index()
    data["images"][record["attachment_id"]] = record
    save_index(data)


def image_exists(attachment_id: str) -> bool:
    record = get_image(attachment_id)
    if not record:
        return False
    local = ROOT / record.get("local_path", "")
    return local.exists()


def get_image(attachment_id: str) -> dict[str, Any] | None:
    return load_index()["images"].get(attachment_id)


def list_images_for_channel(channel_id: str) -> list[dict[str, Any]]:
    return [
        img
        for img in load_index()["images"].values()
        if img.get("channel_id") == channel_id
    ]


def list_all_images() -> list[dict[str, Any]]:
    return list(load_index()["images"].values())


def remove_images(attachment_ids: list[str]) -> None:
    if not attachment_ids:
        return
    data = load_index()
    for aid in attachment_ids:
        data["images"].pop(aid, None)
    save_index(data)


def clear_images() -> None:
    data = load_index()
    data["images"] = {}
    save_index(data)


def clear_watermarks(channel_ids: list[str] | None = None) -> None:
    data = load_index()
    if channel_ids is None:
        data["channel_watermarks"] = {}
    else:
        for cid in channel_ids:
            data["channel_watermarks"].pop(cid, None)
    save_index(data)
