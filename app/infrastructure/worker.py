"""Prepare with a short DB read, perform I/O without a session, then fence and apply."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import random
import re
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError
from sqlalchemy import or_, select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.core.runtime import run_async
from app.imports.ai import CLASSIFICATION_PROMPT_VERSION
from app.imports.markdown_parser import IMAGE, parse_markdown
from app.imports.markdown_storage import (
    unpack,
    local_image,
    download_image,
    store_image,
)
from app.imports.storage import LocalImportStorage
from app.imports import markdown_workflow as workflow
from app.imports.service import (
    classification_fingerprint,
    classification_run_matches,
    validate_import_item,
)
from app.infrastructure.celery_app import celery_app
from app.infrastructure.jobs import enqueue, fingerprint
from app.models import (
    AuditEvent,
    Job,
    Outbox,
    QuestionImportBatch,
    QuestionSourceDocument,
    QuestionImportGroup,
    QuestionImportItem,
    KnowledgeTaxonomyRelease,
    KnowledgeNode,
    QuestionAsset,
    QuestionAssetUsage,
    QuestionClassificationRun,
    QuestionClassificationCandidate,
)


def utcnow():
    return datetime.now(timezone.utc)


async def prepare(factory, job_id):
    async with factory.begin() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        job = await session.scalar(
            select(Job).where(Job.id == job_id).with_for_update()
        )
        if (
            not job
            or job.status not in {"queued", "retry_wait"}
            or job.available_at > now
        ):
            return None
        job.status = "running"
        job.generation += 1
        job.lease_until = now + timedelta(seconds=660 if job.kind in {'tutor','variant','variant_review'} else 360)
        return dict(
            id=job.id,
            batch_id=job.batch_id,
            kind=job.kind,
            payload=copy.deepcopy(job.payload),
            generation=job.generation,
            user_id=job.user_id,
            request_id=job.request_id,
        )


async def inputs(factory, task):
    async with factory() as session:
        batch = await session.get(QuestionImportBatch, task["batch_id"])
        if not batch:
            return None
        if (
            "revision" in task["payload"]
            and batch.revision != task["payload"]["revision"]
        ):
            return None
        if task["kind"] == "parse":
            return task["payload"]
        doc = await workflow.document_for(session, batch.id)
        state = copy.deepcopy(doc.extracted_content)
        request = task["payload"].get("request", {})
        if task["kind"] == "assist":
            block = next(
                (b for b in state["blocks"] if b["id"] == request.get("block_id")), None
            )
            return (
                {"block": block}
                if block and not block.get("confirmed") and not block.get("excluded")
                else None
            )
        if task["kind"] == "archive_images":
            return dict(
                state=state,
                storage_path=doc.storage_path,
                reference=task["payload"].get("reference"),
            )
        if task["kind"] == "supplement":
            if batch.status != "published":
                return None
            blocks = [b for b in state["blocks"] if b.get("excluded")]
            if not blocks:
                raise ValueError("NO_EXCLUDED_BLOCKS")
            for block in blocks:
                if block.get("group_id"):
                    group, items, _ = await workflow.draft_payload(session, block)
                    block.update(
                        material_markdown=group.material_markdown,
                        explanation_markdown=group.explanation_markdown,
                        parts=[
                            dict(
                                question_no=i.question_no,
                                stem_markdown=i.stem_markdown,
                                options=i.options_payload,
                                correct_option_keys=i.correct_option_keys,
                                explanation_markdown=i.explanation_markdown,
                            )
                            for i in items
                        ],
                    )
            assets = list(
                await session.scalars(
                    select(QuestionAsset).where(QuestionAsset.document_id == doc.id)
                )
            )
            return dict(
                blocks=blocks,
                source=state["source"],
                mappings=state["assets"],
                assets=[dict(id=str(a.id), path=a.storage_path) for a in assets],
                meta={
                    k: getattr(batch, k)
                    for k in [
                        "subject_id",
                        "taxonomy_release_id",
                        "year",
                        "period",
                        "batch_code",
                        "title",
                        "exam_date",
                    ]
                },
            )
        release = await session.get(KnowledgeTaxonomyRelease, batch.taxonomy_release_id)
        if not release or not release.catalog_snapshot:
            raise ValueError("CATALOG_SNAPSHOT_MISSING")
        block_ids = {
            b.get("group_id")
            for b in state["blocks"]
            if not b.get("confirmed")
            and not b.get("excluded")
            and (not request.get("block_id") or b["id"] == request["block_id"])
        }
        query = (
            select(QuestionImportItem)
            .where(
                QuestionImportItem.batch_id == batch.id,
                QuestionImportItem.group_id.in_([uuid.UUID(g) for g in block_ids if g]),
                QuestionImportItem.status == "needs_review",
            )
            .order_by(QuestionImportItem.question_no)
        )
        if request.get("question_no"):
            query = query.where(
                QuestionImportItem.question_no == request["question_no"]
            )
        if task["payload"].get("item_ids"):
            query = query.where(
                QuestionImportItem.id.in_(
                    [uuid.UUID(i) for i in task["payload"]["item_ids"]]
                )
            )
        items = list(await session.scalars(query))
        result = []
        for item in items:
            group = await session.get(QuestionImportGroup, item.group_id)
            if any(i.severity == "error" for i in validate_import_item(item, group)):
                continue
            if not request.get("force"):
                previous = list(
                    await session.scalars(
                        select(QuestionClassificationRun).where(
                            QuestionClassificationRun.import_item_id == item.id,
                            QuestionClassificationRun.status == "completed",
                            QuestionClassificationRun.prompt_version
                            == CLASSIFICATION_PROMPT_VERSION,
                        )
                    )
                )
                if any(
                    classification_run_matches(r, item, group, batch, release)
                    for r in previous
                ):
                    continue
            assets = list(
                await session.scalars(
                    select(QuestionAsset)
                    .join(QuestionAssetUsage)
                    .where(
                        or_(
                            QuestionAssetUsage.import_item_id == item.id,
                            QuestionAssetUsage.import_group_id == item.group_id,
                        )
                    )
                )
            )
            result.append(
                dict(
                    id=str(item.id),
                    fingerprint=classification_fingerprint(item, group),
                    asset_hashes={str(a.id): a.sha256 for a in assets},
                    prompt=dict(
                        question_no=item.question_no,
                        shared_material=group.material_markdown,
                        shared_explanation=group.explanation_markdown,
                        stem=item.stem_markdown,
                        options=item.options_payload,
                        answer=item.correct_option_keys,
                        explanation=item.explanation_markdown,
                        locator={"question_no": item.question_no},
                        asset_image_paths=[
                            str(
                                LocalImportStorage(
                                    get_settings().import_storage_root
                                ).resolve(a.storage_path)
                            )
                            for a in assets
                        ],
                    ),
                )
            )
        return dict(
            items=result,
            revision=batch.revision,
            catalog=release.catalog_snapshot["topics"],
            checksum=release.checksum_sha256,
            release_id=str(release.id),
        )


async def perform(task, data):
    storage = LocalImportStorage(get_settings().import_storage_root)
    kind = task["kind"]
    if kind == "parse":

        def parse():
            path = unpack(
                storage,
                task["batch_id"],
                data["filename"],
                storage.resolve(data["source"]).read_bytes(),
            )
            source = path.read_text(encoding="utf-8-sig")
            if "\x00" in source:
                raise ValueError("INVALID_SOURCE")
            return dict(
                source=source,
                path=path.relative_to(storage.root).as_posix(),
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                size=path.stat().st_size,
                blocks=[
                    u.model_dump()
                    | {"id": str(uuid.uuid4()), "excluded": False, "confirmed": False}
                    for u in parse_markdown(source)
                ],
            )

        return await asyncio.to_thread(parse)
    if kind == "assist":
        from app.imports.text_assist import suggest

        return await suggest(data["block"]["raw"])
    if kind == "archive_images":
        reference = data["reference"]
        if not reference:
            return {"fanout": True}
        if reference in data["state"].get("assets", {}):
            return {"skipped": True}
        if reference.startswith(("http://", "https://")):
            body = await download_image(reference, 20 * 1024 * 1024)
        else:
            body = await asyncio.to_thread(
                lambda: local_image(
                    reference,
                    storage.resolve(data["storage_path"]),
                    storage.resolve(f"{task['batch_id']}/markdown"),
                ).read_bytes()
            )
        return await asyncio.to_thread(store_image, storage, task["batch_id"], body)
    if kind == "supplement":
        target = uuid.uuid5(task["id"], "supplement")

        def copy_files():
            path = storage.resolve(f"{target}/markdown/source.md")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data["source"], encoding="utf-8")
            return {
                a["id"]: store_image(
                    storage, target, storage.resolve(a["path"]).read_bytes()
                )
                for a in data["assets"]
            }

        return dict(target=str(target), assets=await asyncio.to_thread(copy_files))
    if len(data["items"]) > 5:
        return {"fanout": True}
    if not data["items"]:
        return {"empty": True, "processed": 0, "failed_items": 0}
    from app.imports.ai import OpenAICompatibleQuestionClient as Client

    client = Client(get_settings())
    try:
        parsed, raw = await client.classify_questions(
            [i["prompt"] for i in data["items"]], data["catalog"]
        )
        return {
            "parsed": parsed.model_dump(mode="json"),
            "raw": raw,
            "model": client.classification_model,
            "provider": client.provider_name,
        }
    finally:
        if hasattr(client, "client"):
            await client.client.close()


async def schedule_images(session, task, batch, state):
    jobs = []
    for match in IMAGE.finditer(state["source"]):
        ref = match[2]
        if ref in state.get("assets", {}):
            continue
        child = await enqueue(
            session,
            user_id=task["user_id"],
            batch=batch,
            kind="archive_images",
            payload={"reference": ref},
            key=f"{task['id']}:{fingerprint(ref)}",
            request_id=task["request_id"],
        )
        jobs.append(str(child.id))
    return list(dict.fromkeys(jobs))


async def apply(factory, task, data, output):
    async with factory.begin() as session:
        job = await session.scalar(
            select(Job).where(Job.id == task["id"]).with_for_update()
        )
        now = await session.scalar(select(func.clock_timestamp()))
        if (
            not job
            or job.status != "running"
            or job.generation != task["generation"]
            or job.lease_until < now
        ):
            return
        batch = await session.scalar(
            select(QuestionImportBatch)
            .where(QuestionImportBatch.id == job.batch_id)
            .with_for_update()
        )
        if data is None or (
            "revision" in task["payload"]
            and batch.revision != task["payload"]["revision"]
        ):
            job.status = "superseded"
            return
        if job.kind == "classify" and data.get("revision") != batch.revision:
            job.status = "superseded"
            return
        if batch.status == "published" and job.kind != "supplement":
            job.status = "superseded"
            return
        children = []
        if job.kind == "parse":
            doc = await session.scalar(
                select(QuestionSourceDocument).where(
                    QuestionSourceDocument.batch_id == batch.id
                )
            )
            if doc is None:
                doc = QuestionSourceDocument(
                    batch_id=batch.id,
                    role="combined",
                    original_name=task["payload"]["filename"],
                    mime_type="text/markdown",
                    file_size=output["size"],
                    sha256=output["sha256"],
                    storage_path=output["path"],
                    status="uploaded",
                    extracted_content=dict(
                        source=output["source"],
                        blocks=output["blocks"],
                        assets={},
                        image_errors={},
                    ),
                )
                session.add(doc)
                await session.flush()
                await workflow.materialize(session, batch, doc)
            children = await schedule_images(
                session, task, batch, doc.extracted_content
            )
            if not children and get_settings().ai_api_key:
                child = await enqueue(
                    session,
                    user_id=job.user_id,
                    batch=batch,
                    kind="classify",
                    payload={"request": {}},
                    key=f"auto:{job.id}",
                )
                children.append(str(child.id))
        elif job.kind == "archive_images":
            doc = await workflow.document_for(session, batch.id, lock=True)
            if output.get("fanout"):
                children = await schedule_images(
                    session, task, batch, doc.extracted_content
                )
            elif not output.get("skipped"):
                ref = data["reference"]
                state = copy.deepcopy(doc.extracted_content)
                if ref in state["assets"]:
                    job.status = "superseded"
                    return
                asset = await session.scalar(
                    select(QuestionAsset).where(
                        QuestionAsset.document_id == doc.id,
                        QuestionAsset.sha256 == output["sha256"],
                    )
                )
                if asset is None:
                    asset = QuestionAsset(
                        document_id=doc.id,
                        page_no=None,
                        bbox=None,
                        kind="figure",
                        **output,
                    )
                    session.add(asset)
                    await session.flush()
                state["assets"][ref] = str(asset.id)
                state["image_errors"].pop(ref, None)
                doc.extracted_content = state
                from app.imports.asset_mapping import apply_asset_mapping

                await apply_asset_mapping(session, batch, doc)
                batch.revision += 1
                remaining = {m[2] for m in IMAGE.finditer(state["source"])} - set(
                    state["assets"]
                )
                if not remaining and get_settings().ai_api_key:
                    child = await enqueue(
                        session,
                        user_id=job.user_id,
                        batch=batch,
                        kind="classify",
                        payload={"request": {}},
                        key=f"images-complete:{batch.revision}",
                    )
                    children.append(str(child.id))
        elif job.kind == "assist":
            doc = await workflow.document_for(session, batch.id, lock=True)
            state = copy.deepcopy(doc.extracted_content)
            block = next(b for b in state["blocks"] if b["id"] == data["block"]["id"])
            if block.get("confirmed") or block.get("excluded"):
                job.status = "superseded"
                return
            block["ai_suggestion"] = output
            doc.extracted_content = state
        elif job.kind == "supplement":
            target_id = uuid.UUID(output["target"])
            target = await session.get(QuestionImportBatch, target_id)
            if target is None:
                target = QuestionImportBatch(
                    id=target_id,
                    **data["meta"],
                    parser_version=workflow.VERSION,
                    source_reference=f"补充自批次 {batch.id}",
                    status="uploaded",
                    expected_question_count=None,
                )
                session.add(target)
                await session.flush()
                doc = QuestionSourceDocument(
                    batch_id=target.id,
                    role="combined",
                    original_name="source.md",
                    mime_type="text/markdown",
                    file_size=len(data["source"].encode()),
                    sha256=hashlib.sha256(data["source"].encode()).hexdigest(),
                    storage_path=f"{target.id}/markdown/source.md",
                    status="uploaded",
                    extracted_content={},
                )
                session.add(doc)
                await session.flush()
                mapping = {}
                for old, info in output["assets"].items():
                    asset = QuestionAsset(
                        document_id=doc.id,
                        page_no=None,
                        bbox=None,
                        kind="figure",
                        **info,
                    )
                    session.add(asset)
                    await session.flush()
                    mapping[old] = str(asset.id)
                blocks = []
                for old in data["blocks"]:
                    block = {
                        k: v
                        for k, v in old.items()
                        if k
                        not in {
                            "group_id",
                            "history",
                            "ai_suggestion",
                            "conflict_action",
                            "conflict_hash",
                        }
                    }
                    block = json.loads(
                        re.sub(
                            r"asset://([0-9a-fA-F-]{36})",
                            lambda m: "asset://" + mapping.get(m[1], m[1]),
                            json.dumps(block),
                        )
                    )
                    block.update(id=str(uuid.uuid4()), confirmed=False, excluded=False)
                    blocks.append(block)
                doc.extracted_content = dict(
                    source=data["source"],
                    blocks=blocks,
                    assets={ref: mapping[old] for ref, old in data["mappings"].items()},
                    image_errors={},
                )
                await workflow.materialize(session, target, doc)
            output = {"batch_id": str(target_id)}
        elif output.get("fanout"):
            for start in range(0, len(data["items"]), 5):
                child = await enqueue(
                    session,
                    user_id=job.user_id,
                    batch=batch,
                    kind="classify",
                    payload={
                        "request": task["payload"].get("request", {}),
                        "item_ids": [i["id"] for i in data["items"][start : start + 5]],
                        "revision": batch.revision,
                    },
                    key=f"{job.id}:{start}",
                )
                children.append(str(child.id))
        elif not output.get("empty"):
            results = {r["question_no"]: r for r in output["parsed"]["results"]}
            allowed = {t["code"] for t in data["catalog"]}
            nodes = {
                n.code: n
                for n in await session.scalars(
                    select(KnowledgeNode).where(
                        KnowledgeNode.subject_id == batch.subject_id,
                        KnowledgeNode.status == "active",
                        KnowledgeNode.node_type == "topic",
                    )
                )
            }
            processed = 0
            for original in data["items"]:
                item = await session.get(QuestionImportItem, uuid.UUID(original["id"]))
                group = (
                    await session.get(QuestionImportGroup, item.group_id)
                    if item
                    else None
                )
                if (
                    not item
                    or item.status != "needs_review"
                    or classification_fingerprint(item, group)
                    != original["fingerprint"]
                ):
                    continue
                run = QuestionClassificationRun(
                    import_item_id=item.id,
                    taxonomy_release_id=uuid.UUID(data["release_id"]),
                    provider=output["provider"],
                    model=output["model"],
                    prompt_version=CLASSIFICATION_PROMPT_VERSION,
                    catalog_checksum=data["checksum"],
                    input_fingerprint=original["fingerprint"],
                    status="completed",
                    raw_response=output["raw"],
                )
                session.add(run)
                await session.flush()
                result = results.get(item.question_no, {})
                primary = False
                for role in ("primary", "related"):
                    seen = set()
                    for rank, candidate in enumerate(
                        result.get(role + "_candidates", []), 1
                    ):
                        code = candidate["code"]
                        if code not in allowed or code not in nodes or code in seen:
                            continue
                        seen.add(code)
                        primary |= role == "primary"
                        session.add(
                            QuestionClassificationCandidate(
                                run_id=run.id,
                                knowledge_node_id=nodes[code].id,
                                role=role,
                                rank=rank,
                                confidence=candidate["confidence"],
                                rationale=candidate["rationale"],
                            )
                        )
                if not primary:
                    run.status = "failed"
                    run.error_summary = "模型未返回有效主知识点"
                processed += 1
            output = {
                "processed": processed,
                "failed_items": sum(
                    not r.get("primary_candidates") for r in results.values()
                ),
            }
        if job.kind not in {"supplement"}:
            await workflow.refresh(session, batch)
        job.status = "succeeded"
        job.lease_until = None
        job.result = {
            "children": children,
            **(
                output
                if job.kind in {"assist", "supplement"}
                or (job.kind == "classify" and not children)
                else {}
            ),
        }
        session.add(
            AuditEvent(
                user_id=job.user_id,
                action="job:" + job.kind,
                object_id=str(batch.id),
                request_id=job.request_id,
                summary={
                    "job_id": str(job.id),
                    "generation": job.generation,
                    "result": "committed",
                },
            )
        )


async def failed(factory, task, exc):
    transient = (
        isinstance(exc, (httpx.TransportError, APIConnectionError))
        or isinstance(exc, APIStatusError)
        and (exc.status_code == 429 or exc.status_code >= 500)
    )
    async with factory.begin() as session:
        job = await session.scalar(
            select(Job).where(Job.id == task["id"]).with_for_update()
        )
        if not job or job.status != "running" or job.generation != task["generation"]:
            return
        if transient and job.retries < (1 if job.kind in {'tutor','variant','variant_review','variant_wait'} else 3):
            delay = [10, 30, 90][job.retries] + random.uniform(0, 5)
            if isinstance(exc, APIStatusError):
                try:
                    delay = max(
                        delay,
                        min(float(exc.response.headers.get("Retry-After", "0")), 120),
                    )
                except ValueError:
                    pass
            job.retries += 1
            job.status = "retry_wait"
            job.available_at = utcnow() + timedelta(seconds=delay)
            (await session.get(Outbox, job.id)).sent_at = None
        else:
            job.status = "failed"
        job.lease_until = None
        job.error_code = type(exc).__name__
        job.error_detail = "任务处理失败，原始资料已保留；请检查配置或重试"
        if job.kind in {"tutor", "variant", "variant_review", "variant_wait"}:
            if isinstance(exc, (APITimeoutError, httpx.TimeoutException)):
                job.error_detail = "AI 模型响应超时，请稍后重试"
            else:
                job.error_detail = "AI 服务暂时不可用，请稍后重试"
        import logging

        logging.getLogger("island").warning(
            "job_attempt_failed",
            extra={"fields": {"job_id": str(job.id), "kind": job.kind,
                              "error_code": job.error_code, "status": job.status,
                              "retries": job.retries}},
        )
        if job.kind == "archive_images" and task["payload"].get("reference"):
            doc = await workflow.document_for(session, job.batch_id, lock=True)
            state = copy.deepcopy(doc.extracted_content)
            state.setdefault("image_errors", {})[task["payload"]["reference"]] = (
                job.error_detail
            )
            doc.extracted_content = state


async def execute(job_id, factory=None):
    if not get_settings().tasks_enabled or not get_settings().writes_enabled:
        return
    engine = None
    if factory is None:
        engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
    task = None
    try:
        task = await prepare(factory, uuid.UUID(str(job_id)))
        if task is None:
            return
        if task['kind'] in {'tutor', 'variant', 'variant_review', 'variant_wait'}:
            from app.h5.ai_worker import execute_learning
            await execute_learning(factory, task)
            return
        data = await inputs(factory, task)
        output = await perform(task, data) if data is not None else {}
        await apply(factory, task, data, output)
    except Exception as exc:
        if task:
            await failed(factory, task, exc)
        else:
            raise
    finally:
        if engine:
            await engine.dispose()


@celery_app.task(name="island.execute")
def execute_task(job_id: str):
    run_async(execute(job_id))
