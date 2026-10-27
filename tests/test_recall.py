import uuid

import pytest
from sqlalchemy import func, select

from app.recall.models import RecallSession, RecallSessionBlank, RecallSubmission
from app.recall.pilots import pilots
from app.recall.schemas import Normalization, VersionIn, normalize
from tests.test_markdown_integration import storage  # noqa: F401 - pytest fixture


@pytest.fixture(autouse=True)
def deterministic_semantic_grading(monkeypatch):
    async def incorrect(**kwargs):
        return False
    monkeypatch.setattr("app.recall.grading.semantic_grade", incorrect)


@pytest.mark.integration
async def test_semantic_grading_failure_retry_and_rule_shortcut(client, session, monkeypatch):
    from unittest.mock import AsyncMock

    from app.config import get_settings
    from app.recall import grading
    monkeypatch.setattr(get_settings(), "recall_semantic_grading_enabled", True)
    row = next(r for r in await seed(client) if r["type"] == "sentence")
    await publish(client, row)
    detail = (await client.get("/api/v2/learning/recalls/" + row["id"])).json()
    s = (await client.post("/api/v2/learning/recall-sessions",
                          json={"recall_id": row["id"]})).json()
    url = "/api/v2/learning/recall-sessions/" + s["id"]
    body = {"revision": s["revision"], "blank_key": s["current_key"], "answer": "未收录的等价表述"}
    headers = {"Idempotency-Key": uuid.uuid4().hex}
    judge = AsyncMock(side_effect=grading.GradingUnavailable("timeout"))
    monkeypatch.setattr(grading, "semantic_grade", judge)
    before = (await client.get(url)).json()
    failed = await client.post(url + "/answers", json=body, headers=headers)
    assert failed.status_code == 503
    assert failed.headers["X-Error-Code"] == "RECALL_GRADING_UNAVAILABLE"
    assert (await client.get(url)).json() == before
    assert await session.scalar(select(func.count()).select_from(RecallSubmission)) == 0

    judge.side_effect = None
    judge.return_value = True
    success = await client.post(url + "/answers", json=body, headers=headers)
    assert success.status_code == 200, success.text
    result = success.json()
    assert result["feedback"]["correct"] is True
    assert result["session"]["slot_presentations"][body["blank_key"]] == {
        "value": body["answer"], "outcome": "correct",
    }
    repeated = await client.post(url + "/answers", json=body, headers=headers)
    assert repeated.json() == result
    assert judge.await_count == 2  # failed attempt and successful retry only
    assert await session.scalar(select(func.count()).select_from(RecallSubmission)) == 1

    s = result["session"]
    reference = next(b["answer"] for b in detail["blanks"] if b["key"] == s["current_key"])
    direct = await client.post(url + "/answers", headers={"Idempotency-Key": uuid.uuid4().hex},
                               json={"revision": s["revision"], "blank_key": s["current_key"], "answer": reference})
    assert direct.json()["feedback"]["correct"] is True
    assert judge.await_count == 2
    s = direct.json()["session"]
    saved = await client.patch(url, json={"revision": s["revision"], "blank_key": s["current_key"], "answer": "草稿"})
    assert saved.json()["slot_presentations"] == s["slot_presentations"]
    monkeypatch.setattr(get_settings(), "recall_semantic_grading_enabled", False)
    s = saved.json()
    unmatched = await client.post(url + "/answers", headers={"Idempotency-Key": uuid.uuid4().hex},
                                  json={"revision": s["revision"], "blank_key": s["current_key"], "answer": "错误"})
    assert unmatched.json()["feedback"]["correct"] is False
    assert judge.await_count == 2


@pytest.mark.integration
async def test_recall_admin_page_renders(client):
    response = await client.get("/admin/recalls")

    assert response.status_code == 200, response.text
    assert "主动回忆" in response.text
    assert "确认全部补充内容" not in response.text
    assert "逐项核对并填写" not in response.text
    assert "生成时有 ${count} 处内容缺少" in response.text


