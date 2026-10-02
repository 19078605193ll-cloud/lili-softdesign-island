"""Administrator-only account status and aggregated formal learning activity."""

import uuid

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, StrictBool
from sqlalchemy import case, func, or_, select

from app.core.errors import fail
from app.core.security import require
from app.imports.admin import templates
from app.imports.dependencies import SessionDependency
from app.models import Attempt, AttemptPart, User, UserRole

router = APIRouter(tags=["user-management"])


async def status_lock(session):
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtext("account-status")))
    )


async def ensure_can_disable(session, row, *, actor_id=None):
    if row.id == actor_id:
        raise fail(409, "CANNOT_DISABLE_SELF", "不能停用当前登录的账号")
    role = await session.scalar(
        select(UserRole.role).where(
            UserRole.user_id == row.id, UserRole.role == "administrator"
        )
    )
    if row.active and role:
        count = await session.scalar(
            select(func.count())
            .select_from(User)
            .join(UserRole)
            .where(User.active.is_(True), UserRole.role == "administrator")
        )
        if count <= 1:
            raise fail(409, "LAST_ADMINISTRATOR", "不能停用最后一个启用的管理员")


@router.get("/admin/users")
async def users_page(request: Request):
    await require(request, "users:read")
    return templates.TemplateResponse(request=request, name="users.html", context={})


@router.get("/api/v1/admin/users")
async def users_list(
    request: Request,
    session: SessionDependency,
    q: str = Query("", max_length=254),
    active: bool | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    await require(request, "users:read")
    filters = []
    if q.strip():
        # Literal search: '%' and '_' in an identifier are not wildcards.
        term = (
            q.strip()
            .casefold()
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        filters.append(
            or_(
                User.username.ilike("%" + term + "%", escape="\\"),
                User.email.ilike("%" + term + "%", escape="\\"),
            )
        )
    if active is not None:
        filters.append(User.active == active)
    total = await session.scalar(select(func.count()).select_from(User).where(*filters))
    users = list(
        await session.scalars(
            select(User)
            .where(*filters)
            .order_by(User.created_at.desc(), User.id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
    )
    ids = [u.id for u in users]
    roles = {}
    for uid, role in await session.execute(
        select(UserRole.user_id, UserRole.role)
        .where(UserRole.user_id.in_(ids))
        .order_by(UserRole.role)
    ):
        roles.setdefault(uid, []).append(role)
    attempts = {
        uid: (count, recent)
        for uid, count, recent in await session.execute(
            select(
                Attempt.user_id, func.count(Attempt.id), func.max(Attempt.created_at)
            )
            .where(Attempt.user_id.in_(ids))
            .group_by(Attempt.user_id)
        )
    }
    parts = {
        uid: (count, correct)
        for uid, count, correct in await session.execute(
            select(
                Attempt.user_id,
                func.count(),
                func.sum(case((AttemptPart.correct.is_(True), 1), else_=0)),
            )
            .join(AttemptPart, AttemptPart.attempt_id == Attempt.id)
            .where(Attempt.user_id.in_(ids))
            .group_by(Attempt.user_id)
        )
    }
    items = []
    for u in users:
        count, recent = attempts.get(u.id, (0, None))
        part_count, correct = parts.get(u.id, (0, 0))
        items.append(
            {
                "id": str(u.id),
                "username": u.username,
                "email": u.email,
                "active": u.active,
                "roles": roles.get(u.id, []),
                "created_at": u.created_at,
                "last_login_at": u.last_login_at,
                "last_attempt_at": recent,
                "attempt_count": count,
                "accuracy": round(100 * correct / part_count, 1)
                if part_count
                else None,
            }
        )
    return {"items": items, "total": total, "page": page, "limit": limit}


class StatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active: StrictBool


@router.patch("/api/v1/admin/users/{uid}/status")
async def user_status(
    uid: uuid.UUID, payload: StatusIn, request: Request, session: SessionDependency
):
    actor = await require(request, "users:manage")
    await status_lock(session)
    row = await session.scalar(select(User).where(User.id == uid).with_for_update())
    if not row:
        raise fail(404, "USER_NOT_FOUND", "用户不存在")
    if not payload.active:
        await ensure_can_disable(session, row, actor_id=actor.id)
    if row.active != payload.active:
        session.info["audit_summary"] = {
            "user_id": str(row.id),
            "old_active": row.active,
            "active": payload.active,
        }
        row.active = payload.active
        if not payload.active:
            row.auth_version += 1
        await session.commit()
    return {"id": str(row.id), "active": row.active}
