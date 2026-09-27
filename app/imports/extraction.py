"""Position preserving PDF text extraction with an optional local OCR engine."""

from __future__ import annotations

import asyncio
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import pymupdf


EXTRACTOR_VERSION = "position-v1"


class OCRUnavailable(RuntimeError):
    pass


class OCRReader(Protocol):
    version: str

    def read(self, image_path: Path) -> list[dict[str, Any]]: ...


class PaddleReader:
    version = "ppocrv5-onnxruntime" if os.name == "nt" else "ppocrv5-paddle-static"

    def __init__(self) -> None:
        # Paddle's Windows predictor can fail to open model JSON under a Unicode
        # user-profile path. Keep the default model cache beside import artifacts.
        os.environ.setdefault(
            "PADDLE_PDX_CACHE_HOME", str((Path.cwd() / "var/imports/.paddlex").resolve())
        )
        os.environ.setdefault("FLAGS_enable_pir_api", "0")
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise OCRUnavailable("PaddleOCR is not installed; install the pdf-ocr extra") from exc
        self.engine = PaddleOCR(
            ocr_version="PP-OCRv5",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            # The Paddle static predictor exits in the Windows/Python 3.13
            # deployment. ONNX Runtime runs the same PP-OCRv5 models locally.
            engine="onnxruntime" if os.name == "nt" else "paddle_static",
            device="cpu",
        )

    def read(self, image_path: Path) -> list[dict[str, Any]]:
        results = list(self.engine.predict(input=str(image_path)))
        if not results:
            return []
        payload = results[0].json
        values = payload.get("res", payload)
        texts = values.get("rec_texts", [])
        scores = values.get("rec_scores", [])
        polygons = values.get("rec_polys", values.get("dt_polys", []))
        if not (len(texts) == len(scores) == len(polygons)):
            raise ValueError("PaddleOCR returned inconsistent text, score and position counts")
        from PIL import Image

        lines = []
        with Image.open(image_path) as image:
            width, height = image.size
            rgb = image.convert("RGB")
            for content, score, polygon in zip(texts, scores, polygons, strict=True):
                if not str(content).strip():
                    continue
                points = polygon.tolist() if hasattr(polygon, "tolist") else polygon
                xs, ys = [float(point[0]) for point in points], [float(point[1]) for point in points]
                box = (max(0, int(min(xs))), max(0, int(min(ys))),
                       min(width, int(max(xs))), min(height, int(max(ys))))
                sample = rgb.crop(box)
                sample.thumbnail((160, 24))
                red = dark = 0
                for r, g, b in sample.getdata():
                    if r < 190 and g < 190 and b < 190:
                        dark += 1
                    if r > g * 1.35 and r > b * 1.35 and r > 90:
                        red += 1
                lines.append({
                    "text": str(content).strip(), "score": float(score),
                    "bbox": [min(xs) / width, min(ys) / height, max(xs) / width, max(ys) / height],
                    "ink": "red" if red > dark * 0.45 and red > 2 else "black",
                })
        return lines


def _digital_lines(pdf_path: Path, page_no: int) -> list[dict[str, Any]]:
    pymupdf.TOOLS.mupdf_display_errors(False)
    document = pymupdf.open(pdf_path)
    try:
        page = document[page_no - 1]
        width, height = page.rect.width, page.rect.height
        result = []
        for block in page.get_text("dict", sort=True)["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                content = "".join(span.get("text", "") for span in spans).strip()
                if not content:
                    continue
                x0, y0, x1, y1 = line["bbox"]
                result.append({"text": content, "score": 1.0,
                               "bbox": [x0 / width, y0 / height, x1 / width, y1 / height]})
        return result
    finally:
        document.close()


def _usable_text(lines: list[dict[str, Any]]) -> bool:
    body = "".join(value["text"] for value in lines if 0.06 < value["bbox"][1] < 0.94)
    # A scanned PDF often has just a searchable advertisement/header text layer.
    return len(body) >= 120 and len(re.findall(r"[\u4e00-\u9fff]", body)) >= 20


def _mark_furniture(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for line in lines:
        text = line["text"]
        y = line["bbox"][1]
        line["furniture"] = bool(
            y < 0.07 or y > 0.95 or text.strip().isdigit() and len(text.strip()) <= 2
            or "ruankaodaren.com" in text
            or ("手机端题库" in text and "PC端题库" in text)
        )
    return sorted(lines, key=lambda value: (round(value["bbox"][1] / 0.012), value["bbox"][0]))


def extract_page_sync(
    pdf_path: Path, image_path: Path, cache_path: Path, *,
    source_sha256: str, page_no: int, ocr_reader: OCRReader | None = None,
) -> dict[str, Any]:
    digital = _digital_lines(pdf_path, page_no)
    use_digital = _usable_text(digital)
    engine_version = "pymupdf" if use_digital else (ocr_reader.version if ocr_reader else PaddleReader.version)
    identity = {"source_sha256": source_sha256, "page_no": page_no,
                "extractor_version": EXTRACTOR_VERSION, "engine_version": engine_version}
    if cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if all(cached.get(key) == value for key, value in identity.items()):
                return cached
        except (OSError, ValueError):
            pass
    if use_digital:
        lines = digital
    else:
        reader = ocr_reader or _default_reader()
        lines = reader.read(image_path)
    payload = {**identity, "method": "digital" if use_digital else "ocr",
               "lines": _mark_furniture(lines)}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(cache_path)
    return payload


async def extract_page(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return await asyncio.to_thread(extract_page_sync, *args, **kwargs)


@lru_cache(maxsize=1)
def _default_reader() -> PaddleReader:
    return PaddleReader()