def test_normalization_preserves_meaning():
    assert normalize(" Ｏ（ｎ） \r\n", Normalization()) == "O(n)"
    assert normalize("n²", Normalization()) != normalize("n2", Normalization())
    assert normalize("X→Y", Normalization()) != normalize("Y→X", Normalization())
    assert normalize("  ATOMICITY ", Normalization(case_sensitive=False)) == "atomicity"
    assert normalize("B,A,A", Normalization(unordered_set=True)) == "A,B"
    assert (
        normalize("X → Y", Normalization(whitespace="remove", aliases={"→": "->"}))
        == "X->Y"
    )


def test_pilots_are_structurally_valid():
    for _, data, codes in pilots():
        VersionIn.model_validate(
            {**data, "knowledge_node_ids": [uuid.uuid4() for _ in codes]}
        )


def table_version(rows):
    return VersionIn.model_validate(
        {
            "title": "格内挖空兼容测试",
            "content": {
                "type": "table",
                "columns": ["知识项", "结论"],
                "rows": rows,
                "note": "",
            },
            "blanks": [
                {
                    "key": "answer_1",
                    "label": "关键结论",
                    "answer": "原子性",
                    "acceptable_answers": [],
                    "normalization": {},
                    "hints": ["考虑事务整体", "全部完成或全部撤销"],
                }
            ],
            "knowledge_node_ids": [uuid.uuid4()],
        }
    )


def test_table_cells_accept_legacy_and_segmented_shapes():
    legacy = table_version([[{"text": "事务"}, {"blank_key": "answer_1"}]])
    segmented = table_version(
        [
            [
                [{"text": "事务"}],
                [
                    {"text": "核心性质是"},
                    {"blank_key": "answer_1"},
                    {"text": "，需要整体理解"},
                ],
            ]
        ]
    )

    assert not isinstance(legacy.content.rows[0][0], list)
    assert len(segmented.content.rows[0][1]) == 3
    assert VersionIn.model_validate(legacy.model_dump()).model_dump() == legacy.model_dump()


def test_table_cells_reject_empty_content():
    with pytest.raises(ValueError, match="表格单元格不能为空"):
        table_version([[[{"text": " "}], [{"blank_key": "answer_1"}]]])


async def seed(client):
    response = await client.post("/api/v2/admin/recalls/seed-pilots")
    assert response.status_code == 200, response.text
    return (await client.get("/api/v2/admin/recalls")).json()["items"]


async def publish(client, row):
    rev = row["revision"]
    for action in ("submit", "approve", "publish"):
        r = await client.post(
            f"/api/v2/admin/recalls/versions/{row['version_id']}/{action}",
            json={"revision": rev},
        )
        assert r.status_code == 200, r.text
        rev = r.json()["revision"]
    return rev


