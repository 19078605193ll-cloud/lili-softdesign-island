import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from openai import APIStatusError, APITimeoutError

from app.config import Settings, get_settings
from app.h5.ai_worker import LearningModelsExhausted, completion


def response(text="回答", finish="stop", model="actual-model"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content=text), finish_reason=finish)
        ],
        model=model,
        usage=None,
    )


def status_error(status, code=None):
    return APIStatusError(
        "provider error",
        response=httpx.Response(
            status, request=httpx.Request("POST", "https://model.invalid")
        ),
        body={"error": {"code": code}},
    )


@pytest.fixture
def model_client(monkeypatch):
    settings = get_settings()
    for key, value in {
        "ai_api_key": "test-only",
        "ai_tutor_model": "primary",
        "ai_tutor_fallback_models": ["qwen", "gemini"],
        "ai_tutor_reasoning_effort": "low",
        "ai_tutor_timeout_seconds": 30.0,
    }.items():
        monkeypatch.setattr(settings, key, value)
    client = AsyncMock()
    client.chat.completions.create.return_value = response()
    constructor = MagicMock()
    constructor.return_value.__aenter__ = AsyncMock(return_value=client)
    constructor.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr("app.h5.ai_worker.AsyncOpenAI", constructor)
    return client.chat.completions.create, constructor


async def test_primary_success_configuration_and_metadata(model_client):
    create, constructor = model_client
    messages = [
        {"role": "system", "content": "context"},
        {"role": "user", "content": "question"},
    ]
    metadata = {}
    assert await completion(messages, metadata=metadata) == "回答"
    assert create.await_count == 1
    assert create.call_args.kwargs["messages"] == messages
    assert create.call_args.kwargs["reasoning_effort"] == "low"
    assert metadata == {"model": "actual-model", "requested_model": "primary"}
    assert constructor.call_args.kwargs["timeout"] == 30.0
    assert constructor.call_args.kwargs["max_retries"] == 0


@pytest.mark.parametrize("failures", [1, 2])
async def test_ordered_fallback_and_fresh_primary(model_client, failures):
    create, _ = model_client
    create.side_effect = [status_error(503)] * failures + [response()]
    assert await completion([]) == "回答"
    assert [c.kwargs["model"] for c in create.call_args_list] == [
        "primary",
        "qwen",
        "gemini",
    ][: failures + 1]
    create.side_effect = None
    await completion([])
    assert create.call_args.kwargs["model"] == "primary"


@pytest.mark.parametrize(
    "error",
    [
        APITimeoutError(request=httpx.Request("POST", "https://model.invalid")),
        httpx.ConnectError("connection failed"),
        status_error(408),
        status_error(429),
        status_error(500),
        status_error(404, "model_not_found"),
        status_error(403, "model_unavailable"),
    ],
)
async def test_transient_errors_switch(model_client, error):
    create, _ = model_client
    create.side_effect = [error, response()]
    assert await completion([]) == "回答"
    assert create.await_count == 2


@pytest.mark.parametrize(
    "error",
    [
        status_error(401),
        status_error(402),
        status_error(403),
        status_error(404),
        status_error(400),
        status_error(429, "insufficient_quota"),
        status_error(500, "insufficient_balance"),
        RuntimeError("programming error"),
    ],
)
async def test_terminal_errors_stop(model_client, error):
    create, _ = model_client
    create.side_effect = error
    with pytest.raises(type(error)):
        await completion([])
    assert create.await_count == 1


@pytest.mark.parametrize(
    "invalid",
    [
        response(""),
        response(" "),
        response("a" * 30001),
        response("partial", "length"),
        SimpleNamespace(choices=[]),
    ],
)
async def test_invalid_response_switches(model_client, invalid):
    create, _ = model_client
    create.side_effect = [invalid, response()]
    assert await completion([]) == "回答"
    assert create.await_count == 2


@pytest.mark.parametrize("invalid", ["invalid-json", "[]", "null"])
async def test_json_and_reasoning_preserved_across_fallback(model_client, invalid):
    create, _ = model_client
    create.side_effect = [response(invalid), response('{"valid": false}')]
    assert await completion([], structured=True) == {"valid": False}
    for call in create.call_args_list:
        assert call.kwargs["response_format"] == {"type": "json_object"}
        assert call.kwargs["reasoning_effort"] == "low"


async def test_wall_clock_deadline_exhaustion_and_cleanup(model_client, monkeypatch):
    create, constructor = model_client
    monkeypatch.setattr(get_settings(), "ai_tutor_timeout_seconds", 0.01)
    cancelled = []

    async def slow(**kwargs):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(kwargs["model"])

    create.side_effect = slow
    with pytest.raises(LearningModelsExhausted) as result:
        async with asyncio.timeout(1):
            await completion([])
    assert result.value.timed_out
    assert cancelled == ["primary", "qwen", "gemini"]
    constructor.return_value.__aexit__.assert_awaited_once()


async def test_cancel_does_not_fallback(model_client):
    create, _ = model_client
    create.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await completion([])
    assert create.await_count == 1


async def test_deduplication_and_blank_reasoning(model_client, monkeypatch):
    create, _ = model_client
    monkeypatch.setattr(
        get_settings(), "ai_tutor_fallback_models", [" primary ", "qwen", "qwen", ""]
    )
    monkeypatch.setattr(get_settings(), "ai_tutor_reasoning_effort", "")
    create.side_effect = status_error(503)
    with pytest.raises(LearningModelsExhausted) as result:
        await completion([])
    assert not result.value.timed_out
    assert [c.kwargs["model"] for c in create.call_args_list] == ["primary", "qwen"]
    assert "reasoning_effort" not in create.call_args.kwargs


def test_config_parses_environment(monkeypatch):
    monkeypatch.setenv("AI_TUTOR_FALLBACK_MODELS", json.dumps(["qwen", "gemini"]))
    assert Settings(_env_file=None).ai_tutor_fallback_models == ["qwen", "gemini"]


async def test_no_backups_uses_text_model(model_client, monkeypatch):
    create, _ = model_client
    monkeypatch.setattr(get_settings(), "ai_tutor_model", None)
    monkeypatch.setattr(get_settings(), "ai_text_model", "text-model")
    monkeypatch.setattr(get_settings(), "ai_tutor_fallback_models", [])
    create.side_effect = status_error(503)
    with pytest.raises(LearningModelsExhausted):
        await completion([])
    assert create.await_count == 1
    assert create.call_args.kwargs["model"] == "text-model"


async def test_logs_include_context_without_private_content(model_client, caplog):
    import logging

    create, _ = model_client
    create.side_effect = [status_error(503), response()]
    with caplog.at_level(logging.INFO, logger="island"):
        await completion(
            [{"role": "user", "content": "private-question"}],
            job_id="test-job",
            stage="variant_generate",
        )
    records = [r.fields for r in caplog.records if r.name == "island"]
    assert records[0]["next_model"] == "qwen"
    assert records[1]["outcome"] == "succeeded"
    assert all(
        r["job_id"] == "test-job" and r["stage"] == "variant_generate" for r in records
    )
    assert "private-question" not in str(records)
    assert "test-only" not in str(records)
