from collections.abc import AsyncIterator
from fastapi import Request
from sqlalchemy import event
from sqlalchemy.orm import Session

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

settings = get_settings()
engine = create_async_engine(
    settings.database_url, echo=settings.sql_echo, pool_pre_ping=True
)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


def attach_audit(session: AsyncSession, request: Request) -> None:
    session.info.pop("audit", None)
    session.info["expected_revision"] = request.headers.get("if-match")
    session.info["read_only_preview"] = request.url.path.endswith(
        ("/preview", "/repair-preview")
    )
    user = getattr(request.state, "principal", None)
    if user and request.method not in {"GET", "HEAD", "OPTIONS"}:
        route = request.scope.get("route")
        session.info["audit"] = dict(
            user_id=user.id,
            action=request.method + ":" + getattr(route, "name", "write")[:80],
            object_id=request.url.path,
            request_id=request.state.request_id,
        )


@event.listens_for(Session, "before_flush")
def audit_write(session, flush_context, instances):
    from app.models import AuditEvent

    data = session.info.get("audit")
    if (
        data
        and not session.info.get("audit_added")
        and (session.new or session.dirty or session.deleted)
    ):
        versions = {
            str(obj.id): {
                name: getattr(obj, name)
                for name in ("revision", "content_version")
                if hasattr(obj, name)
            }
            for obj in list(session.dirty)
            if hasattr(obj, "id")
            and (hasattr(obj, "revision") or hasattr(obj, "content_version"))
        }
        session.add(
            AuditEvent(**data, summary={"result": "committed", "versions": versions})
        )
        session.info["audit_added"] = True


@event.listens_for(Session, "before_commit")
def audit_bulk_write(session):
    # SQL DELETE/UPDATE does not necessarily pass through ORM before_flush.
    from app.models import AuditEvent

    data = session.info.get("audit")
    if data and not session.info.get("audit_added"):
        session.add(AuditEvent(**data, summary={"result": "committed"}))
        session.info["audit_added"] = True


@event.listens_for(Session, "after_transaction_end")
def reset_audit(session, transaction):
    if transaction.parent is None:
        session.info.pop("audit_added", None)
        session.info.pop("revision_bumped", None)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        attach_audit(session, request)
        yield session