@pytest.mark.integration
@pytest.mark.parametrize("completed", [False, True])
async def test_publish_new_version_deletes_old_learning_only(client, session, completed):
    from app.models import User
    from app.recall import service

    rows = await seed(client)
    row = next(r for r in rows if r["type"] == "sentence")
    other = next(r for r in rows if r["id"] != row["id"])
    revision = await publish(client, row)
    await publish(client, other)
    old = (await client.post("/api/v2/learning/recall-sessions",
                             json={"recall_id": row["id"]})).json()
    url = "/api/v2/learning/recall-sessions/" + old["id"]
    body = {"revision": old["revision"], "blank_key": old["current_key"], "answer": "错误"}
    headers = {"Idempotency-Key": uuid.uuid4().hex}
    result = await client.post(url + "/answers", json=body, headers=headers)
    assert result.status_code == 200, result.text
    old = result.json()["session"]
    saved = await client.patch(url, json={"revision": old["revision"],
                                         "blank_key": old["current_key"], "answer": "旧草稿"})
    assert saved.status_code == 200
    if completed:
        s = await session.get(RecallSession, uuid.UUID(old["id"]))
        s.phase = "completed"
        await session.commit()
    second_user = User(username="second-learner", password_hash="unused")
    session.add(second_user)
    await session.flush()
    second = await service.create_session(session, second_user.id, uuid.UUID(row["id"]))
    second_id = second.id
    await session.commit()
    unaffected = (await client.post("/api/v2/learning/recall-sessions",
                                    json={"recall_id": other["id"]})).json()
    detail = (await client.get("/api/v2/admin/recalls/versions/" + row["version_id"])).json()
    version = {k: detail[k] for k in ("title", "content", "blanks", "source", "provenance", "knowledge_node_ids")}
    version["title"] += " 新版"
    created = await client.post(f"/api/v2/admin/recalls/{row['id']}/versions", json=version,
                                headers={"If-Match": f"{row['version_id']}:{revision}"})
    assert created.status_code == 201, created.text
    new = created.json()
    approved = await client.post(f"/api/v2/admin/recalls/versions/{new['version_id']}/approve",
                                 json={"revision": 1})
    assert approved.status_code == 200, approved.text
    # Editing and approval leave the existing training intact.
    assert (await client.get(url)).status_code == 200
    publish_url = f"/api/v2/admin/recalls/versions/{new['version_id']}/publish"
    failed = await client.post(publish_url, json={"revision": 1})
    assert failed.status_code == 409
    assert (await client.get(url)).status_code == 200
    published = await client.post(publish_url, json={"revision": approved.json()["revision"]})
    assert published.status_code == 200, published.text
    assert (await client.get(url)).status_code == 404
    assert (await client.get("/api/v2/learning/recall-sessions/" + unaffected["id"])).json() == unaffected
    old_ids = [uuid.UUID(old["id"]), second_id]
    assert await session.scalar(select(func.count()).select_from(RecallSession).where(RecallSession.id.in_(old_ids))) == 0
    assert await session.scalar(select(func.count()).select_from(RecallSessionBlank).where(RecallSessionBlank.session_id.in_(old_ids))) == 0
    assert await session.scalar(select(func.count()).select_from(RecallSubmission).where(RecallSubmission.session_id.in_(old_ids))) == 0
    # A cached answer retry cannot bring deleted old feedback back.
    assert (await client.post(url + "/answers", json=body, headers=headers)).status_code == 404
    fresh = (await client.post("/api/v2/learning/recall-sessions", json={"recall_id": row["id"]})).json()
    assert fresh["version_id"] == new["version_id"]
    assert fresh["title"] == version["title"]
    assert fresh["completed"] == 0 and fresh["slot_presentations"] == {}
    assert fresh["current_key"] == fresh["blanks"][0]["key"]
    assert all(b["draft"] == "" and b["historical_error_count"] == 0 and not b["mastered"] for b in fresh["blanks"])


