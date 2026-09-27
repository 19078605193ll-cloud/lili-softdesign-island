from __future__ import annotations

from pathlib import Path

import pymupdf
from PIL import Image

from app.imports.extraction import extract_page_sync


class FakeOCR:
    version = "fake-v1"

    def __init__(self) -> None:
        self.calls = 0

    def read(self, image_path: Path) -> list[dict]:
        self.calls += 1
        return [{"text": "参考答案：D", "score": 0.99,
                 "bbox": [0.1, 0.1, 0.4, 0.2]}]


def test_scanned_page_cache_checks_pdf_hash_and_engine(tmp_path: Path) -> None:
    pdf = tmp_path / "sample.pdf"
    document = pymupdf.open()
    document.new_page()
    document.save(pdf)
    document.close()
    image = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(image)
    cache = tmp_path / "page.json"
    ocr = FakeOCR()

    first = extract_page_sync(pdf, image, cache, source_sha256="a" * 64,
                              page_no=1, ocr_reader=ocr)
    second = extract_page_sync(pdf, image, cache, source_sha256="a" * 64,
                               page_no=1, ocr_reader=ocr)
    assert first == second
    assert ocr.calls == 1
    assert first["method"] == "ocr"

    extract_page_sync(pdf, image, cache, source_sha256="b" * 64,
                      page_no=1, ocr_reader=ocr)
    assert ocr.calls == 2
