import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import Settings, get_settings
from app.core.ai_options import thinking_options
from app.recall import grading


def blank(answer="更适合"):
    return SimpleNamespace(
        key="pipeline",
        label="流水线适用性",
        answer=answer,
        acceptable_answers=[],
        normalization={},
    )


@pytest.fixture
def model_client(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_api_key", "test-only")
    monkeypatch.setattr(settings, "ai_recall_model", "qwen3.8-flash")
    create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"verdict":"correct"}'),
                    finish_reason="stop",
                )
            ],
            model="qwen3.8-flash",
            usage=None,
        )
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    constructor = MagicMock()
    constructor.return_value.__aenter__ = AsyncMock(return_value=client)
    constructor.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(grading, "AsyncOpenAI", constructor)
    return create, constructor


async def test_context_and_qwen_request(model_client):
    create, constructor = model_client
    content = {
        "type": "table",
        "columns": ["RISC", "CISC"],
        "rows": [[{"text": "指令简单，"}, {"blank_key": "pipeline"}]],
    }
    assert await grading.semantic_grade(
        content=content, blank=blank(), user_answer="更合适"
    )
    request = create.call_args.kwargs
    assert request["model"] == "qwen3.8-flash"
    assert request["extra_body"] == {"enable_thinking": False}
    assert "reasoning_effort" not in request
    data = json.loads(request["messages"][1]["content"])
    assert data["content"] == content
    assert data["reference_answer"] == "更适合"
    assert data["user_answer"] == "更合适"
    assert constructor.call_args.kwargs["max_retries"] == 0
    assert constructor.call_args.kwargs["timeout"] == 10


@pytest.mark.parametrize(
    "raw",
    [
        "invalid",
        "[]",
        '{"verdict":true}',
        '{"verdict":"uncertain"}',
        '{"verdict":"correct","answer":"leaked"}',
        "{}",
    ],
)
async def test_invalid_or_uncertain_is_retryable(model_client, raw):
    create, _ = model_client
    create.return_value.choices[0].message.content = raw
    with pytest.raises(grading.GradingUnavailable):
        await grading.semantic_grade(content={}, blank=blank(), user_answer="更合适")
    assert create.await_count == 1


async def test_incorrect_and_model_fallback(model_client, monkeypatch):
    create, _ = model_client
    monkeypatch.setattr(get_settings(), "ai_recall_model", None)
    monkeypatch.setattr(get_settings(), "ai_text_model", "qwen3.8-flash")
    create.return_value.choices[0].message.content = '{"verdict":"incorrect"}'
    assert not await grading.semantic_grade(
        content={}, blank=blank(), user_answer="不适合"
    )
    assert create.call_args.kwargs["model"] == "qwen3.8-flash"


async def test_total_timeout_and_cleanup(model_client, monkeypatch):
    create, constructor = model_client
    monkeypatch.setattr(get_settings(), "recall_grading_timeout_seconds", 0.01)
    cancelled = []

    async def slow(**kwargs):
        try:
            await asyncio.sleep(5)
        finally:
            cancelled.append(True)

    create.side_effect = slow
    with pytest.raises(grading.GradingUnavailable):
        await grading.semantic_grade(content={}, blank=blank(), user_answer="更合适")
    assert cancelled == [True]
    assert create.await_count == 1
    constructor.return_value.__aexit__.assert_awaited_once()


async def test_missing_configuration(model_client, monkeypatch):
    create, _ = model_client
    monkeypatch.setattr(get_settings(), "ai_api_key", None)
    with pytest.raises(grading.GradingUnavailable):
        await grading.semantic_grade(content={}, blank=blank(), user_answer="更合适")
    create.assert_not_awaited()


@pytest.mark.parametrize("finish", ["length", "content_filter"])
async def test_incomplete_output_is_retryable(model_client, finish):
    create, _ = model_client
    create.return_value.choices[0].finish_reason = finish
    with pytest.raises(grading.GradingUnavailable):
        await grading.semantic_grade(content={}, blank=blank(), user_answer="更合适")


async def test_cancellation_propagates(model_client):
    create, _ = model_client
    create.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await grading.semantic_grade(content={}, blank=blank(), user_answer="更合适")


async def test_text_assist_qwen_parameters(monkeypatch):
    from app.imports import text_assist

    settings = get_settings()
    monkeypatch.setattr(settings, "ai_api_key", "test-only")
    monkeypatch.setattr(settings, "ai_text_model", "qwen3.8-flash")
    create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]
        )
    )
    constructor = MagicMock()
    constructor.return_value.__aenter__ = AsyncMock(
        return_value=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
    )
    constructor.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(text_assist, "AsyncOpenAI", constructor)
    monkeypatch.setattr(
        text_assist,
        "validate_suggestion",
        lambda *args: SimpleNamespace(model_dump=lambda: {"validated": True}),
    )
    assert await text_assist.suggest("synthetic source") == {"validated": True}
    assert create.call_args.kwargs["model"] == "qwen3.8-flash"
    assert create.call_args.kwargs["extra_body"] == {"enable_thinking": False}


@pytest.mark.parametrize(
    "model,expected",
    [
        ("qwen3.8-flash", {"extra_body": {"enable_thinking": False}}),
        ("QWEN3.8-FLASH", {"extra_body": {"enable_thinking": False}}),
        ("gpt-6.1-sol", {}),
        ("gemini", {}),
    ],
)
def test_model_specific_options(model, expected):
    assert thinking_options(model) == expected


def test_thinking_defaults():
    settings = Settings(_env_file=None)
    assert settings.ai_text_enable_thinking is False
    assert settings.ai_recall_enable_thinking is False
    assert settings.recall_semantic_grading_enabled is True
    assert settings.recall_grading_timeout_seconds == 10
