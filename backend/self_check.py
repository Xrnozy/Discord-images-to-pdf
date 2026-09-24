"""ponytail: one runnable check — builds a 3-page PDF and asserts page aspect ratios."""

from __future__ import annotations

import tempfile
from pathlib import Path

import img2pdf
from PIL import Image
from pypdf import PdfReader

from backend.pdf_generator import _page_size_pts, verify_page_aspect_ratios


def run_self_check() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths: list[Path] = []
        sizes = [(1920, 1080), (1080, 1920), (1024, 1024)]

        for i, (w, h) in enumerate(sizes):
            path = root / f"test_{i}.png"
            Image.new("RGB", (w, h), color=(40 + i * 60, 80, 120)).save(path)
            paths.append(path)

        def layout_fun(
            imgwidthpx: int, imgheightpx: int, _ndpi: tuple[float, float]
        ) -> tuple[float, float, float, float]:
            pw, ph = _page_size_pts(imgwidthpx, imgheightpx)
            return pw, ph, pw, ph

        pdf_path = root / "test.pdf"
        pdf_path.write_bytes(
            img2pdf.convert([str(p) for p in paths], layout_fun=layout_fun)
        )
        verify_page_aspect_ratios(pdf_path, sizes)
        print(
            "self_check: OK — landscape, portrait, and square pages match image aspect ratios"
        )


if __name__ == "__main__":
    run_self_check()