@pytest.mark.integration
async def test_recall_complete_retest_idempotency(client, session):
    rows = await seed(client)
    assert len(rows) == 3
    assert len(await seed(client)) == 3
    row = next(r for r in rows if r["type"] == "sentence")
    assert (
        await client.get("/api/v2/learning/recalls/" + row["id"])
    ).status_code == 404
    await publish(client, row)
    detail = (await client.get("/api/v2/learning/recalls/" + row["id"])).json()
    answers = {b["key"]: b["answer"] for b in detail["blanks"]}
    s = (
        await client.post(
            "/api/v2/learning/recall-sessions", json={"recall_id": row["id"]}
        )
    ).json()
    again = (
        await client.post(
            "/api/v2/learning/recall-sessions", json={"recall_id": row["id"]}
        )
    ).json()
    assert again["id"] == s["id"]
    assert s["slot_presentations"] == {}
    assert all("answer" not in b and "hints" not in b for b in s["blanks"])
    sid, first = s["id"], s["current_key"]

    async def answer(value, key=None):
        nonlocal s
        body = {
            "revision": s["revision"],
            "blank_key": s["current_key"],
            "answer": value,
        }
        headers = {"Idempotency-Key": key or uuid.uuid4().hex}
        r = await client.post(
            f"/api/v2/learning/recall-sessions/{sid}/answers",
            json=body,
            headers=headers,
        )
        assert r.status_code == 200, r.text
        s = r.json()["session"]
        return r, body, headers

    for count in range(1, 4):
        response, body, headers = await answer("错误")
        repeated = await client.post(
            f"/api/v2/learning/recall-sessions/{sid}/answers",
            json=body,
            headers=headers,
        )
        assert repeated.json() == response.json()
        b = next(b for b in s["blanks"] if b["key"] == first)
        assert b["round_error_count"] == b["historical_error_count"] == count
        assert response.json()["feedback"]["answer"] == (
            answers[first] if count == 3 else None
        )
    while s["phase"] == "first":
        await answer(answers[s["current_key"]])
    assert len(s["slot_presentations"]) == 4
    assert s["slot_presentations"][first] == {"value": "错误", "outcome": "incorrect"}
    restored = (await client.get(f"/api/v2/learning/recall-sessions/{sid}")).json()
    assert restored["slot_presentations"] == s["slot_presentations"]
    assert s["phase"] == "retest_ready"
    s = (
        await client.post(
            f"/api/v2/learning/recall-sessions/{sid}/retest",
            json={"revision": s["revision"]},
        )
    ).json()
    b = next(b for b in s["blanks"] if b["key"] == first)
    assert s["slot_presentations"] == {}
    assert (
        b["pass_error_count"] == 0 and b["round_error_count"] == 3 and b["hint"] is None
    )
    await answer("错误")
    await answer(answers[first])
    assert s["phase"] == "retest_ready"
    s = (
        await client.post(
            f"/api/v2/learning/recall-sessions/{sid}/retest",
            json={"revision": s["revision"]},
        )
    ).json()
    await answer(answers[first])
    assert s["phase"] == "completed" and s["completed"] == 4
    assert s["slot_presentations"] == {first: {"value": answers[first], "outcome": "correct"}}
    assert (
        next(b for b in s["blanks"] if b["key"] == first)["historical_error_count"] == 4
    )
    assert await session.scalar(select(func.count()).select_from(RecallSubmission)) == 9


@pytest.mark.integration
async def test_versions_permissions_visibility_and_drafts(client, session):
    row = (await seed(client))[0]
    revision = await publish(client, row)
    d = (await client.get("/api/v2/admin/recalls/versions/" + row["version_id"])).json()
    body = {
        k: d[k]
        for k in (
            "title",
            "content",
            "blanks",
            "source",
            "provenance",
            "knowledge_node_ids",
        )
    }
    body["title"] += " 修订"
    new = await client.post(
        "/api/v2/admin/recalls/" + row["id"] + "/versions",
        json=body,
        headers={"If-Match": f"{row['version_id']}:{revision}"},
    )
    assert new.status_code == 201, new.text
    assert (await client.get("/api/v2/learning/recalls/" + row["id"])).json()[
        "title"
    ] == d["title"]
    stale = await client.post(
        "/api/v2/admin/recalls/" + row["id"] + "/versions",
        json=body,
        headers={"If-Match": f"{row['version_id']}:{revision}"},
    )
    assert stale.status_code == 409
    await client.post(
        "/api/v2/admin/recalls/versions/" + row["version_id"] + "/unpublish",
        json={"revision": revision},
    )
    assert (
        await client.post(
            "/api/v2/learning/recall-sessions", json={"recall_id": row["id"]}
        )
    ).status_code == 404


@pytest.mark.integration
async def test_bad_content_and_cross_subject_rejected(client, session):
    row = (await seed(client))[0]
    d = (await client.get("/api/v2/admin/recalls/versions/" + row["version_id"])).json()
    body = {
        k: d[k]
        for k in (
            "title",
            "content",
            "blanks",
            "source",
            "provenance",
            "knowledge_node_ids",
        )
    }
    body["knowledge_node_ids"] = [str(uuid.uuid4())]
    r = await client.post(
        "/api/v2/admin/recalls/" + row["id"] + "/versions",
        json=body,
        headers={"If-Match": f"{row['version_id']}:1"},
    )
    assert r.status_code == 422


