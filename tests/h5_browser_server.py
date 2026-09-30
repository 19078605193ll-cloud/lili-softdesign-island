"""Isolated browser fixture only. Never used by app/server or production images."""

import asyncio
import os
from contextlib import asynccontextmanager
import uvicorn
from sqlalchemy import select
from app.main import app
from app.db import SessionFactory
from app.models import Job
from app.infrastructure.worker import execute
from app.server import selector_loop_factory
import app.h5.ai_worker as worker


fixture_generation = 0


async def fixture_completion(messages, *, structured=False):
    global fixture_generation
    if structured:
        if "独立的软件设计师试题复核员" in messages[0]["content"]:
            return {"valid": True, "answer": "A", "reason": "isolated fixture"}
        fixture_generation += 1
        return dict(
            stem=f"隔离测试第{fixture_generation}题：二进制0010左移一位的结果是什么？",
            options={"A": "0100", "B": "0001", "C": "0011", "D": "0000"},
            answer="A",
            explanation="向左移动一位，低位补零，得到0100。",
        )
    if "画出步骤" in messages[-1]["content"]:
        return "先看输入，再看输出。\n```mermaid\nflowchart LR\n A[输入] --> B[处理] --> C[输出]\n```"
    return "隔离测试：先判断这一步的输入与输出分别是什么？"


worker.completion = fixture_completion
original = app.router.lifespan_context


async def drain():
    while True:
        async with SessionFactory() as s:
            ids = list(
                await s.scalars(
                    select(Job.id).where(
                        Job.batch_id.is_(None), Job.status.in_(["queued", "retry_wait"])
                    )
                )
            )
        for jid in ids:
            await execute(jid, SessionFactory)
        await asyncio.sleep(0.15)


@asynccontextmanager
async def lifespan(application):
    async with original(application):
        task = asyncio.create_task(drain())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


app.router.lifespan_context = lifespan
if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ["H5_E2E_PORT"]),
        loop=selector_loop_factory,
    )
