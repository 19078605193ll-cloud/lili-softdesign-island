import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException


async def test_committed_upload_survives_response_failure(monkeypatch):
    from app.imports import api, markdown_storage, markdown_workflow
    batch = SimpleNamespace(id=uuid.uuid4())
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    storage = SimpleNamespace(max_bytes=100, resolve=Mock(), remove_batch=Mock())
    upload = SimpleNamespace(read=AsyncMock(return_value=b'example'), close=AsyncMock(), filename='source.md')
    monkeypatch.setattr(api, 'create_batch_record', AsyncMock(return_value=batch))
    monkeypatch.setattr(api, 'get_batch_read', AsyncMock(side_effect=RuntimeError('response query failed')))
    monkeypatch.setattr(markdown_storage, 'unpack', Mock())
    monkeypatch.setattr(markdown_workflow, 'initialize', AsyncMock())
    with pytest.raises(HTTPException):
        await api.create_import_batch(session, storage, upload)
    storage.remove_batch.assert_not_called()
