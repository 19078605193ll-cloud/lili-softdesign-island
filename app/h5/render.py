"""Rendering policy shared with the existing editor; never accept arbitrary URLs."""

import re
from html import unescape
from app.imports.markdown_render import render_markdown


def rich(value):
    if isinstance(value, list):
        return [rich(v) for v in value]
    if not isinstance(value, dict):
        return value
    result = {k: rich(v) for k, v in value.items() if not k.endswith("_html")}
    for key, text in list(result.items()):
        if key.endswith("_markdown") and isinstance(text, str):
            result[key[:-9] + "_html"] = render_markdown(text, audience="learner")
    return result


def historical(attempt):
    import json

    snapshot = json.loads(
        re.sub(
            r"/api/v1/question-assets/([0-9a-fA-F-]{36})",
            lambda m: f"/api/v2/learning/attempts/{attempt.id}/assets/{m[1]}",
            json.dumps(attempt.snapshot),
        )
    )
    return rich(snapshot)


def excerpt(question):
    source = question.get("material_markdown") or next(
        (
            p.get("stem_markdown")
            for p in question.get("parts", [])
            if p.get("stem_markdown")
        ),
        "",
    )
    html = render_markdown(source, audience="learner")
    text = unescape(re.sub("<[^>]*>", " ", html))
    text = " ".join(text.split())
    return text[:240] if text else "图片题，点击查看完整题目"
