import json
import logging
import time
import uuid
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.exceptions import RequestValidationError


class JsonFormatter(logging.Formatter):
    def format(self, record):
        # Deliberately do not serialize exception strings, arguments or request bodies.
        return json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                **getattr(record, "fields", {}),
            },
            ensure_ascii=False,
        )


def install(app):
    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc):
        return JSONResponse(
            {
                "detail": [
                    {"loc": e["loc"], "msg": e["msg"], "type": e["type"]}
                    for e in exc.errors()
                ],
                "code": "VALIDATION_ERROR",
                "request_id": request.state.request_id,
            },
            status_code=422,
        )

    @app.middleware("http")
    async def trace(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception as exc:
            from app.config import get_settings

            if get_settings().app_env == "test":
                raise
            logging.getLogger("island.http").error(
                "request_failed",
                extra={
                    "fields": {
                        "request_id": request.state.request_id,
                        "exception_type": type(exc).__name__,
                    }
                },
            )
            response = JSONResponse(
                {
                    "detail": "服务暂时不可用",
                    "code": "INTERNAL_ERROR",
                    "request_id": request.state.request_id,
                },
                status_code=500,
            )
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith(
            (
                "/admin",
                "/api/v1/auth",
                "/api/v1/admin",
                "/api/v2/admin",
                "/api/v2/learning",
                "/api/v2/practice/papers",
            )
        ):
            response.headers["Cache-Control"] = "no-store"
        logging.getLogger("island.http").info(
            "request",
            extra={
                "fields": {
                    "request_id": request.state.request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "user_id": str(request.state.principal.id)
                    if getattr(request.state, "principal", None)
                    else None,
                    "duration_ms": round((time.monotonic() - started) * 1000, 1),
                }
            },
        )
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc):
        if exc.status_code == 401 and request.url.path.startswith("/admin/"):
            return RedirectResponse("/admin/login", status_code=303)
        code = (exc.headers or {}).get("X-Error-Code", f"HTTP_{exc.status_code}")
        return JSONResponse(
            {
                "detail": exc.detail,
                "code": code,
                "request_id": request.state.request_id,
            },
            status_code=exc.status_code,
            headers=exc.headers,
        )
