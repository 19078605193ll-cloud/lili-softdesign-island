import uuid
from collections import defaultdict
from datetime import datetime, timezone
from sqlalchemy import select, text
from sqlalchemy.orm import load_only
from app.core.errors import fail
from app.models import (
    ExamPaper,
    KnowledgeNode,
    QuestionKnowledgeAssignment,
    PracticeQuestion,
    PracticeQuestionPart,
    Attempt,
    AttemptPart,
)
from app.practice.service import visible
from app.h5.models import Mark, LearningSession


async def lock(session, key):
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": key}
    )


async def owned(session, model, row_id, user_id, *, write=False):
    query = select(model).where(model.id == row_id, model.user_id == user_id)
    if write:
        query = query.with_for_update()
    row = await session.scalar(query)
    if not row:
        raise fail(404, "NOT_FOUND", "记录不存在")
    return row


async def catalog(session, subject_id):
    return list(
        (
            await session.execute(
                select(PracticeQuestion, ExamPaper)
                .join(ExamPaper)
                .where(ExamPaper.subject_id == subject_id, *visible())
                .order_by(
                    ExamPaper.year.desc(),
                    ExamPaper.id,
                    PracticeQuestion.source_label,
                    PracticeQuestion.id,
                )
            )
        ).all()
    )


async def nodes(session, subject_id):
    return list(
        await session.scalars(
            select(KnowledgeNode)
            .where(
                KnowledgeNode.subject_id == subject_id, KnowledgeNode.status == "active"
            )
            .order_by(KnowledgeNode.sort_order, KnowledgeNode.code)
        )
    )


def descendants(node_id, tree):
    found = {node_id}
    while True:
        expanded = found | {n.id for n in tree if n.parent_id in found}
        if found == expanded:
            return found
        found = expanded


async def node_units(session, node_id, subject_id):
    ids = descendants(node_id, await nodes(session, subject_id))
    return set(
        await session.scalars(
            select(PracticeQuestionPart.practice_question_id)
            .join(
                QuestionKnowledgeAssignment,
                QuestionKnowledgeAssignment.question_id
                == PracticeQuestionPart.question_id,
            )
            .where(
                QuestionKnowledgeAssignment.knowledge_node_id.in_(ids),
                QuestionKnowledgeAssignment.role == "primary",
                QuestionKnowledgeAssignment.status == "confirmed",
            )
        )
    )


async def set_mark(session, user_id, question_id, kind, active, attempt_id=None):
    await lock(session, f"mark:{user_id}:{question_id}")
    mark = await session.get(Mark, (user_id, question_id, kind))
    if mark is None:
        mark = Mark(user_id=user_id, question_id=question_id, kind=kind)
        session.add(mark)
    mark.active = active
    mark.resolved_at = None if active else datetime.now(timezone.utc)
    if attempt_id:
        mark.attempt_id = attempt_id
    mark.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return mark


async def apply_attempt(session, user_id, attempt, parts, session_id):
    practice = None
    if session_id:
        practice = await owned(
            session, LearningSession, session_id, user_id, write=True
        )
        if str(attempt.practice_question_id) not in practice.question_ids:
            raise fail(422, "SESSION_QUESTION", "题目不属于本次练习")
        old = practice.attempts.get(str(attempt.practice_question_id))
        if old and old != str(attempt.id):
            raise fail(409, "ALREADY_ANSWERED", "本次练习已经提交，请开始新的练习")
        practice.attempts = {
            **practice.attempts,
            str(attempt.practice_question_id): str(attempt.id),
        }
        practice.drafts = {
            k: v
            for k, v in practice.drafts.items()
            if k != str(attempt.practice_question_id)
        }
    if not all(p["correct"] for p in parts):
        await set_mark(
            session, user_id, attempt.practice_question_id, "wrong", True, attempt.id
        )
    elif practice and practice.source in {"wrong", "hesitant"}:
        for kind in ("wrong", "hesitant"):
            await set_mark(
                session, user_id, attempt.practice_question_id, kind, False, attempt.id
            )


