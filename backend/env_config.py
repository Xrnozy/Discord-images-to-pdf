from __future__ import annotations

import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"

RETRIEVAL_GUILD_ID = "RETRIEVAL_GUILD_ID"
RETRIEVAL_CATEGORY_ID = "RETRIEVAL_CATEGORY_ID"
RETRIEVAL_CHANNEL_ID = "RETRIEVAL_CHANNEL_ID"
RETRIEVAL_CHANNEL_NAME = "RETRIEVAL_CHANNEL_NAME"
RETRIEVAL_ALL_IN_CATEGORY = "RETRIEVAL_ALL_IN_CATEGORY"

ALL_RETRIEVAL_KEYS = {
    RETRIEVAL_GUILD_ID,
    RETRIEVAL_CATEGORY_ID,
    RETRIEVAL_CHANNEL_ID,
    RETRIEVAL_CHANNEL_NAME,
    RETRIEVAL_ALL_IN_CATEGORY,
}


def _format_env_value(value: str) -> str:
    if value == "":
        return ""
    if any(c in value for c in " #\"'\\"):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def set_env_values(values: dict[str, str]) -> None:
    keys = set(values.keys())
    lines: list[str] = []
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if not line or line.strip().startswith("#"):
                lines.append(line)
                continue
            key = line.split("=", 1)[0].strip()
            if key in keys:
                continue
            lines.append(line)
    for key, value in values.items():
        lines.append(f"{key}={_format_env_value(value)}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for key, value in values.items():
        os.environ[key] = value


def get_env_value(key: str) -> str | None:
    value = os.getenv(key, "").strip()
    return value or None


def save_retrieval_selection(
    *,
    guild_id: str,
    category_id: str,
    channel_id: str,
    channel_name: str,
    all_in_category: bool,
) -> None:
    set_env_values(
        {
            RETRIEVAL_GUILD_ID: guild_id,
            RETRIEVAL_CATEGORY_ID: category_id,
            RETRIEVAL_CHANNEL_ID: channel_id,
            RETRIEVAL_CHANNEL_NAME: channel_name,
            RETRIEVAL_ALL_IN_CATEGORY: "true" if all_in_category else "false",
        }
    )


def get_saved_selection() -> dict[str, Any]:
    return {
        "guild_id": get_env_value(RETRIEVAL_GUILD_ID),
        "category_id": get_env_value(RETRIEVAL_CATEGORY_ID),
        "channel_id": get_env_value(RETRIEVAL_CHANNEL_ID),
        "channel_name": get_env_value(RETRIEVAL_CHANNEL_NAME),
        "all_in_category": get_env_value(RETRIEVAL_ALL_IN_CATEGORY) == "true",
    }


def get_retrieval_channel_name() -> str | None:
    return get_env_value(RETRIEVAL_CHANNEL_NAME)


def get_retrieval_channel_ids() -> list[str]:
    raw = get_env_value(RETRIEVAL_CHANNEL_ID)
    if not raw:
        return []
    return [c.strip() for c in raw.split(",") if c.strip()]
