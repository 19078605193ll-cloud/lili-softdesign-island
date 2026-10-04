import uuid
from unittest.mock import AsyncMock
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.models import ExamPaper, PracticeQuestion, User, Job
from app.h5.models import LearningSession, TutorSession
from tests.test_markdown_integration import upload, review
from tests.test_markdown_integration import storage as storage

pytestmark = pytest.mark.integration


async def execute_due(session, jid, factory):
    """Make the isolated fixture due explicitly; host and Docker clocks can differ."""
    from datetime import datetime, timedelta, timezone
    from app.infrastructure.worker import execute
    job = await session.get(Job, jid)
    job.available_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    await session.commit()
    await execute(jid, factory)
    await session.rollback()
    session.expire_all()


async def setup_questions(client, session):
    batch = await upload(client)
    await review(client, session, batch)
    publication = await client.post(f"/api/v1/admin/markdown-batches/{batch}/publish")
    assert publication.status_code == 200, publication.text
    paper = publication.json()["paper_id"]
    units = (await client.get(f"/api/v2/practice/papers/{paper}/questions")).json()[
        "items"
    ]
    subject = str((await session.get(ExamPaper, uuid.UUID(paper))).subject_id)
    return subject, paper, units


async def answer(client, q, choices, sid=None, key=None):
    response = await client.post(
        "/api/v2/learning/attempts",
        headers={"Idempotency-Key": key or uuid.uuid4().hex},
        json=dict(
            practice_question_id=q["id"],
            content_version=q["content_version"],
            answers={p["id"]: a for p, a in zip(q["parts"], choices)},
            **({"session_id": sid} if sid else {}),
        ),
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


async def create_practice(client, subject, q, source="question"):
    r = await client.post(
        "/api/v2/learning/sessions",
        json=dict(subject_id=subject, source=source, source_id=q["id"]),
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_resume_merges_history_and_keeps_last_position(client, session, storage):
    subject, paper, units = await setup_questions(client, session)
    body = dict(subject_id=subject, source="paper", source_id=paper)
    old = (await client.post("/api/v2/learning/sessions", json=body)).json()
    q = units[0]
    attempt = await answer(client, q, ["A"] * len(q["parts"]), old["id"])
    await client.patch("/api/v2/learning/sessions/" + old["id"], json={"position": 1})
    # Legacy empty sessions must not win over meaningful progress.
    await client.post("/api/v2/learning/sessions", json=body)
    record = await session.get(LearningSession, uuid.UUID(old["id"]))
    record.source_id = None
    await session.commit()
    resumed = await client.post("/api/v2/learning/sessions", json={**body, "mode": "resume"})
    assert resumed.status_code == 201, resumed.text
    state = resumed.json()
    assert state["id"] == old["id"]
    assert state["position"] == 1
    assert state["attempts"][q["id"]] == attempt["id"]
    await client.patch("/api/v2/learning/sessions/" + state["id"], json={"position": 0})
    again = (await client.post("/api/v2/learning/sessions", json={**body, "mode": "resume"})).json()
    assert again["id"] == state["id"] and again["position"] == 0
    papers = (await client.get("/api/v2/practice/papers?subject_id=" + subject)).json()["items"]
    assert papers[0]["completed"] == 1
    assert papers[0]["resume_position"] == 0
    # A different source remains an independent, unanswered review.
    review_session = await create_practice(client, subject, q, "similar")
    assert not review_session["attempts"]
    # Retiring the paper preserves answered snapshots and skips inaccessible blanks.
    paper_row = await session.get(ExamPaper, uuid.UUID(paper))
    paper_row.status = "retired"
    await session.commit()
    await client.patch('/api/v2/learning/sessions/' + state['id'], json={'position': 1})
    restored = (await client.get('/api/v2/learning/sessions/' + state['id'])).json()
    assert restored['available_positions'] == [0]
    assert restored['position'] == 0 and restored['notice']
    assert restored['attempts'][q['id']] == attempt['id']


async def test_resume_nineteenth_question_and_completed_paper(client, session, storage):
    source = "\n\n".join(f"{i}. 第{i}题\nA.a B.b C.c D.d\n答案：A\n解析：测试解析" for i in range(1, 21))
    batch = await upload(client, source=source)
    await review(client, session, batch)
    publication = await client.post(f"/api/v1/admin/markdown-batches/{batch}/publish")
    assert publication.status_code == 200, publication.text
    paper = publication.json()['paper_id']
    subject = str((await session.get(ExamPaper, uuid.UUID(paper))).subject_id)
    body = {'subject_id': subject, 'source': 'paper', 'source_id': paper, 'mode': 'resume'}
    practice = (await client.post('/api/v2/learning/sessions', json=body)).json()
    assert len(practice['question_ids']) == 20
    sid = practice['id']
    for index, qid in enumerate(practice['question_ids'][:19]):
        await client.patch('/api/v2/learning/sessions/' + sid, json={'position': index})
        q = (await client.get('/api/v2/practice/questions/' + qid)).json()
        await answer(client, q, ['A'], sid)
    resumed = (await client.post('/api/v2/learning/sessions', json=body)).json()
    assert resumed['id'] == sid and resumed['position'] == 18
    assert len(resumed['attempts']) == 19
    await client.patch('/api/v2/learning/sessions/' + sid, json={'position': 7})
    assert (await client.post('/api/v2/learning/sessions', json=body)).json()['position'] == 7
    await client.patch('/api/v2/learning/sessions/' + sid, json={'position': 19})
    q = (await client.get('/api/v2/practice/questions/' + practice['question_ids'][19])).json()
    await answer(client, q, ['A'], sid)
    completed = (await client.post('/api/v2/learning/sessions', json=body)).json()
    assert completed['id'] == sid and len(completed['attempts']) == 20
    assert completed['position'] == 19


async def test_notebook_clear_requires_review_session_and_full_correct(
    client, session, storage
):
    subject, paper, units = await setup_questions(client, session)
    q = next(q for q in units if q["type"] == "composite")
    wrong = await answer(client, q, ["C", "C"])
    assert (
        await client.put(
            f"/api/v2/learning/questions/{q['id']}/marks/hesitant",
            json={"attempt_id": wrong["id"]},
        )
    ).status_code == 200
    await client.put(f"/api/v2/learning/questions/{q['id']}/marks/favorite", json={})
    await answer(client, q, ["A", "B"])
    assert set(
        (await client.get(f"/api/v2/learning/questions/{q['id']}/marks")).json()[
            "items"
        ]
    ) == {"wrong", "hesitant", "favorite"}
    s = await create_practice(client, subject, q, "wrong")
    await answer(client, q, ["A", "C"], s["id"])
    assert (
        "wrong"
        in (await client.get(f"/api/v2/learning/questions/{q['id']}/marks")).json()[
            "items"
        ]
    )
    s = await create_practice(client, subject, q, "hesitant")
    key = uuid.uuid4().hex
    first = await answer(client, q, ["A", "B"], s["id"], key)
    replay = await answer(client, q, ["A", "B"], s["id"], key)
    assert first == replay
    assert (await client.get(f"/api/v2/learning/questions/{q['id']}/marks")).json()[
        "items"
    ] == ["favorite"]
    await answer(client, q, ["C", "C"])
    listing = (
        await client.get(f"/api/v2/learning/notebook?subject_id={subject}&type=wrong")
    ).json()
    assert listing["items"][0]["stem_excerpt"] == "共享材料"
    await client.delete(f"/api/v2/learning/questions/{q['id']}/marks/wrong")
    assert not (
        await client.get(f"/api/v2/learning/notebook?subject_id={subject}&type=wrong")
    ).json()["items"]
    assert (
        await client.get("/api/v2/learning/attempts/" + wrong["id"])
    ).status_code == 200


async def test_h5_drafts_catalog_metrics_versions_ownership(client, session, storage):
    subject, paper, units = await setup_questions(client, session)
    q = units[0]
    s = await create_practice(client, subject, q)
    draft = dict(
        question_id=q["id"],
        content_version=q["content_version"],
        answers={q["parts"][0]["id"]: "A"},
    )
    r = await client.patch("/api/v2/learning/sessions/" + s["id"], json=draft)
    assert r.status_code == 200, r.text
    assert (await client.get("/api/v2/learning/sessions/" + s["id"])).json()["drafts"][
        q["id"]
    ]["answers"] == draft["answers"]
    dashboard = await client.get("/api/v2/learning/dashboard?subject_id=" + subject)
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["total"] == 2 and dashboard.json()["accuracy"] is None
    assert (await client.get("/api/v2/practice/papers?subject_id=" + subject)).json()[
        "items"
    ][0]["total"] == 2
    assert (
        await client.get("/api/v2/learning/chapters?subject_id=" + subject)
    ).status_code == 200
    await answer(client, q, ["A"] * len(q["parts"]))
    unit = await session.get(PracticeQuestion, uuid.UUID(q["id"]))
    unit.content_version += 1
    await session.commit()
    assert (
        await client.patch("/api/v2/learning/sessions/" + s["id"], json=draft)
    ).status_code == 409
    user = User(username="another", password_hash="unusable")
    session.add(user)
    await session.flush()
    record = await session.get(LearningSession, uuid.UUID(s["id"]))
    record.user_id = user.id
    await session.commit()
    assert (await client.get("/api/v2/learning/sessions/" + s["id"])).status_code == 404
    p = await session.get(ExamPaper, uuid.UUID(paper))
    p.status = "retired"
    await session.commit()
    m = (await client.get("/api/v2/learning/dashboard?subject_id=" + subject)).json()
    assert m["total"] == m["completed"] == 0


async def test_preferences_plan_validation_and_empty_search(client, session, storage):
    subject, paper, units = await setup_questions(client, session)
    r = await client.patch(
        "/api/v2/learning/preferences",
        json=dict(subject_id=subject, motto="一步一步来"),
    )
    assert r.json()["motto"] == "一步一步来"
    assert (
        await client.put(
            "/api/v2/learning/exam-plan",
            json=dict(
                subject_id=subject,
                title="统考",
                start_date="2026-12-01",
                exam_date="2026-01-01",
            ),
        )
    ).status_code == 422
    r = await client.put(
        "/api/v2/learning/exam-plan",
        json=dict(
            subject_id=subject,
            title="统考",
            start_date="2020-01-01",
            exam_date="2020-06-01",
        ),
    )
    assert r.status_code == 200, r.text
    assert r.json()["plan"]["progress"] == 100
    assert (
        await client.get(
            "/api/v2/practice/search?subject_id=" + subject + "&q=不存在内容"
        )
    ).json()["items"] == []


async def test_tutor_exhausted_chain_allows_manual_retry(
    client, session, storage, monkeypatch
):
    from app.h5.ai_worker import LearningModelsExhausted
    from app.config import get_settings

    _, _, units = await setup_questions(client, session)
    monkeypatch.setattr(get_settings(), "ai_api_key", "test-only")
    monkeypatch.setattr(get_settings(), "ai_tutor_model", "test-only")
    mock = AsyncMock(side_effect=LearningModelsExhausted(timed_out=True))
    monkeypatch.setattr("app.h5.ai_worker.completion", mock)
    response = await client.post(
        "/api/v2/learning/tutor/sessions",
        json=dict(question_id=units[0]["id"], content_version=1),
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert response.status_code == 201
    sid, jid = response.json()["id"], uuid.UUID(response.json()["job_id"])
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    await execute_due(session, jid, factory)
    data = (await client.get("/api/v2/learning/tutor/sessions/" + sid)).json()
    assert data["job"]["status"] == "failed"
    assert data["job"]["retries"] == 0
    assert "模型响应超时" in data["job"]["error"]
    assert not any(m["role"] == "assistant" for m in data["messages"])
    assert mock.await_count == 1
    mock.side_effect = None
    mock.return_value = "恢复后的回答"
    response = await client.post(
        f"/api/v2/learning/tutor/sessions/{sid}/messages",
        json={"text": "请重新回答上一条问题。"},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert response.status_code == 202
    await execute_due(session, uuid.UUID(response.json()["job_id"]), factory)
    data = (await client.get("/api/v2/learning/tutor/sessions/" + sid)).json()
    assert data["job"]["status"] == "succeeded"
    assert data["messages"][-1]["content"] == "恢复后的回答"


async def test_tutor_durable_output_idempotency_and_private_jobs(
    client, session, storage, monkeypatch
):
    subject, paper, units = await setup_questions(client, session)
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "ai_api_key", "test-only")
    monkeypatch.setattr(get_settings(), "ai_tutor_model", "test-only")
    mock = AsyncMock(return_value="先判断关键条件是什么？")
    monkeypatch.setattr("app.h5.ai_worker.completion", mock)
    key = uuid.uuid4().hex
    payload = dict(question_id=units[0]["id"], content_version=1)
    r = await client.post(
        "/api/v2/learning/tutor/sessions",
        json=payload,
        headers={"Idempotency-Key": key},
    )
    assert r.status_code == 201, r.text
    again = await client.post(
        "/api/v2/learning/tutor/sessions",
        json=payload,
        headers={"Idempotency-Key": key},
    )
    assert r.json()["id"] == again.json()["id"]
    jid = uuid.UUID(r.json()["job_id"])
    assert (await client.get("/api/v2/admin/jobs/" + str(jid))).status_code == 404
    await session.commit()
    from app.infrastructure.worker import execute

    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    await execute_due(session, jid, factory)
    await session.rollback()
    session.expire_all()
    data = (
        await client.get("/api/v2/learning/tutor/sessions/" + r.json()["id"])
    ).json()
    assert data["job"]["status"] == "succeeded", data
    assert data["messages"][-1]["content"] == "先判断关键条件是什么？"
    assert mock.await_count == 1
    other = User(username="other-tutor", password_hash="unusable")
    session.add(other)
    await session.flush()
    tutor = await session.get(TutorSession, uuid.UUID(r.json()["id"]))
    tutor.user_id = other.id
    job = await session.get(Job, jid)
    job.user_id = other.id
    await session.commit()
    assert (
        await client.get("/api/v2/learning/tutor/sessions/" + r.json()["id"])
    ).status_code == 404
    assert (
        await client.get("/api/v2/learning/tutor/jobs/" + str(jid))
    ).status_code == 404


@pytest.mark.parametrize("rejected_first", [False, True])
async def test_variant_pool_validation_reuse_and_no_formal_statistics(
    client, session, storage, monkeypatch, rejected_first
):
    subject, paper, units = await setup_questions(client, session)
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "ai_api_key", "test-only")
    monkeypatch.setattr(get_settings(), "ai_tutor_model", "test-only")
    content = dict(
        stem="二进制0010左移一位的结果是什么？",
        options={"A": "0100", "B": "0001", "C": "0011", "D": "0000"},
        answer="A",
        explanation="每位向左移动一位，低位补零，得到0100。",
    )
    async def generated(*args, metadata=None, **kwargs):
        metadata["model"] = "fallback-model"
        return content
    generate = AsyncMock(side_effect=generated)
    monkeypatch.setattr("app.h5.ai_worker.completion", generate)
    validations = ([{"valid": False, "answer": "B"}] if rejected_first else [])
    validations.append({"valid": True, "answer": "A", "reason": "verified"})
    monkeypatch.setattr(
        "app.h5.ai_worker.validate_variant",
        AsyncMock(side_effect=validations),
    )
    r = await client.post(
        "/api/v2/learning/variants",
        json={"question_id": units[0]["id"]},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert r.status_code == 202, r.text
    wait_id = uuid.UUID(r.json()["job_id"])
    waiting = await session.get(Job, wait_id)
    generated_id = uuid.UUID(waiting.payload["shared_job_id"])
    await session.commit()
    from app.infrastructure.worker import execute

    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    await execute_due(session, generated_id, factory)
    await execute_due(session, wait_id, factory)
    await session.rollback()
    session.expire_all()
    data = (await client.get("/api/v2/learning/tutor/jobs/" + str(wait_id))).json()
    assert data["status"] == "succeeded", data
    vid = data["result"]["variant_id"]
    assert generate.await_count == (2 if rejected_first else 1)
    from app.h5.models import Variant
    assert (await session.get(Variant, uuid.UUID(vid))).model == "fallback-model"
    assert (
        "answer"
        not in (await client.get("/api/v2/learning/variants/" + vid)).json()["variant"]
    )
    reused = await client.post(
        "/api/v2/learning/variants",
        json={"question_id": units[0]["id"]},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert reused.json()["cache_hit"] is True
    answered = await client.post(
        "/api/v2/learning/variants/" + vid + "/answers", json={"answer": "A"}
    )
    assert answered.json()["result"]["correct"] is True
    metrics = (
        await client.get("/api/v2/learning/dashboard?subject_id=" + subject)
    ).json()
    assert metrics["completed"] == 0 and metrics["accuracy"] is None
    report = await client.post(
        "/api/v2/learning/variants/" + vid + "/reports", json={"reason": "请检查题目"}
    )
    assert report.status_code == 202
    assert (await client.get("/api/v2/learning/variants/" + vid)).status_code == 404


async def test_related_priority_resume_cache_generation_and_ownership(client, session, storage, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.config import get_settings
    from app.h5.models import VariantAnswer
    subject, paper, units = await setup_questions(client, session)
    parent = await create_practice(client, subject, units[0])
    url = f"/api/v2/practice/questions/{units[0]['id']}/related-practice"
    body = {"session_id": parent["id"]}
    key = uuid.uuid4().hex
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_api_key", "")
    # Database real-question hit works without AI configuration.
    found = await client.post(url, json=body, headers={"Idempotency-Key": key})
    assert found.status_code == 200, found.text
    assert found.json()["kind"] == "question"
    again = await client.post(url, json=body, headers={"Idempotency-Key": uuid.uuid4().hex})
    assert again.json() == found.json()
    target = (await client.get('/api/v2/learning/sessions/' + found.json()['session_id'])).json()
    assert target['question_ids'] == [units[1]['id']]
    await answer(client, units[1], ['C'], target['id'])
    # The original key still refers to its original result, even after answering.
    assert (await client.post(url, json=body, headers={"Idempotency-Key": key})).json() == found.json()
    monkeypatch.setattr(settings, "ai_api_key", "test-only")
    monkeypatch.setattr(settings, "ai_tutor_model", "test-only")
    mock = AsyncMock(return_value=dict(stem="测试变式：1+1？", options={"A": "2", "B": "3", "C": "4", "D": "5"}, answer="A", explanation="两单位相加。"))
    async def generated(*args, metadata=None, **kwargs):
        metadata["model"] = "fallback-model"
        return mock.return_value
    mock.side_effect = generated
    monkeypatch.setattr("app.h5.ai_worker.completion", mock)
    monkeypatch.setattr("app.h5.ai_worker.validate_variant", AsyncMock(return_value={"valid": True, "answer": "A"}))
    pending = await client.post(url, json=body, headers={"Idempotency-Key": uuid.uuid4().hex})
    assert pending.status_code == 200, pending.text
    assert pending.json()['kind'] == 'job'
    assert (await client.post(url, json=body, headers={"Idempotency-Key": uuid.uuid4().hex})).json() == pending.json()
    wait_id = uuid.UUID(pending.json()['job_id'])
    job = await session.get(Job, wait_id)
    generation_id = uuid.UUID(job.payload['shared_job_id'])
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    await execute_due(session, generation_id, factory)
    await execute_due(session, wait_id, factory)
    job = (await client.get('/api/v2/learning/tutor/jobs/' + str(wait_id))).json()
    assert job['status'] == 'succeeded', job
    vid = job['result']['variant_id']
    assert mock.await_count == 1
    monkeypatch.setattr(settings, "ai_api_key", "")
    cached = await client.post('/api/v2/learning/variants', json={'question_id': units[0]['id']}, headers={"Idempotency-Key": uuid.uuid4().hex})
    assert cached.json()['variant']['id'] == vid, cached.text
    assert cached.json()['cache_hit'] is True
    # The unified entry also resumes the already generated, unanswered question.
    resumed = await client.post(url, json=body, headers={"Idempotency-Key": uuid.uuid4().hex})
    assert resumed.json() == pending.json()
    await client.post('/api/v2/learning/variants/' + vid + '/answers', json={'answer': 'A'})
    from sqlalchemy import select
    answered = await session.scalar(select(VariantAnswer).where(VariantAnswer.variant_id == uuid.UUID(vid)))
    answered.updated_at = datetime.now(timezone.utc) - timedelta(days=90)
    await session.commit()
    unavailable = await client.post(url, json=body, headers={"Idempotency-Key": uuid.uuid4().hex})
    assert unavailable.status_code == 503  # Even old answers are excluded, requiring generation.
    monkeypatch.setattr(settings, "ai_api_key", "test-only")
    new = await client.post(url, json=body, headers={"Idempotency-Key": uuid.uuid4().hex})
    assert new.json()['kind'] == 'job' and new.json()['job_id'] != str(wait_id), new.text
    other = User(username='related-other', password_hash='unusable')
    session.add(other)
    await session.flush()
    owned_parent = await session.get(LearningSession, uuid.UUID(parent['id']))
    owned_parent.user_id = other.id
    await session.commit()
    assert (await client.post(url, json=body, headers={"Idempotency-Key": key})).status_code == 404


def test_learner_render_table_math_and_resource_boundary():
    from app.imports.markdown_render import render_markdown

    asset = str(uuid.uuid4())
    attempt = str(uuid.uuid4())
    value = f'<table><tr><td colspan="2">合并</td></tr></table><img src="/api/v1/question-assets/{asset}" onerror="alert(1)"><img src="/api/v1/admin/question-assets/{asset}"><script>alert(1)</script>\n\n$x^2$'
    rendered = render_markdown(value, audience="learner")
    assert "<table>" in rendered and 'colspan="2"' in rendered
    assert "/api/v1/question-assets/" in rendered
    assert (
        "/admin/" not in rendered
        and "onerror" not in rendered
        and "<script" not in rendered
    )
    assert "<math" in rendered
    assert f"/attempts/{attempt}/assets/{asset}" in render_markdown(
        f"![题图](/api/v2/learning/attempts/{attempt}/assets/{asset})",
        audience="learner",
    )
