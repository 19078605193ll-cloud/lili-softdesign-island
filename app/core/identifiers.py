"""Shared account identifiers; serialize changes across both unique namespaces."""

import unicodedata

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func, or_, select

from app.core.errors import fail
from app.models import User


def normalize_username(value: str) -> str:
    value = value.strip().casefold()
    if not 1 <= len(value) <= 100 or any(
        unicodedata.category(c).startswith("C") for c in value
    ):
        raise ValueError("用户名需为1—100字，不能包含控制字符")
    return value


def normalize_email(value: str) -> str:
    try:
        email = validate_email(
            value.strip(), check_deliverability=False
        ).normalized.casefold()
    except EmailNotValidError as exc:
        raise ValueError("请输入有效的邮箱地址") from exc
    if len(email) > 254:
        raise ValueError("邮箱地址过长")
    return email


async def identifiers_available(
    session, username: str, email: str | None = None, *, user_id=None
):
    # A single transaction lock also prevents concurrent username/email cross-collisions.
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtext("account-identifiers")))
    )
    values = {username, email} - {None}
    rows = await session.scalars(
        select(User).where(
            or_(User.username.in_(values), User.email.in_(values)),
            *([User.id != user_id] if user_id else []),
        )
    )
    for row in rows:
        if username in {row.username, row.email}:
            raise fail(409, "USERNAME_TAKEN", "该用户名已被使用，请换一个")
        if email and email in {row.username, row.email}:
            raise fail(409, "EMAIL_TAKEN", "该邮箱已被使用，请换一个或直接登录")
