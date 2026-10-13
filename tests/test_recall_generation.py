import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.infrastructure.worker import execute
from app.models import Job, KnowledgeNode
from app.recall.generation import normalize_evidence
from app.recall.schemas import GeneratedRecall

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def recall_prompt_file(tmp_path, monkeypatch):
    path = tmp_path / "recall-prompt.md"
    path.write_text("隔离测试：生成独立、准确的知识回忆内容。", encoding="utf-8-sig")
    monkeypatch.setattr("app.recall.generation.PROMPT_PATH", path)
    return path


def generated(*, references=None):
    return GeneratedRecall.model_validate(
        {
            "title": "AI 主动回忆草稿",
            "content": {
                "type": "sentence",
                "paragraphs": [
                    [{"text": "关键结论是"}, {"blank_key": "answer_1"}]
                ],
                "note": "用于隔离测试",
            },
            "blanks": [
                {
                    "key": "answer_1",
                    "label": "关键结论",
                    "answer": "正确答案",
                    "acceptable_answers": [],
                    "normalization": {
                        "case_sensitive": True,
                        "whitespace": "preserve",
                        "aliases": {},
                        "unordered_set": False,
                        "separator": ",",
                    },
                    "hints": ["回忆概念定义", "关注题干中的核心性质"],
                }
            ],
            "evidence_references": references or [],
            "supplements": [],
        }
    )


def test_missing_or_invalid_evidence_becomes_supplement():
    refs, supplements = normalize_evidence(
        generated(
            references=[
                {"path": "/content", "evidence_ids": ["not-in-input"]}
            ]
        ),
        {"knowledge:1"},
    )
    assert refs == [{"path": "/content", "evidence_ids": []}]
    assert {item["path"] for item in supplements} == {
        "/content",
        "/blanks/answer_1/answer",
        "/blanks/answer_1/hints/0",
        "/blanks/answer_1/hints/1",
    }