@pytest.mark.integration
async def test_recall_drafts_stale_empty_switch_off_and_isolation(
    client, session, monkeypatch
):
    from app.config import get_settings
    from app.models import User
    from app.recall.models import RecallSession

    row = next(r for r in await seed(client) if r["type"] == "sentence")
    await publish(client, row)
    s = (
        await client.post(
            "/api/v2/learning/recall-sessions", json={"recall_id": row["id"]}
        )
    ).json()
    url = "/api/v2/learning/recall-sessions/" + s["id"]
    b = {
        "revision": s["revision"],
        "blank_key": s["current_key"],
        "answer": "unfinished",
    }
    draft = await client.patch(url, json=b)
    assert draft.status_code == 200
    assert (await client.get(url)).json()["blanks"][0]["draft"] == "unfinished"
    stale = await client.post(
        url + "/answers", json=b, headers={"Idempotency-Key": uuid.uuid4().hex}
    )
    assert stale.status_code == 409
    b["revision"] = draft.json()["revision"]
    b["answer"] = ""
    empty = await client.patch(url, json=b)
    assert empty.status_code == 200
    b["revision"] = empty.json()["revision"]
    b["answer"] = "  "
    assert (
        await client.post(
            url + "/answers", json=b, headers={"Idempotency-Key": uuid.uuid4().hex}
        )
    ).status_code == 422
    assert await session.scalar(select(func.count()).select_from(RecallSubmission)) == 0
    other = User(username="other", password_hash="not-used")
    session.add(other)
    await session.flush()
    owned_row = await session.get(RecallSession, uuid.UUID(s["id"]))
    owned_row.user_id = other.id
    await session.commit()
    assert (await client.get(url)).status_code == 404
    monkeypatch.setattr(get_settings(), "recall_enabled", False)
    assert (
        await client.post(
            "/api/v2/learning/recall-sessions", json={"recall_id": row["id"]}
        )
    ).status_code == 503


