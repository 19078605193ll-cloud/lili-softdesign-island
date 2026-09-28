from functools import lru_cache
from typing import Annotated
from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import Settings, get_settings
from app.db import get_session
from app.imports.ai import (
    AIConfigurationError,
    OpenAICompatibleQuestionClient,
    QuestionAIClient,
)
from app.imports.storage import ImportStorageError, LocalImportStorage
from app.imports.service import (
    ImportNotFoundError,
    ImportConflictError,
    ImportWorkflowError,
)

SessionDependency = Annotated[AsyncSession, Depends(get_session)]


@lru_cache
def get_import_storage() -> LocalImportStorage:
    settings = get_settings()
    return LocalImportStorage(
        settings.import_storage_root,
        max_file_size_mb=settings.import_max_file_size_mb,
        render_dpi=settings.pdf_render_dpi,
        libreoffice_path=settings.libreoffice_path,
        use_local_ocr=settings.import_use_local_ocr,
    )


def get_ai_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> QuestionAIClient:
    try:
        return OpenAICompatibleQuestionClient(settings)
    except AIConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


StorageDependency = Annotated[LocalImportStorage, Depends(get_import_storage)]
AIClientDependency = Annotated[QuestionAIClient, Depends(get_ai_client)]


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, ImportNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ImportConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (ImportWorkflowError, ImportStorageError)):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail="question import failed")
