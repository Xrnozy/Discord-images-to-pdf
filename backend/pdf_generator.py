from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import img2pdf
from pypdf import PdfReader

from backend.store import ROOT

OUTPUT_DIR = ROOT / "output"
MAX_PAGE_PT = 14400.0


def _page_size_pts(width: int, height: int) -> tuple[float, float]:
    """1 px = 1 pt; scale page box only if Acrobat limit would be exceeded."""
    w, h = float(width), float(height)
    scale = min(1.0, MAX_PAGE_PT / max(w, h))
    return w * scale, h * scale


def _sanitize_folder_name(name: str) -> str:
    text = name.strip().lower()
    text = re.sub(r"[^\w\-]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "screenshots"


def channel_label(images: list[dict], requested: str | None = None) -> str:
    if requested and requested.strip() and requested.strip().lower() not in {
        "screenshots",
        "channel",
        "all-channels",
    }:
        return requested.strip()
    names = {
        img.get("channel_name", "")
        for img in images
        if img.get("channel_name") and img.get("channel_name") != "local"
    }
    if len(names) == 1:
        return next(iter(names))
    if len(names) > 1:
        return "all-channels"
    return "screenshots"


def make_generation_folder(channel_name: str) -> Path:
    """One folder per generation run: {channel}-{YYYY-MM-DD}-generated"""
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    base = f"{_sanitize_folder_name(channel_name)}-{today}-generated"
    folder = OUTPUT_DIR / base
    if folder.exists():
        counter = 2
        while (OUTPUT_DIR / f"{base}-{counter}").exists():
            counter += 1
        folder = OUTPUT_DIR / f"{base}-{counter}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def generate_pdf_for_date(
    images: list[dict], date_key: str, output_folder: Path
) -> Path:
    sorted_images = sorted(images, key=lambda i: i["capture_datetime"])
    paths: list[Path] = []

    for img in sorted_images:
        src = ROOT / img.get("pdf_source_path", img["local_path"])
        if not src.exists():
            src = ROOT / img["local_path"]
        paths.append(src)

    def layout_fun(
        imgwidthpx: int, imgheightpx: int, _ndpi: tuple[float, float]
    ) -> tuple[float, float, float, float]:
        pw, ph = _page_size_pts(imgwidthpx, imgheightpx)
        return pw, ph, pw, ph

    out_path = output_folder / f"screenshots_{date_key}.pdf"
    with out_path.open("wb") as f:
        f.write(img2pdf.convert([str(p) for p in paths], layout_fun=layout_fun))
    return out_path


def generate_all_pdfs(images: list[dict], channel_name: str) -> tuple[Path, list[Path]]:
    output_folder = make_generation_folder(channel_name)
    by_date: dict[str, list[dict]] = defaultdict(list)
    for img in images:
        by_date[img["capture_date"]].append(img)
    results = [
        generate_pdf_for_date(by_date[date_key], date_key, output_folder)
        for date_key in sorted(by_date)
    ]
    return output_folder, results


def verify_page_aspect_ratios(pdf_path: Path, expected_sizes: list[tuple[int, int]]) -> None:
    reader = PdfReader(str(pdf_path))
    if len(reader.pages) != len(expected_sizes):
        raise AssertionError(
            f"Expected {len(expected_sizes)} pages, got {len(reader.pages)}"
        )
    for page, (w, h) in zip(reader.pages, expected_sizes):
        box = page.mediabox
        pw = float(box.width)
        ph = float(box.height)
        expected_ratio = w / h
        actual_ratio = pw / ph
        if abs(expected_ratio - actual_ratio) > 0.01:
            raise AssertionError(
                f"Aspect ratio mismatch: expected {expected_ratio:.4f}, got {actual_ratio:.4f}"
            )