@pytest.mark.integration
async def test_batch_generation_idempotency_worker_and_simplified_review(
    client, session, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_api_key", "isolated-test-only")
    monkeypatch.setattr(settings, "ai_recall_model", "fixture-recall")
    monkeypatch.setattr(settings, "recall_ai_generation_enabled", True)

    nodes = list(
        await session.scalars(
            select(KnowledgeNode)
            .where(KnowledgeNode.node_type == "topic")
            .order_by(KnowledgeNode.code)
            .limit(2)
        )
    )
    assert len(nodes) == 2

    async def fake_generate(data):
        assert data["node"]["id"] in {str(node.id) for node in nodes}
        return generated(), "fixture-recall", "fixture"

    monkeypatch.setattr("app.recall.generation.generate", fake_generate)
    body = {
        "subject_id": str(nodes[0].subject_id),
        "instruction": "突出易错点",
        "items": [
            {
                "knowledge_node_id": str(node.id),
                "type": "auto",
                "target": {"mode": "new"},
            }
            for node in nodes
        ],
    }
    key = uuid.uuid4().hex
    response = await client.post(
        "/api/v2/admin/recalls/generations",
        json=body,
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 202, response.text
    first = response.json()
    repeated = await client.post(
        "/api/v2/admin/recalls/generations",
        json=body,
        headers={"Idempotency-Key": key},
    )
    assert repeated.status_code == 202
    assert [item["job_id"] for item in repeated.json()["items"]] == [
        item["job_id"] for item in first["items"]
    ]
    changed = {**body, "instruction": "另一个请求"}
    conflict = await client.post(
        "/api/v2/admin/recalls/generations",
        json=changed,
        headers={"Idempotency-Key": key},
    )
    assert conflict.status_code == 409

    await session.commit()
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    for item in first["items"]:
        await execute(uuid.UUID(item["job_id"]), factory)
    session.expire_all()
    result = (
        await client.get(
            "/api/v2/admin/recalls/generations/" + first["request_id"]
        )
    ).json()
    assert [item["status"] for item in result["items"]] == [
        "succeeded",
        "succeeded",
    ]

    version_id = result["items"][0]["result"]["version_id"]
    detail = (
        await client.get("/api/v2/admin/recalls/versions/" + version_id)
    ).json()
    supplements = detail["provenance"]["supplements"]
    assert detail["provenance"]["prompt_snapshot"] == "隔离测试：生成独立、准确的知识回忆内容。"
    assert len(detail["provenance"]["prompt_sha256"]) == 64
    assert detail["provenance"]["contract_version"] == "recall-contract-v3"
    assert detail["provenance"]["input"]["type"] == "auto"
    assert detail["provenance"]["input"]["instruction"] == "突出易错点"
    assert len(supplements) == 4
    preview = (
        await client.get(f"/api/v2/admin/recalls/versions/{version_id}/preview")
    ).json()
    assert "supplements" not in preview

    approved = await client.post(
        f"/api/v2/admin/recalls/versions/{version_id}/approve",
        json={"revision": detail["revision"]},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    published = await client.post(
        f"/api/v2/admin/recalls/versions/{version_id}/publish",
        json={"revision": approved.json()["revision"]},
    )
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"

    second_version_id = result["items"][1]["result"]["version_id"]
    second_detail = (
        await client.get("/api/v2/admin/recalls/versions/" + second_version_id)
    ).json()
    submitted = await client.post(
        f"/api/v2/admin/recalls/versions/{second_version_id}/submit",
        json={"revision": second_detail["revision"]},
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "pending"

    jobs = list(
        await session.scalars(select(Job).where(Job.kind == "recall_generate"))
    )
    assert len(jobs) == 2


async def test_empty_prompt_rejected_before_enqueue(client, session, monkeypatch, recall_prompt_file):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_api_key", "fixture")
    monkeypatch.setattr(settings, "ai_recall_model", "fixture")
    monkeypatch.setattr(settings, "recall_ai_generation_enabled", True)
    node = await session.scalar(select(KnowledgeNode).where(KnowledgeNode.node_type == "topic"))
    recall_prompt_file.write_text("", encoding="utf-8")
    response = await client.post('/api/v2/admin/recalls/generations',
        headers={"Idempotency-Key": uuid.uuid4().hex},
        json={"subject_id":str(node.subject_id),"items":[{"knowledge_node_id":str(node.id),"type":"auto","target":{"mode":"new"}}]})
    assert response.status_code == 503
    assert response.json()["code"] == "RECALL_PROMPT_NOT_CONFIGURED"
    assert not list(await session.scalars(select(Job).where(Job.kind == "recall_generate")))


async def test_worker_prompt_error_and_transport_snapshot(client, session, monkeypatch, recall_prompt_file):
    from app.recall.generation import prepare_generation
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_api_key", "fixture")
    monkeypatch.setattr(settings, "ai_recall_model", "fixture")
    monkeypatch.setattr(settings, "recall_ai_generation_enabled", True)
    node = await session.scalar(select(KnowledgeNode).where(KnowledgeNode.node_type == "topic"))
    async def enqueue():
        response = await client.post('/api/v2/admin/recalls/generations',
            headers={"Idempotency-Key": uuid.uuid4().hex},
            json={"subject_id":str(node.subject_id),"items":[{"knowledge_node_id":str(node.id),"type":"auto","target":{"mode":"new"}}]})
        assert response.status_code == 202, response.text
        await session.commit()
        return uuid.UUID(response.json()["items"][0]["job_id"])
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    job_id = await enqueue()
    job = await session.get(Job, job_id)
    task = {"id":job.id,"payload":job.payload}
    first = await prepare_generation(factory, task)
    recall_prompt_file.write_text("新任务的提示词", encoding="utf-8")
    second = await prepare_generation(factory, task)
    assert first["prompt"] == second["prompt"]
    other_id = await enqueue()
    recall_prompt_file.write_text("", encoding="utf-8")
    async def forbidden(_):
        pytest.fail("empty prompt must never invoke generate")
    monkeypatch.setattr("app.recall.generation.generate", forbidden)
    await execute(other_id, factory)
    session.expire_all()
    failed = await session.get(Job, other_id)
    assert failed.status == "failed"
    assert failed.error_code == "RECALL_PROMPT_NOT_CONFIGURED"
    assert "请先填写" in failed.error_detail


@pytest.mark.integration
async def test_generation_does_not_write_after_base_version_changes(
    client, session, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_api_key", "isolated-test-only")
    monkeypatch.setattr(settings, "ai_recall_model", "fixture-recall")
    monkeypatch.setattr(settings, "recall_ai_generation_enabled", True)

    assert (await client.post("/api/v2/admin/recalls/seed-pilots")).status_code == 200
    row = (await client.get("/api/v2/admin/recalls")).json()["items"][0]
    detail = (
        await client.get(
            "/api/v2/admin/recalls/versions/" + row["version_id"]
        )
    ).json()

    async def fake_generate(_data):
        return generated(), "fixture-recall", "fixture"

    monkeypatch.setattr("app.recall.generation.generate", fake_generate)
    response = await client.post(
        "/api/v2/admin/recalls/generations",
        headers={"Idempotency-Key": uuid.uuid4().hex},
        json={
            "subject_id": detail["subject_id"],
            "items": [
                {
                    "knowledge_node_id": detail["knowledge_node_ids"][0],
                    "type": "auto",
                    "target": {"mode": "new_version", "recall_id": row["id"]},
                }
            ],
        },
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["items"][0]["job_id"]
    body = {
        key: detail[key]
        for key in (
            "title",
            "content",
            "blanks",
            "knowledge_node_ids",
            "source",
            "provenance",
        )
    }
    body["title"] += " 人工修订"
    changed = await client.post(
        f"/api/v2/admin/recalls/{row['id']}/versions",
        headers={"If-Match": f"{row['version_id']}:{detail['revision']}"},
        json=body,
    )
    assert changed.status_code == 201, changed.text

    await session.commit()
    await execute(
        uuid.UUID(job_id),
        async_sessionmaker(session.bind, expire_on_commit=False),
    )
    session.expire_all()
    job = await session.get(Job, uuid.UUID(job_id))
    assert job.status == "superseded"
    assert job.error_code == "CONTENT_CHANGED"
    versions = (
        await client.get("/api/v2/admin/recalls")
    ).json()["items"]
    assert len([item for item in versions if item["id"] == row["id"]]) == 2
