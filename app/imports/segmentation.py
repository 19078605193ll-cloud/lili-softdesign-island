"""Conservative, position backed segmentation of numbered morning questions."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any


NUMBER = re.compile(r"[（(]\s*(\d{1,2})\s*[）)]")
OPTION = re.compile(r"(?<![\w])([A-D])\s*[.．、]")
ANSWER = re.compile(r"参考答案\s*[：:]?\s*([A-D](?:\s*[,，、]?\s*[A-D])*)", re.I)
EXPLANATION = re.compile(r"(?:老师)?解析\s*[】：:]")


def _ref(document_id: str, page_no: int, line: dict[str, Any], role: str) -> dict[str, Any]:
    return {"document_id": document_id, "page_no": page_no, "role": role,
            "score": line.get("score", 1.0),
            "bbox": {name: value for name, value in zip(("x0", "y0", "x1", "y1"), line["bbox"], strict=True)}}


def _add_ref(refs: list[dict[str, Any]], value: dict[str, Any]) -> None:
    if value not in refs:
        refs.append(value)


def _append(existing: str, addition: str) -> str:
    addition = addition.strip()
    return (existing + "\n" + addition).strip() if addition and addition not in existing else existing


def segment_pages(pages: list[dict[str, Any]], document_id: str) -> dict[str, Any]:
    """Return candidates and conflicts; never invent a missing answer or option."""
    questions: dict[int, dict[str, Any]] = {}
    answers: dict[int, dict[str, Any]] = {}
    conflicts: dict[int, list[str]] = defaultdict(list)
    current: int | None = None
    pending_answers: list[int] = []
    last_answered: list[int] = []
    zone = "questions"
    pending_stem = ""
    pending_refs: list[dict[str, Any]] = []
    for page in sorted(pages, key=lambda value: value["page_no"]):
        page_no = int(page["page_no"])
        # Horizontal order matters for A/B and C/D printed on the same row.
        lines = sorted((x for x in page["lines"] if not x.get("furniture")),
                       key=lambda x: (round(x["bbox"][1] / 0.012), x["bbox"][0]))
        previous_y: float | None = None
        for line in lines:
            text = " ".join(str(line["text"]).split())
            if not text:
                continue
            y = line["bbox"][1]
            gap = y - previous_y if previous_y is not None else 0.0
            previous_y = y
            if text.isdigit() and len(text) <= 2:
                continue
            if line.get("ink") == "red":
                if zone == "explanation":
                    for number in last_answered:
                        answer = answers.get(number)
                        if answer:
                            answer["explanation"] = _append(answer["explanation"], text)
                            _add_ref(answer["refs"], _ref(document_id, page_no, line, "answers"))
                continue
            answer_match = ANSWER.search(text)
            if answer_match:
                keys = re.findall(r"[A-D]", answer_match.group(1).upper())
                targets = list(pending_answers or last_answered)
                if len(targets) != len(keys):
                    for number in targets:
                        conflicts[number].append(f"第 {page_no} 页答案数 {len(keys)} 与待配题数 {len(targets)} 不符")
                else:
                    for number, key in zip(targets, keys, strict=True):
                        ref = _ref(document_id, page_no, line, "answers")
                        previous = answers.get(number)
                        if previous and previous["keys"] != [key]:
                            conflicts[number].append(f"第 {page_no} 页答案 {key} 与既有答案冲突")
                        else:
                            answer = answers.setdefault(number, {"keys": [key], "explanation": "", "refs": []})
                            _add_ref(answer["refs"], ref)
                    last_answered = targets
                    if len(targets) > 1:
                        for number in targets:
                            conflicts[number].append(
                                f"第 {page_no} 页为多题共用解析，需核对第 {number} 题的解析范围"
                            )
                pending_answers = []
                zone = "explanation"
                tail = text[answer_match.end():].strip()
                if tail and EXPLANATION.search(tail):
                    for number in last_answered:
                        answers[number]["explanation"] = _append(answers[number]["explanation"], tail)
                continue

            numbers = [int(value) for value in NUMBER.findall(text)]
            numbers = list(dict.fromkeys(number for number in numbers if 1 <= number <= 75))
            # An option label may carry the question number; preserve the current target.
            option_prefix = re.match(r"^\s*[（(]\d{1,2}[）)]\s*(?=[A-D][.．、])", text)
            if option_prefix:
                current = numbers[0]
                text = text[option_prefix.end():].strip()
                numbers = []
                zone = "questions"
            elif numbers and not text.lstrip().startswith(("【", "参考答案")):
                for number in numbers:
                    question = questions.setdefault(number, {"stem": "", "options": {}, "refs": [], "group": None})
                    if pending_stem:
                        question["stem"] = _append(question["stem"], pending_stem)
                        for pending_ref in pending_refs:
                            _add_ref(question["refs"], pending_ref)
                    question["stem"] = _append(question["stem"], text)
                    _add_ref(question["refs"], _ref(document_id, page_no, line, "questions"))
                    if number not in pending_answers:
                        pending_answers.append(number)
                if len(numbers) > 1:
                    label = f"{numbers[0]}-{numbers[-1]}"
                    for number in numbers:
                        questions[number]["group"] = label
                current = numbers[-1]
                pending_stem = ""
                pending_refs = []
                zone = "questions"
                continue

            if zone == "explanation":
                if gap > 0.04 and not OPTION.search(text):
                    # A paragraph after a sizable gap can introduce the next
                    # question before its numbered blank appears.
                    pending_stem = text
                    pending_refs = [_ref(document_id, page_no, line, "questions")]
                    current = None
                    zone = "questions"
                    continue
                if last_answered and not OPTION.search(text):
                    for number in last_answered:
                        answer = answers.get(number)
                        if answer:
                            answer["explanation"] = _append(answer["explanation"], text)
                            _add_ref(answer["refs"], _ref(document_id, page_no, line, "answers"))
                continue
            if current is None:
                pending_stem = _append(pending_stem, text)
                _add_ref(pending_refs, _ref(document_id, page_no, line, "questions"))
                continue
            question = questions.setdefault(current, {"stem": "", "options": {}, "refs": [], "group": None})
            matches = list(OPTION.finditer(text))
            if matches:
                for index, match in enumerate(matches):
                    end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
                    content = text[match.end():end].strip()
                    if content:
                        key = match.group(1)
                        old = question["options"].get(key, "")
                        question["options"][key] = _append(old, content)
                _add_ref(question["refs"], _ref(document_id, page_no, line, "questions"))
            elif question["options"]:
                key = next(reversed(question["options"]))
                question["options"][key] = _append(question["options"][key], text)
                _add_ref(question["refs"], _ref(document_id, page_no, line, "questions"))
            else:
                question["stem"] = _append(question["stem"], text)
                _add_ref(question["refs"], _ref(document_id, page_no, line, "questions"))
    return {"questions": questions, "answers": answers, "conflicts": dict(conflicts)}
