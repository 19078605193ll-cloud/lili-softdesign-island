from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any, Protocol, TypeVar

from openai import AsyncOpenAI
from PIL import Image
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.imports.schemas import (
    AIClassificationBatch,
    VisionAnswerPage,
    VisionCombinedPage,
    VisionPageRoles,
    VisionQuestionPage,
)

VISION_PROMPT_VERSION = "software-designer-vision-v2"
COMBINED_PROMPT_VERSION = "software-designer-vision-combined-v1"
REPAIR_PROMPT_VERSION = "software-designer-vision-repair-v1"
CLASSIFICATION_PROMPT_VERSION = "software-designer-classification-v2"


class AIConfigurationError(RuntimeError):
    pass


class AIResponseError(RuntimeError):
    pass


SchemaT = TypeVar("SchemaT", bound=BaseModel)


class QuestionAIClient(Protocol):
    provider_name: str
    vision_model: str
    classification_model: str

    async def classify_page_roles(
        self, pages: list[tuple[int, Path]]
    ) -> VisionPageRoles: ...

    async def parse_question_page(
        self, image_path: Path, page_no: int
    ) -> tuple[VisionQuestionPage, dict[str, Any]]: ...

    async def parse_answer_page(
        self, image_path: Path, page_no: int
    ) -> tuple[VisionAnswerPage, dict[str, Any]]: ...

    async def parse_combined_page(
        self, image_path: Path, page_no: int
    ) -> tuple[VisionCombinedPage, dict[str, Any]]: ...

    async def repair_question_pages(
        self,
        image_paths: list[Path],
        page_nos: list[int],
        question_numbers: list[int],
        previous_items: list[dict[str, Any]],
    ) -> tuple[VisionQuestionPage, dict[str, Any]]: ...

    async def repair_answer_pages(
        self,
        image_paths: list[Path],
        page_nos: list[int],
        question_numbers: list[int],
        previous_items: list[dict[str, Any]],
    ) -> tuple[VisionAnswerPage, dict[str, Any]]: ...

    async def classify_questions(
        self, items: list[dict[str, Any]], catalog: list[dict[str, Any]]
    ) -> tuple[AIClassificationBatch, dict[str, Any]]: ...


class OpenAICompatibleQuestionClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.ai_api_key:
            raise AIConfigurationError("AI_API_KEY is not configured")
        if not settings.ai_classification_model:
            raise AIConfigurationError("AI_CLASSIFICATION_MODEL is not configured")
        kwargs: dict[str, Any] = {
            "api_key": settings.ai_api_key,
            "timeout": settings.ai_timeout_seconds,
            "max_retries": 0,
        }
        if settings.ai_base_url:
            kwargs["base_url"] = settings.ai_base_url
        self.client = AsyncOpenAI(**kwargs)
        self.provider_name = settings.ai_provider_name
        self.vision_model = settings.ai_vision_model
        self.classification_model = settings.ai_classification_model

    async def _request_json(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        schema: type[SchemaT],
    ) -> tuple[SchemaT, dict[str, Any]]:
        last_error: Exception | None = None
        current_messages = list(messages)
        for attempt in range(2):
            response = await self.client.chat.completions.create(
                model=model,
                messages=current_messages,  # type: ignore[arg-type]
                response_format={"type": "json_object"},
                temperature=0,
            )
            content = response.choices[0].message.content or ""
            try:
                payload = self._decode_json(content)
                return schema.model_validate(payload), payload
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = exc
                if attempt == 0:
                    current_messages.append({"role": "assistant", "content": content})
                    current_messages.append(
                        {
                            "role": "user",
                            "content": (
                                "上一个响应不符合约定的 JSON 结构。请只返回修正后的 JSON，"
                                f"不要解释。校验错误：{exc}"
                            ),
                        }
                    )
        raise AIResponseError(f"AI response failed validation after one retry: {last_error}")

    @staticmethod
    def _decode_json(content: str) -> dict[str, Any]:
        value = content.strip()
        if value.startswith("```"):
            lines = value.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            value = "\n".join(lines)
        payload = json.loads(value)
        if not isinstance(payload, dict):
            raise ValueError("top-level AI response must be an object")
        return payload

    @staticmethod
    def _image_url(image_path: Path) -> str:
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    async def classify_page_roles(
        self, pages: list[tuple[int, Path]]
    ) -> VisionPageRoles:
        content: list[dict[str, Any]] = [{
            "type": "text",
            "text": (
                "Inspect each numbered exam PDF page. Return JSON only: "
                '{"pages":[{"page_no":1,"questions":true,"answers":true}]}. '
                "Set questions=true only for actual numbered exam items 1 through 75, "
                "including numbered blanks embedded in an English passage. A cover page "
                "with instructions and sample/example items such as 88 or 89 is NOT a "
                "question page. Set answers=true if the page "
                "contains answer keys, reference answers or explanations, including answers "
                "printed directly beneath questions or a compact answer table after the "
                "last question. Both may be true for the same page. Scan the entire page: "
                "a new numbered question can begin near the bottom after a long red "
                "explanation. Pay special attention to continuation pages at the end "
                "of a question sequence. "
                "Return exactly one record for every supplied page number."
            ),
        }]
        for page_no, image_path in pages:
            with Image.open(image_path) as source:
                thumbnail = source.convert("RGB")
                thumbnail.thumbnail((1500, 2100) if len(pages) == 1 else (1100, 1500))
                buffer = io.BytesIO()
                thumbnail.save(buffer, format="JPEG", quality=85 if len(pages) == 1 else 78)
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
            content.extend([
                {"type": "text", "text": f"Page {page_no}"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
            ])
        result, _ = await self._request_json(
            model=self.vision_model,
            messages=[
                {"role": "system", "content": "Classify document page roles from images. Return JSON only."},
                {"role": "user", "content": content},
            ],
            schema=VisionPageRoles,
        )
        return result

    async def parse_question_page(
        self, image_path: Path, page_no: int
    ) -> tuple[VisionQuestionPage, dict[str, Any]]:
        prompt = f"""
Transcribe the Software Designer foundation multiple-choice questions visible on
source page {page_no}. Return JSON only. Copy visible text exactly; never guess
cropped text and never redraw a figure.

Rules:
- A printed range such as N-M represents one independent item for every blank in
  that range. Return one item per actual question number and keep them in one group.
- source_label must be the exact printed question number or printed number range.
  Never use the page number as source_label.
- Unrelated questions must not share a group.
- Keep separate A/B/C/D option sets separate and assign them to their matching
  question numbers in reading order.
- If a question starts or ends outside this page, return every visible fragment;
  do not invent the missing fragment.
- Set needs_visual_asset for figures, tables, UML, state diagrams, or code images.
- Ignore any reference answer, answer key, explanation, solution or commentary
  printed on the same page. Do not include it in stems, options or group material.

Output shape:
{{"groups":[{{"source_label":"<printed label>","material_markdown":"<shared text>",
"items":[{{"question_no":0,"stem_markdown":"<item text>","options":[
{{"key":"A","content_markdown":"<visible option>"}}],
"needs_visual_asset":false,"visual_description":null}}]}}]}}
Replace the zero placeholder with only numbers actually printed on the page.
""".strip()
        messages = [
            {"role": "system", "content": "你是严谨的考试资料转写器，只输出 JSON。"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": self._image_url(image_path)}},
                ],
            },
        ]
        return await self._request_json(
            model=self.vision_model, messages=messages, schema=VisionQuestionPage
        )

    async def parse_answer_page(
        self, image_path: Path, page_no: int
    ) -> tuple[VisionAnswerPage, dict[str, Any]]:
        prompt = f"""
Transcribe answers and explanations visible on Software Designer foundation source
page {page_no}. Return JSON only and do not infer content outside this page.

Rules:
- Expand every printed question-number range into one answer record per number.
- When a range has an ordered answer sequence, map answers to question numbers in
  ascending order.
- Never copy question numbers from these instructions; use only numbers visible in
  the source image.
- Preserve visible explanation text. A continuation without its number may be
  omitted here and will be recovered by the adjacent-page repair pass.

Output shape:
{{"answers":[{{"question_no":0,"correct_option_keys":["A"],
"explanation_markdown":"<visible explanation or null>"}}]}}
Replace the zero placeholder with only numbers actually printed on the page.
""".strip()
        messages = [
            {"role": "system", "content": "你是严谨的考试答案转写器，只输出 JSON。"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": self._image_url(image_path)}},
                ],
            },
        ]
        return await self._request_json(
            model=self.vision_model, messages=messages, schema=VisionAnswerPage
        )

    async def parse_combined_page(
        self, image_path: Path, page_no: int
    ) -> tuple[VisionCombinedPage, dict[str, Any]]:
        prompt = f"""Read source page {page_no} once and transcribe BOTH the numbered
multiple-choice questions and their printed answers/explanations. Return JSON only:
{{"groups":[{{"source_label":"<printed number or range>","material_markdown":"<shared question text>","items":[{{"question_no":1,"stem_markdown":"<question text>","options":[{{"key":"A","content_markdown":"<option>"}}],"needs_visual_asset":false,"visual_description":null}}]}}],"answers":[{{"question_no":1,"correct_option_keys":["A"],"explanation_markdown":"<explanation or null>"}}]}}
Both arrays must contain only content visible on this page and must not be empty.
Keep question text/options/material strictly separate from answer keys, solutions and
commentary printed below them. Do not guess cropped text. Return visible fragments
of questions crossing page boundaries. Expand printed ranges and ordered answer
sequences to one record per question number. Include a final compact answer table
if present. Use only printed question numbers (1–75), never the source page number.
Set needs_visual_asset for figures, tables, UML, diagrams or code images.
""".strip()
        return await self._request_json(
            model=self.vision_model,
            messages=[
                {"role": "system", "content": "你是严谨的试题与答案转写器，只输出 JSON。"},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": self._image_url(image_path)}},
                ]},
            ],
            schema=VisionCombinedPage,
        )

    def _repair_content(
        self, prompt: str, image_paths: list[Path], page_nos: list[int]
    ) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for page_no, image_path in zip(page_nos, image_paths, strict=True):
            content.append({"type": "text", "text": f"Source page {page_no}:"})
            content.append(
                {"type": "image_url", "image_url": {"url": self._image_url(image_path)}}
            )
        return content

    async def repair_question_pages(
        self,
        image_paths: list[Path],
        page_nos: list[int],
        question_numbers: list[int],
        previous_items: list[dict[str, Any]],
    ) -> tuple[VisionQuestionPage, dict[str, Any]]:
        prompt = json.dumps(
            {
                "task": "Repair question transcription using all adjacent source pages.",
                "required_question_numbers": question_numbers,
                "previous_items": previous_items,
                "rules": [
                    "Return every required question number exactly once.",
                    "Use only source text; do not invent missing content.",
                    "Join stems and options split across page boundaries.",
                    "Expand printed ranges into one item per number and one option set per item.",
                    "Use an exact printed range as source_label only for a real shared group.",
                    "Ordinary questions must use their own printed number as source_label.",
                    "Every complete morning item must contain options A, B, C, and D.",
                ],
                "output_shape": {
                    "groups": [
                        {
                            "source_label": "<printed label>",
                            "material_markdown": "<shared stem>",
                            "items": [
                                {
                                    "question_no": "<required integer>",
                                    "stem_markdown": "<item-specific text>",
                                    "options": [
                                        {"key": "A", "content_markdown": "<option>"}
                                    ],
                                    "needs_visual_asset": False,
                                    "visual_description": None,
                                }
                            ],
                        }
                    ]
                },
            },
            ensure_ascii=False,
        )
        messages = [
            {
                "role": "system",
                "content": "You repair exam transcription from adjacent pages. Return JSON only.",
            },
            {
                "role": "user",
                "content": self._repair_content(prompt, image_paths, page_nos),
            },
        ]
        return await self._request_json(
            model=self.vision_model, messages=messages, schema=VisionQuestionPage
        )

    async def repair_answer_pages(
        self,
        image_paths: list[Path],
        page_nos: list[int],
        question_numbers: list[int],
        previous_items: list[dict[str, Any]],
    ) -> tuple[VisionAnswerPage, dict[str, Any]]:
        prompt = json.dumps(
            {
                "task": "Repair answer transcription using all adjacent source pages.",
                "required_question_numbers": question_numbers,
                "previous_items": previous_items,
                "rules": [
                    "Return every required question number exactly once.",
                    "Use only source text; do not invent missing content.",
                    "Join an answer or explanation split across page boundaries.",
                    "Map an ordered answer sequence to an ascending printed number range.",
                    "Each morning question has exactly one answer key from A through D.",
                ],
                "output_shape": {
                    "answers": [
                        {
                            "question_no": "<required integer>",
                            "correct_option_keys": ["A"],
                            "explanation_markdown": "<visible explanation or null>",
                        }
                    ]
                },
            },
            ensure_ascii=False,
        )
        messages = [
            {
                "role": "system",
                "content": "You repair exam answers from adjacent pages. Return JSON only.",
            },
            {
                "role": "user",
                "content": self._repair_content(prompt, image_paths, page_nos),
            },
        ]
        return await self._request_json(
            model=self.vision_model, messages=messages, schema=VisionAnswerPage
        )

    async def classify_questions(
        self, items: list[dict[str, Any]], catalog: list[dict[str, Any]]
    ) -> tuple[AIClassificationBatch, dict[str, Any]]:
        prompt_items = [
            {key: value for key, value in item.items() if key != "asset_image_paths"}
            for item in items
        ]
        prompt = {
            "task": (
                "为每道题从给定知识树叶子考点中选择最多3个主知识点候选，并给出最多3个关联考点。"
                "只能返回 catalog 中存在的 code，禁止创造分类。主候选按匹配度降序。组合题保留完整题干，请依据当前 question_no、选项及对应解析分类，不要把其他小问的考点混入本小问。"
            ),
            "output_schema": {
                "results": [
                    {
                        "question_no": 1,
                        "primary_candidates": [
                            {"code": "existing.code", "confidence": 0.9, "rationale": "简短理由"}
                        ],
                        "related_candidates": [],
                    }
                ]
            },
            "catalog": catalog,
            "questions": prompt_items,
        }
        content: list[dict[str, Any]] = [
            {"type": "text", "text": json.dumps(prompt, ensure_ascii=False)}
        ]
        for item in items:
            for index, raw_path in enumerate(item.get("asset_image_paths", []), 1):
                content.append(
                    {
                        "type": "text",
                        "text": f"题号 {item['question_no']} 的视觉素材 {index}：",
                    }
                )
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": self._image_url(Path(raw_path))},
                    }
                )
        messages = [
            {
                "role": "system",
                "content": "你是软件设计师考试知识点分类器，只输出严格 JSON。",
            },
            {"role": "user", "content": content},
        ]
        return await self._request_json(
            model=self.classification_model,
            messages=messages,
            schema=AIClassificationBatch,
        )