@pytest.mark.integration
async def test_related_questions_dedupe_and_metrics(client, session, storage):
    from app.models import (
        ExamPaper,
        KnowledgeNode,
        QuestionKnowledgeAssignment,
    )
    from tests.test_h5 import setup_questions

    subject, paper, units = await setup_questions(client, session)
    row = next(r for r in await seed(client) if r["type"] == "sentence")
    await publish(client, row)
    node = await session.scalar(
        select(KnowledgeNode).where(KnowledgeNode.code == "database.transaction.acid")
    )
    for unit in units:
        for part in unit["parts"]:
            session.add(
                QuestionKnowledgeAssignment(
                    question_id=uuid.UUID(part["id"]),
                    subject_id=uuid.UUID(subject),
                    knowledge_node_id=node.id,
                    role="related",
                    status="confirmed",
                    source="manual",
                )
            )
    await session.commit()
    related = (
        await client.get("/api/v2/learning/recalls/" + row["id"] + "/questions")
    ).json()
    assert len(related["items"]) == len(units)
    assert any(len(q["parts"]) > 1 for q in related["items"])
    before = (
        await client.get("/api/v2/learning/dashboard?subject_id=" + subject)
    ).json()
    s = (
        await client.post(
            "/api/v2/learning/recall-sessions", json={"recall_id": row["id"]}
        )
    ).json()
    await client.post(
        "/api/v2/learning/recall-sessions/" + s["id"] + "/answers",
        json={
            "revision": s["revision"],
            "blank_key": s["current_key"],
            "answer": "Atomicity",
        },
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    after = (
        await client.get("/api/v2/learning/dashboard?subject_id=" + subject)
    ).json()
    assert all(
        before[k] == after[k] for k in ("accuracy", "mastery", "completed", "total")
    )
    assert after["recent"][0]["kind"] == "recall"
    practice = await client.post(
        "/api/v2/learning/sessions",
        json={
            "subject_id": subject,
            "source": "recall",
            "source_id": row["version_id"],
            "mode": "resume",
        },
    )
    assert practice.status_code == 201, practice.text
    assert set(practice.json()["question_ids"]) == {q["id"] for q in units}
    second = await client.post(
        "/api/v2/learning/sessions",
        json={
            "subject_id": subject,
            "source": "recall",
            "source_id": row["version_id"],
            "mode": "resume",
        },
    )
    assert second.json()["id"] == practice.json()["id"]
    p = await session.get(ExamPaper, uuid.UUID(paper))
    p.status = "retired"
    await session.commit()
    assert (
        await client.get("/api/v2/learning/recalls/" + row["id"] + "/questions")
    ).json()["items"] == []


@pytest.mark.integration
async def test_all_types_hide_answers_and_finish(client, session):
    for row in await seed(client):
        await publish(client, row)
        detail = (await client.get("/api/v2/learning/recalls/" + row["id"])).json()
        answers = {b["key"]: b["answer"] for b in detail["blanks"]}
        s = (
            await client.post(
                "/api/v2/learning/recall-sessions", json={"recall_id": row["id"]}
            )
        ).json()
        while s["phase"] != "completed":
            r = await client.post(
                "/api/v2/learning/recall-sessions/" + s["id"] + "/answers",
                json={
                    "revision": s["revision"],
                    "blank_key": s["current_key"],
                    "answer": answers[s["current_key"]],
                },
                headers={"Idempotency-Key": uuid.uuid4().hex},
            )
            assert r.status_code == 200, r.text
            s = r.json()["session"]
        assert s["completed"] == len(answers)


@pytest.mark.integration
async def test_recall_role_boundaries(client, session):
    from httpx import ASGITransport, AsyncClient

    from app.core.security import passwords
    from app.main import app
    from app.models import Role, User, UserRole

    row = (await seed(client))[0]
    for role in ["editor", "reviewer", "publisher"]:
        session.add(Role(name=role))
    await session.commit()
    revision = row["revision"]
    for role in ["editor", "reviewer", "publisher", "learner"]:
        u = User(
            username="recall-" + role, password_hash=passwords.hash("isolated-password")
        )
        session.add(u)
        await session.flush()
        if role != "learner":
            session.add(UserRole(user_id=u.id, role=role))
        await session.commit()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            c.headers["Origin"] = "http://test"
            c.headers["X-CSRF-Token"] = (await c.get("/api/v1/auth/csrf")).json()[
                "csrf_token"
            ]
            login = await c.post(
                "/api/v1/auth/login",
                json={"username": u.username, "password": "isolated-password"},
            )
            c.headers["X-CSRF-Token"] = login.json()["csrf_token"]
            assert (await c.get("/api/v2/admin/recalls")).status_code == (
                403 if role == "learner" else 200
            )
            if role != "publisher":
                r = await c.post(
                    "/api/v2/admin/recalls/versions/" + row["version_id"] + "/publish",
                    json={"revision": revision},
                )
                assert r.status_code == 403
            if role in ["reviewer", "publisher", "learner"]:
                assert (
                    await c.post("/api/v2/admin/recalls/seed-pilots")
                ).status_code == 403
            if role == "editor":
                direct = await c.post(
                    "/api/v2/admin/recalls/versions/" + row["version_id"] + "/approve",
                    json={"revision": revision},
                )
                assert direct.status_code == 403
                submitted = await c.post(
                    "/api/v2/admin/recalls/versions/" + row["version_id"] + "/submit",
                    json={"revision": revision},
                )
                assert submitted.status_code == 200, submitted.text
                revision = submitted.json()["revision"]
            elif role == "reviewer":
                approved = await c.post(
                    "/api/v2/admin/recalls/versions/" + row["version_id"] + "/approve",
                    json={"revision": revision},
                )
                assert approved.status_code == 200, approved.text
                revision = approved.json()["revision"]
            elif role == "publisher":
                published = await c.post(
                    "/api/v2/admin/recalls/versions/" + row["version_id"] + "/publish",
                    json={"revision": revision},
                )
                assert published.status_code == 200, published.text
                revision = published.json()["revision"]


@pytest.mark.integration
async def test_concurrent_start_and_submission_are_idempotent(client, session):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models import User
    from app.recall import service
    from app.recall.models import RecallSession, RecallSessionBlank
    from app.recall.schemas import AnswerIn

    row = next(r for r in await seed(client) if r["type"] == "sentence")
    await publish(client, row)
    user = await session.scalar(select(User).where(User.username == "test-admin"))
    uid = user.id
    await session.commit()
    factory = async_sessionmaker(session.bind, expire_on_commit=False)

    async def start():
        async with factory.begin() as db:
            s = await service.create_session(db, uid, uuid.UUID(row["id"]))
            return s.id

    ids = await asyncio.gather(start(), start())
    assert ids[0] == ids[1]
    async with factory() as db:
        s = await db.get(RecallSession, ids[0])
        payload = AnswerIn(revision=s.revision, blank_key=s.queue[0], answer="wrong")
    key = uuid.uuid4().hex

    async def submit():
        async with factory.begin() as db:
            return await service.submit(db, ids[0], uid, payload, key)

    replies = await asyncio.gather(submit(), submit())
    assert replies[0] == replies[1]
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(RecallSubmission)) == 1
        b = await db.get(RecallSessionBlank, (ids[0], payload.blank_key))
        assert b.round_error_count == 1


