from fastapi import FastAPI

from app.api import router
from app.config import get_settings
from app.imports.api import router as import_router
from app.imports.admin import router as admin_router

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.2.0")
app.include_router(router)
app.include_router(import_router)
app.include_router(admin_router)
from app.imports.markdown_api import router as markdown_router, public_router as practice_router
app.include_router(markdown_router)
app.include_router(practice_router)


@app.middleware("http")
async def retired_page_workflow(request, call_next):
    """Historical source viewing survives; page/OCR mutations are retired."""
    import re
    from fastapi.responses import JSONResponse
    path = request.url.path
    retired = re.fullmatch(r"/api/v1/admin/import-batches/[^/]+/(suggest-sections|sections|parse|repair|reconcile)", path)
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and retired:
        return JSONResponse(status_code=410, content={"detail": "旧页面识别及逐题编辑入口已停用；请使用 Markdown 整题审核入口。历史数据仍可查看。"})
    return await call_next(request)
