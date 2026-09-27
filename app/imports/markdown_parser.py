"""Deterministic extraction. Ambiguous blocks remain reviewable source records."""
from __future__ import annotations

import re
from typing import Literal
from pydantic import BaseModel, Field

VERSION = "markdown-v3"
HEADER = re.compile(r"^[ \t]*(?:#{1,6}\s*)?(\d{1,4})(?:\s*([-~～—至.．])\s*(\d{1,4}))?\s*[、.．：:]\s*(.*)$")
OPTION = re.compile(r"(?<![A-Za-z0-9_.])([ABCD])(?:[ \t]*[、.．:：][ \t]*|[ \t]+)")
ANSWER = re.compile(r"(?:参考|正确)?答案\s*[:：]")
EXPLANATION = re.compile(r"(?:答案)?解析\s*[:：;；]")
IMAGE = re.compile(r"!\[([^\]]*)\]\(([^\n]+?)\)")
STANDALONE_IMAGE = re.compile(r"(?m)^[ \t]*(!\[[^\]]*\]\([^\n]+?\))[ \t]*$")


class Option(BaseModel):
    key: Literal["A", "B", "C", "D"]
    content_markdown: str


class Part(BaseModel):
    question_no: int = Field(gt=0)
    stem_markdown: str = ""
    options: list[Option] = Field(default_factory=list)
    correct_option_keys: list[str] = Field(default_factory=list)
    explanation_markdown: str | None = None


class Unit(BaseModel):
    source_label: str
    material_markdown: str = ""
    explanation_markdown: str | None = None
    parts: list[Part] = Field(default_factory=list)
    raw: str = ""
    line_start: int = 1
    line_end: int = 1
    issues: list[str] = Field(default_factory=list)
    method: str = "rules"


def _options(text: str) -> tuple[str, list[list[dict[str, str]]]]:
    # Explicit subquestion labels belong to the option block, not the option text.
    text = re.sub(r"(?m)^\s*(?:第\s*)?\d+\s*(?:题选项\s*[:：]|[、.．]\s*(?=A[、.．\s]))", "", text)
    marks = []
    image_spans = [m.span() for m in IMAGE.finditer(text)]
    for mark in OPTION.finditer(text):
        if any(a <= mark.start() < b for a, b in image_spans):
            continue
        prefix = text[text.rfind('\n', 0, mark.start())+1:mark.start()]
        explicit = bool(re.search(r'[、.．:：]', mark[0]))
        if explicit or not prefix.strip() or prefix.endswith('  '):
            marks.append(mark)
    first = None
    for i, mark in enumerate(marks):
        prefix = text[text.rfind('\n', 0, mark.start())+1:mark.start()]
        if mark[1] != 'A':
            continue
        if prefix.strip() and '、' in mark[0]:
            continue
        line_end = text.find('\n', mark.end())
        line_end = len(text) if line_end < 0 else line_end
        explicit = bool(re.search(r'[、.．:：]', mark[0]))
        if explicit or (not prefix.strip() and [m[1] for m in marks[i:i+4]] == list('ABCD')):
            first = i
            break
    if first is None:
        return text.strip(), []
    marks = marks[first:]
    groups: list[list[dict[str, str]]] = []
    current: list[dict[str, str]] = []
    for i, mark in enumerate(marks):
        if mark[1] == "A" and current:
            groups.append(sorted(current, key=lambda o:o['key']))
            current = []
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        current.append({"key": mark[1], "content_markdown": text[mark.end():end].strip()})
    if current:
        groups.append(sorted(current, key=lambda o:o['key']))
    return text[:marks[0].start()].strip(), groups


def split_explanations(value: str | None, numbers: list[int]) -> dict[int, str] | None:
    if len(numbers) == 1:
        return {numbers[0]: value}
    if not value:
        return {}
    marks = list(re.finditer(r"第([一二三四五六七八九十\d]+)问|(?m:^)[ \t]*(\d+)[、.．：:]", value))
    mapped = []
    for mark in marks:
        if mark[1]:
            ordinal = int(mark[1]) if mark[1].isdigit() else '一二三四五六七八九十'.find(mark[1]) + 1
            number = numbers[ordinal - 1] if 1 <= ordinal <= len(numbers) else None
        else:
            number = int(mark[2])
        mapped.append(number)
    if mapped != numbers or value[:marks[0].start()].strip():
        return None
    return {number: value[mark.end():marks[i+1].start() if i+1 < len(marks) else len(value)].strip()
            for i, (number, mark) in enumerate(zip(numbers, marks))}


def part_locator(material: str, number: int) -> dict:
    marker = re.search(r"[（(]\s*" + str(number) + r"\s*[）)]", material)
    return {"question_no": number, "label": f"第 {number} 题", "marker": marker[0] if marker else None}