async def metrics(session, user_id, subject_id):
    units = await catalog(session, subject_id)
    versions = {u.id: u.content_version for u, _ in units}
    attempts = list(
        await session.scalars(
            select(Attempt).options(load_only(Attempt.id, Attempt.practice_question_id, Attempt.content_version, Attempt.created_at))
            .join(PracticeQuestion)
            .join(ExamPaper)
            .where(Attempt.user_id == user_id, ExamPaper.subject_id == subject_id)
            .order_by(Attempt.created_at.desc(), Attempt.id.desc())
        )
    )
    amap = {a.id: a for a in attempts}
    parts = (
        list(
            await session.scalars(
                select(AttemptPart).where(AttemptPart.attempt_id.in_(amap))
            )
        )
        if amap
        else []
    )
    latest = {}
    by_attempt = defaultdict(list)
    for part in parts:
        by_attempt[part.attempt_id].append(part)
    for a in attempts:
        if versions.get(a.practice_question_id) != a.content_version:
            continue
        for p in by_attempt[a.id]:
            latest.setdefault(p.question_id, p)
    active = set(
        await session.scalars(
            select(Mark.question_id).where(
                Mark.user_id == user_id,
                Mark.active.is_(True),
                Mark.kind.in_(["wrong", "hesitant"]),
            )
        )
    )
    assignments = list(
        (
            await session.execute(
                select(QuestionKnowledgeAssignment, PracticeQuestionPart)
                .join(
                    PracticeQuestionPart,
                    PracticeQuestionPart.question_id
                    == QuestionKnowledgeAssignment.question_id,
                )
                .where(
                    PracticeQuestionPart.practice_question_id.in_(versions),
                    QuestionKnowledgeAssignment.status == "confirmed",
                    QuestionKnowledgeAssignment.role == "primary",
                )
            )
        ).all()
    )
    tree = await nodes(session, subject_id)
    parent_ids = {n.parent_id for n in tree}
    leaf_ids = {n.id for n in tree if n.id not in parent_ids}
    questions = defaultdict(set)
    blocked = set()
    for a, member in assignments:
        result = latest.get(a.question_id)
        if result and not any(
            k.get("role") == "primary"
            and k.get("node_id") == str(a.knowledge_node_id)
            and k.get("release_id") == str(a.taxonomy_release_id)
            for k in result.knowledge
        ):
            latest.pop(a.question_id, None)
        if a.knowledge_node_id in leaf_ids:
            questions[a.knowledge_node_id].add(a.question_id)
            if member.practice_question_id in active:
                blocked.add(a.knowledge_node_id)
    mastered = set()
    sampled = set()
    for nid, qids in questions.items():
        results = [latest[q] for q in qids if q in latest]
        if len(results) >= 3:
            sampled.add(nid)
            if (
                sum(p.correct for p in results) / len(results) >= 0.8
                and nid not in blocked
            ):
                mastered.add(nid)

    def progress(ids):
        eligible = set(questions) & ids
        percent = (
            round(100 * len(mastered & eligible) / len(eligible), 1)
            if eligible
            else None
        )
        practiced = any(q in latest for n in eligible for q in questions[n])
        state = (
            "未练习"
            if not practiced
            else "样本不足"
            if not (sampled & eligible)
            else "基本掌握"
            if percent >= 70
            else "正在提升"
            if percent >= 40
            else "需要加强"
        )
        return dict(mastery=percent, state=state)

    chapters = [
        dict(
            id=str(n.id),
            name=n.name,
            score_share=None,
            sample_label="暂无统计",
            **progress(descendants(n.id, tree)),
        )
        for n in tree
        if n.node_type == "chapter"
    ]
    # Only complete verified papers with matching published score totals qualify as a sample.
    from app.models import Question
    from decimal import Decimal

    score_rows = list(
        (
            await session.execute(
                select(PracticeQuestionPart, Question)
                .join(Question)
                .where(PracticeQuestionPart.practice_question_id.in_(versions))
            )
        ).all()
    )
    paper_for = {u.id: p.id for u, p in units}
    papers = {p.id: p for _, p in units}
    totals = defaultdict(lambda: Decimal("0"))
    qscore = {}
    for member, q in score_rows:
        totals[paper_for[member.practice_question_id]] += q.score
        qscore[q.id] = (paper_for[member.practice_question_id], q.score)
    complete = {
        pid
        for pid, total in totals.items()
        if papers[pid].total_score and total == papers[pid].total_score
    }
    denominator = sum((totals[p] for p in complete), Decimal("0"))
    for chapter in chapters:
        branch = descendants(uuid.UUID(chapter["id"]), tree)
        related = {
            a.question_id for a, _ in assignments if a.knowledge_node_id in branch
        }
        if denominator:
            score = sum(
                (
                    qscore[q][1]
                    for q in related
                    if q in qscore and qscore[q][0] in complete
                ),
                Decimal("0"),
            )
            chapter.update(
                score_share=round(float(score / denominator * 100), 1),
                sample_label=f"{len(complete)}套完整已发布试卷",
            )
    recent_sessions = list(
        await session.scalars(
            select(LearningSession)
            .where(
                LearningSession.user_id == user_id,
                LearningSession.subject_id == subject_id,
            )
            .order_by(LearningSession.updated_at.desc())
            .limit(5)
        )
    )
    recent = []
    from app.h5.paper_state import session_title
    for s in recent_sessions:
        results = [
            p for aid in s.attempts.values() for p in by_attempt.get(uuid.UUID(aid), [])
        ]
        recent.append(
            dict(
                id=str(s.id),
                title=await session_title(session, s),
                count=len(s.attempts),
                accuracy=round(100 * sum(p.correct for p in results) / len(results), 1)
                if results
                else None,
            )
        )
    if not recent and attempts:
        recent = [
            dict(
                id=None,
                title="历史练习",
                count=len({a.practice_question_id for a in attempts}),
                accuracy=round(100 * sum(p.correct for p in parts) / len(parts), 1)
                if parts
                else None,
            )
        ]
    return dict(
        rule_version="h5-v1",
        total=len(versions),
        completed=len({a.practice_question_id for a in attempts} & set(versions)),
        accuracy=round(100 * sum(p.correct for p in parts) / len(parts), 1)
        if parts
        else None,
        **progress(leaf_ids),
        chapters=chapters,
        recent=recent,
    )
