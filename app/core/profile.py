"""Self-service profile updates and validated, private avatar storage."""

import io
import logging
import math
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.config import get_settings
from app.core.errors import fail
from app.core.identifiers import identifiers_available, normalize_username
from app.core.security import current_user
from app.imports.dependencies import SessionDependency
from app.models import User

router = APIRouter(prefix="/api/v1/auth/me", tags=["profile"])


def profile_read(row, principal):
    return {
        "id": str(row.id),
        "username": row.username,
        "email": row.email,
        "permissions": sorted(principal.permissions),
        "avatar_url": f"/api/v1/auth/me/avatar/{row.avatar_key}"
        if row.avatar_key
        else None,
    }


class Rename(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str

    @field_validator("username")
    @classmethod
    def normalize(cls, value):
        return normalize_username(value)


async def own_row(session, principal):
    return await session.scalar(
        select(User).where(User.id == principal.id).with_for_update()
    )


@router.patch("")
async def rename(payload: Rename, request: Request, session: SessionDependency):
    principal = await current_user(request)
    await identifiers_available(session, payload.username, user_id=principal.id)
    row = await own_row(session, principal)
    session.info["audit_summary"] = {
        "old_username": row.username,
        "new_username": payload.username,
    }
    row.username = payload.username
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise fail(409, "USERNAME_TAKEN", "该用户名已被使用，请换一个") from exc
    return profile_read(row, principal)


def avatar_path(key):
    # All keys are generated UUIDs, never user-supplied paths.
    return (
        Path(get_settings().import_storage_root).resolve()
        / "avatars"
        / (str(uuid.UUID(key)) + ".webp")
    )


def cleanup(key):
    if not key:
        return
    try:
        avatar_path(key).unlink(missing_ok=True)
    except OSError:
        logging.getLogger("island").warning(
            "avatar_cleanup_pending", extra={"fields": {"avatar_key": key}}
        )


def render_avatar(data, content_type, x, y, size):
    try:
        with Image.open(io.BytesIO(data)) as original:
            expected = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
            if (
                original.format not in expected
                or content_type != expected[original.format]
            ):
                raise ValueError("type")
            if (
                original.width * original.height > 20_000_000
                or getattr(original, "n_frames", 1) != 1
            ):
                raise ValueError("dimensions")
            image = ImageOps.exif_transpose(original)
            if (
                not all(math.isfinite(v) for v in (x, y, size))
                or min(x, y) < 0
                or size < 1
                or x + size > image.width + 0.01
                or y + size > image.height + 0.01
            ):
                raise ValueError("crop")
            image = (
                image.convert("RGB")
                .crop((x, y, x + size, y + size))
                .resize((512, 512), Image.Resampling.LANCZOS)
            )
            image.info.clear()
            output = io.BytesIO()
            image.save(output, format="WEBP", quality=88)
            return output.getvalue()
    except (
        ValueError,
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
    ) as exc:
        raise fail(
            422,
            "INVALID_AVATAR",
            "请选择有效的静态JPEG、PNG或WebP图片（不超过2000万像素），并调整裁剪范围",
        ) from exc


@router.put("/avatar")
async def upload_avatar(
    request: Request,
    session: SessionDependency,
    file: Annotated[UploadFile, File()],
    x: Annotated[float, Form()],
    y: Annotated[float, Form()],
    size: Annotated[float, Form()],
):
    principal = await current_user(request)
    data = await file.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024:
        raise fail(413, "AVATAR_TOO_LARGE", "图片不能超过5MB")
    rendered = render_avatar(data, file.content_type, x, y, size)
    row = await own_row(session, principal)
    old, key = row.avatar_key, str(uuid.uuid4())
    path = avatar_path(key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as output:
            output.write(rendered)
        row.avatar_key = key
        await session.commit()
    except (OSError, SQLAlchemyError):
        await session.rollback()
        cleanup(key)
        raise fail(503, "AVATAR_SAVE_FAILED", "头像保存失败，原头像已保留，请重试")
    cleanup(old)
    return profile_read(row, principal)


@router.delete("/avatar")
async def remove_avatar(request: Request, session: SessionDependency):
    principal = await current_user(request)
    row = await own_row(session, principal)
    old = row.avatar_key
    row.avatar_key = None
    await session.commit()
    cleanup(old)
    return profile_read(row, principal)


@router.get("/avatar/{key}")
async def read_avatar(key: uuid.UUID, request: Request, session: SessionDependency):
    principal = await current_user(request)
    row = await session.get(User, principal.id)
    path = avatar_path(str(key))
    if row.avatar_key != str(key) or not path.is_file():
        raise fail(404, "AVATAR_NOT_FOUND", "头像不存在")
    return FileResponse(
        path,
        media_type="image/webp",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