def parse_block(raw: str, start: int = 1) -> Unit:
    lines = raw.splitlines()
    header = HEADER.match(lines[0]) if lines else None
    if not header:
        return Unit(source_label=f"line-{start}", raw=raw, line_start=start,
                    line_end=start + len(lines) - 1, issues=["无法确定题号，请人工拆分或使用文本辅助"])
    first = int(header[1]); last = int(header[3] or first)
    unit = Unit(source_label=f"{first}-{last}" if last != first else str(first), raw=raw,
                line_start=start, line_end=start + len(lines) - 1)
    if last < first or last - first > 100:
        unit.issues.append("题号范围无效")
        return unit
    body = header[4] + "\n" + "\n".join(lines[1:])
    separate = list(re.finditer(r'(?m)^\s*第\s*(\d+)\s*题\s*$', body))
    if last > first and [int(m[1]) for m in separate] == list(range(first,last+1)):
        unit.material_markdown = body[:separate[0].start()].strip()
        for index, mark in enumerate(separate):
            end = separate[index+1].start() if index+1 < len(separate) else len(body)
            child = parse_block(f'{mark[1]}.\n' + body[mark.end():end])
            unit.parts.extend(child.parts); unit.issues.extend(child.issues)
        return unit
    answer = ANSWER.search(body)
    explanation = EXPLANATION.search(body, answer.end() if answer else 0)
    question_text = body[:answer.start()] if answer else body[:explanation.start()] if explanation else body
    # A diagram separated from the options by a blank line describes the
    # question. An image immediately following an option can be that option's
    # content, so keep it in place.
    before_options, _ = _options(question_text)
    first_option_at = len(before_options)
    standalone = []
    def lift_diagram(match):
        prefix = question_text[:match.start()]
        if match.start() > first_option_at and re.search(r"\n[ \t]*\n[ \t]*$", prefix):
            standalone.append(match.group(1))
            return ""
        return match.group(0)
    question_text = STANDALONE_IMAGE.sub(lift_diagram, question_text)
    answer_text = body[answer.end():explanation.start() if explanation else len(body)].strip() if answer else ""
    answers = re.findall(r"[A-D]", answer_text)
    if re.sub(r"[A-D\s,，、;；.。\d()（）第题:：]", "", answer_text):
        unit.issues.append("答案区域包含非答案内容，请核对")
    shared, option_sets = _options(question_text)
    if standalone:
        shared = (shared + "\n\n" + "\n\n".join(standalone)).strip()
    numbers = list(range(first, last + 1))
    if len(numbers) == 1 and len(option_sets) > 1:
        unit.issues.append("同一题干有多组选项但未明确标注完整题号范围，请确认组合关系")
    if len(option_sets) != len(numbers):
        unit.issues.append(f"题号有 {len(numbers)} 个，但识别到 {len(option_sets)} 组选项")
    if len(answers) != len(numbers):
        unit.issues.append(f"题号有 {len(numbers)} 个，但识别到 {len(answers)} 个答案")
    exp = body[explanation.end():].strip() if explanation else None
    if len(numbers) > 1:
        unit.material_markdown = shared
    explanations = split_explanations(exp, numbers)
    if len(numbers) > 1 and explanations is None:
        unit.explanation_markdown = exp
    for i, number in enumerate(numbers):
        opts = option_sets[i] if i < len(option_sets) else []
        if [o["key"] for o in opts] != list("ABCD") or any(not o["content_markdown"] for o in opts):
            unit.issues.append(f"第 {number} 小问选项不完整或有歧义")
        unit.parts.append(Part(question_no=number, stem_markdown=shared if len(numbers) == 1 else "",
                               options=opts, correct_option_keys=answers[i:i+1], explanation_markdown=(explanations or {}).get(number)))
    return unit


def parse_markdown(text: str) -> list[Unit]:
    text = text.lstrip("\ufeff").replace("\r\n", "\n")
    lines = text.splitlines()
    candidates = []
    fence = False
    for index, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            fence = not fence
        match = HEADER.match(line) if not fence else None
        if match and (match[3] or not re.match(r"A(?:[、.．]|\s)", match[4])):
            candidates.append(index)
    starts = []
    for pos, index in enumerate(candidates):
        end = candidates[pos + 1] if pos + 1 < len(candidates) else len(lines)
        segment = "\n".join(lines[index:end])
        before_answer = ANSWER.split(segment, maxsplit=1)[0]
        # Explanations often contain numbered lists; do not interpret those as questions.
        h = HEADER.match(lines[index])
        _, options = _options(h[4] + '\n' + before_answer.partition('\n')[2])
        if any(set(o['key'] for o in group) == set('ABCD') for group in options) or ANSWER.search(segment) or h[3] is not None:
            starts.append(index)
    if not starts:
        return [Unit(source_label="unresolved", raw=text, line_end=len(lines), issues=["未识别到题目边界"])]
    units = [parse_block("\n".join(lines[i:starts[n+1] if n+1 < len(starts) else len(lines)]), i+1)
             for n, i in enumerate(starts)]
    seen: set[int] = set()
    for unit in units:
        numbers = {p.question_no for p in unit.parts}
        if seen & numbers:
            unit.issues.append("与前面的题目存在重复题号")
        seen.update(numbers)
    return units