@pytest.mark.integration
async def test_publication_waits_for_inflight_answer_then_removes_it(client, session, monkeypatch):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models import User
    from app.recall import service
    from app.recall.models import Recall, RecallVersion
    from app.recall.schemas import AnswerIn

    row = next(r for r in await seed(client) if r["type"] == "sentence")
    await publish(client, row)
    user = await session.scalar(select(User).where(User.username == "test-admin"))
    uid, rid = user.id, uuid.UUID(row["id"])
    old = await service.create_session(session, uid, rid)
    sid = old.id
    payload = AnswerIn(revision=old.revision, blank_key=old.queue[0], answer="wrong")
    version_data = await service.version_payload(session, await session.get(RecallVersion, old.version_id))
    version_data["title"] += " 新版"
    next_version = await service.add_version(session, await session.get(Recall, rid),
                                             VersionIn.model_validate(version_data), uid)
    vid = next_version.id
    await session.commit()
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    grading_started, finish_grading, publish_started = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def grade(**kwargs):
        grading_started.set()
        await finish_grading.wait()
        return False

    monkeypatch.setattr("app.recall.grading.semantic_grade", grade)

    async def answer():
        async with factory.begin() as db:
            await service.submit(db, sid, uid, payload, uuid.uuid4().hex)

    async def publish_new():
        await grading_started.wait()
        async with factory.begin() as db:
            publish_started.set()
            recall = await db.scalar(select(Recall).where(Recall.id == rid).with_for_update())
            await service.clear_obsolete_sessions(db, rid, vid)
            recall.published_version_id = vid
            (await db.get(RecallVersion, vid)).status = "published"

    answer_task = asyncio.create_task(answer())
    publish_task = asyncio.create_task(publish_new())
    try:
        await asyncio.wait_for(publish_started.wait(), timeout=5)
        assert not publish_task.done()
    finally:
        finish_grading.set()
        await asyncio.wait_for(asyncio.gather(answer_task, publish_task), timeout=10)
    async with factory.begin() as db:
        assert await db.get(RecallSession, sid) is None
        assert await db.scalar(select(func.count()).select_from(RecallSubmission)) == 0
        fresh = await service.create_session(db, uid, rid)
        assert fresh.version_id == vid
