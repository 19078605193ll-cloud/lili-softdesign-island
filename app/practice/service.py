from collections import defaultdict
from decimal import Decimal
import re
import uuid

from sqlalchemy import exists, select
from app.models import (
    ExamPaper,
    PracticeQuestion,
    PracticeQuestionPart,
    Question,
    QuestionOption,
    QuestionCorrectOption,
    QuestionGroup,
)
from app.core.errors import fail
from app.imports.markdown_parser import part_locator


def visible():
    members = (
        select(1)
        .select_from(PracticeQuestionPart)
        .where(PracticeQuestionPart.practice_question_id == PracticeQuestion.id)
    )
    hidden = (
        select(1)
        .select_from(PracticeQuestionPart)
        .join(Question)
        .where(
            PracticeQuestionPart.practice_question_id == PracticeQuestion.id,
            Question.status != "published",
        )
    )
    return (ExamPaper.status == "verified", exists(members), ~exists(hidden))


def urls(value):
    return re.sub(
        r"asset://([0-9a-fA-F-]{36})", r"/api/v1/question-assets/\1", value or ""
    )


async def load(session, ids: list[uuid.UUID], *, answers=False):
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(PracticeQuestion, ExamPaper)
            .join(ExamPaper)
            .where(PracticeQuestion.id.in_(ids), *visible())
        )
    ).all()
    units = {u.id: (u, p) for u, p in rows}
    members = (
        await session.execute(
            select(PracticeQuestionPart, Question)
            .join(Question)
            .where(PracticeQuestionPart.practice_question_id.in_(units))
            .order_by(PracticeQuestionPart.position)
        )
    ).all()
    qids = [q.id for _, q in members]
    options = defaultdict(list)
    for option in await session.scalars(
        select(QuestionOption)
        .where(QuestionOption.question_id.in_(qids))
        .order_by(QuestionOption.sort_order)
    ):
        options[option.question_id].append(option)
    correct = defaultdict(set)
    if answers:
        for qid, oid in await session.execute(
            select(
                QuestionCorrectOption.question_id, QuestionCorrectOption.option_id
            ).where(QuestionCorrectOption.question_id.in_(qids))
        ):
            correct[qid].add(oid)
    groups = {
        g.id: g
        for g in await session.scalars(
            select(QuestionGroup).where(
                QuestionGroup.id.in_([u.group_id for u, _ in rows if u.group_id])
            )
        )
    }
    parts = defaultdict(list)
    sources: dict[uuid.UUID, str] = {}
    for member, question in members:
        sources.setdefault(member.practice_question_id, question.source_type)
        part = dict(
            id=str(question.id),
            question_no=question.question_no,
            stem_markdown=urls(question.stem_markdown),
            options=[
                dict(key=o.option_key, content_markdown=urls(o.content_markdown))
                for o in options[question.id]
            ],
            score=str(question.score),
        )
        if answers:
            part.update(
                correct_option_keys=[
                    o.option_key
                    for o in options[question.id]
                    if o.id in correct[question.id]
                ],
                explanation_markdown=urls(question.explanation_markdown),
            )
        parts[member.practice_question_id].append(part)
    result = {}
    for uid, (unit, paper) in units.items():
        group = groups.get(unit.group_id)
        material = urls(group.material_markdown if group else "")
        for part in parts[uid]:
            part["locator"] = part_locator(material, part["question_no"])
        result[uid] = dict(
            id=str(uid),
            content_version=unit.content_version,
            type="composite" if len(parts[uid]) > 1 else "single_choice",
            material_markdown=material,
            parts=parts[uid],
            subquestion_count=len(parts[uid]),
            source=dict(
                paper_id=str(paper.id),
                year=paper.year,
                period=paper.period,
                batch_code=paper.batch_code,
                title=paper.title,
                source_type=sources[uid],
                label=unit.source_label,
            ),
        )
        if answers:
            result[uid]["explanation_markdown"] = urls(
                group.explanation_markdown if group else None
            )
    return result


def grade(question: dict, answers: dict[str, str]) -> tuple[list[dict], Decimal]:
    if set(answers) != {p["id"] for p in question["parts"]}:
        raise fail(422, "INCOMPLETE_ANSWER", "请一次提交全部小问")
    results = []
    for part in question["parts"]:
        answer = answers[part["id"]].upper()
        if answer not in {o["key"] for o in part["options"]}:
            raise fail(422, "INVALID_OPTION", "选项不存在")
        correct = answer in part["correct_option_keys"]
        results.append(
            dict(
                question_id=part["id"],
                answer=answer,
                correct=correct,
                score=part["score"] if correct else "0",
            )
        )
    return results, sum((Decimal(p["score"]) for p in results), Decimal("0"))
