from sqlalchemy import select
from fastapi import HTTPException
from app.models import QuestionImportBatch
from app.imports import markdown_workflow as workflow


async def context(session, batch_id, *, writable=False):
    query = select(QuestionImportBatch).where(QuestionImportBatch.id == batch_id)
    if writable:
        query = query.with_for_update()
    batch = await session.scalar(query)
    if batch is None:
        raise HTTPException(404, "导入批次不存在")
    if writable and batch.status == "published":
        raise HTTPException(409, "已发布批次不可修改，请创建补充批次")
    if writable:
        from app.core.revisions import check_revision

        await check_revision(session, batch)
    doc = await workflow.document_for(session, batch_id, lock=writable)
    return batch, doc


def block_for(doc, block_id):
    block = next(
        (b for b in doc.extracted_content["blocks"] if b["id"] == block_id), None
    )
    if block is None:
        raise HTTPException(404, "题块不存在")
    return dict(block)


def replace_block(doc, block):
    doc.extracted_content = dict(
        doc.extracted_content,
        blocks=[
            block if b["id"] == block["id"] else b
            for b in doc.extracted_content["blocks"]
        ],
    )
