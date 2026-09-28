from fastapi import FastAPI, Depends
from contextlib import asynccontextmanager

from app.api import router
from app.config import get_settings
from app.imports.api import router as import_router
from app.imports.admin import router as admin_router

settings = get_settings()
from app.core.security import protect, router as auth_router
from app.core.observability import install
from app.db import engine


@asynccontextmanager
async def lifespan(app):
    from app.core.observability import JsonFormatter
    import logging

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("island")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    yield
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="0.3.0",
    lifespan=lifespan,
    dependencies=[Depends(protect)],
)
install(app)
app.include_router(auth_router)
app.include_router(router)
app.include_router(import_router)
app.include_router(admin_router)
from app.imports.markdown_api import (
    router as markdown_router,
    public_router as practice_router,
)

app.include_router(markdown_router)
app.include_router(practice_router)
from app.practice.api import router as practice_v2_router
from app.learning.api import router as learning_router

app.include_router(practice_v2_router)
app.include_router(learning_router)
from app.infrastructure.task_api import router as task_router

app.include_router(task_router)
from app.core.health import router as health_router

app.include_router(health_router)
