from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.config import get_settings
from app.h5.ai_worker import completion


async def test_learning_completion_uses_configured_timeout(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_api_key", "test-only")
    monkeypatch.setattr(settings, "ai_tutor_model", "test-model")
    monkeypatch.setattr(settings, "ai_timeout_seconds", 45.0)
    client = AsyncMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="回答"))],
        model="test-model", usage=None,
    )
    constructor = MagicMock()
    constructor.return_value.__aenter__ = AsyncMock(return_value=client)
    constructor.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr("app.h5.ai_worker.AsyncOpenAI", constructor)
    assert await completion([{"role": "user", "content": "问题"}]) == "回答"
    assert constructor.call_args.kwargs["timeout"] == 45.0
    assert constructor.call_args.kwargs["max_retries"] == 0
