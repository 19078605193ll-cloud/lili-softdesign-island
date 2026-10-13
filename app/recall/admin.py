from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.config import get_settings
from app.core.security import require
from app.imports.admin import templates
from app.imports.dependencies import SessionDependency
from app.models import ExamSubject, KnowledgeNode

router = APIRouter()


@router.get("/admin/recalls", response_class=HTMLResponse)
async def page(request: Request, session: SessionDependency):
    user = await require(request, "recalls:read")
    subjects = list(
        await session.scalars(
            select(ExamSubject).where(ExamSubject.is_active.is_(True))
        )
    )
    nodes = list(
        await session.scalars(
            select(KnowledgeNode)
            .where(KnowledgeNode.node_type == "topic", KnowledgeNode.status == "active")
            .order_by(KnowledgeNode.code)
        )
    )
    return templates.TemplateResponse(
        request=request,
        name="recalls.html",
        context={
            "subjects": [{"id": str(s.id), "name": s.name} for s in subjects],
            "nodes": [
                {
                    "id": str(n.id),
                    "subject_id": str(n.subject_id),
                    "name": n.name,
                    "code": n.code,
                }
                for n in nodes
            ],
            "permissions": sorted(user.permissions),
            "recall_ai_enabled": bool(
                get_settings().recall_ai_generation_enabled
                and get_settings().ai_api_key
                and (get_settings().ai_recall_model or get_settings().ai_text_model)
            ),
        },
    )
