"""Restore learner progress without rewriting historical attempts."""

import uuid

from sqlalchemy import select

from app.h5.models import LearningSession
from app.h5.service import lock
from app.models import Attempt, PracticeQuestion


async def resume_session(session, user_id, payload, rows, title):
    await lock(
        session,
        f"resume:{user_id}:{payload.subject_id}:{payload.source}:{payload.source_id}",
    )
    ids = [str(u.id) for u, _ in rows]
    candidates = list(
        await session.scalars(
            select(LearningSession)
            .where(
                LearningSession.user_id == user_id,
                LearningSession.subject_id == payload.subject_id,
                LearningSession.source == payload.source,
            )
            .order_by(LearningSession.updated_at.desc(), LearningSession.id.desc())
        )
    )
    matching = []
    for item in candidates:
        if item.source_id == payload.source_id:
            matching.append(item)
        elif item.source_id is None and item.question_ids and payload.source == "paper":
            units = list(
                await session.scalars(
                    select(PracticeQuestion).where(
                        PracticeQuestion.id.in_(
                            [uuid.UUID(q) for q in item.question_ids]
                        )
                    )
                )
            )
            if len(units) == len(item.question_ids) and all(
                u.paper_id == payload.source_id for u in units
            ):
                item.source_id = payload.source_id
                matching.append(item)
    meaningful = [s for s in matching if s.attempts or s.drafts or s.position != 0]
    row = next(iter(meaningful or matching), None)
    if row is None:
        row = LearningSession(
            user_id=user_id,
            subject_id=payload.subject_id,
            source=payload.source,
            source_id=payload.source_id,
            title=title[:200],
            question_ids=ids,
            position=0,
            drafts={},
            attempts={},
        )
        session.add(row)
    # Keep the original ordering/snapshots, append newly published questions only.
    original = list(row.question_ids)
    row.question_ids = original + [q for q in ids if q not in original]
    history = list(
        await session.scalars(
            select(Attempt)
            .where(
                Attempt.user_id == user_id,
                Attempt.practice_question_id.in_(
                    [uuid.UUID(q) for q in row.question_ids]
                ),
            )
            .order_by(Attempt.created_at.desc(), Attempt.id.desc())
        )
    )
    latest = {}
    for attempt in history:
        latest.setdefault(str(attempt.practice_question_id), str(attempt.id))
    row.attempts = {**row.attempts, **latest}
    drafts = {}
    for item in reversed(matching):
        drafts.update(item.drafts)
    drafts.update(row.drafts)
    row.drafts = {
        q: d
        for q, d in drafts.items()
        if q in row.question_ids and q not in row.attempts
    }
    if not meaningful and history:
        row.position = row.question_ids.index(str(history[0].practice_question_id))
    row.source_id = payload.source_id
    await session.commit()
    return row
