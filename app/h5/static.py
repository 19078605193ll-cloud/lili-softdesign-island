from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import FileResponse, RedirectResponse
from app.config import get_settings
from app.core.errors import fail

router = APIRouter(include_in_schema=False)
DIST = Path(__file__).resolve().parents[2] / "web" / "h5" / "dist"


@router.get("/h5")
async def redirect_h5():
    return RedirectResponse("/h5/")


@router.get("/h5/{path:path}")
async def h5(path: str):
    if not get_settings().h5_enabled:
        raise fail(503, "H5_DISABLED", "学习端暂不可用")
    target = (DIST / path).resolve()
    if not target.is_relative_to(DIST.resolve()):
        raise fail(404, "NOT_FOUND", "页面不存在")
    if not target.is_file():
        if path.startswith("assets/"):
            raise fail(404, "NOT_FOUND", "资源不存在")
        target = DIST / "index.html"
    if not target.is_file():
        raise fail(503, "H5_NOT_BUILT", "请先构建 H5 静态资源")
    return FileResponse(
        target,
        headers={
            "Cache-Control": "no-cache"
            if target.name == "index.html"
            else "public, max-age=31536000, immutable"
        },
    )
