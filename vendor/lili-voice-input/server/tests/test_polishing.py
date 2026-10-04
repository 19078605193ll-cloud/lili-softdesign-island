"""Provider request contracts and preservation of ASR text on failure.

Run with ``python -m unittest discover -s tests`` in the voice server environment.
"""

import json
import os
import unittest
from unittest.mock import patch

import httpx
from openai import AsyncOpenAI

from lili_voice_input.config import Settings
from lili_voice_input.providers.openai_polisher import OpenAICompatiblePolisher
from lili_voice_input.services.polishing import PolishingService


class PolishingTests(unittest.IsolatedAsyncioTestCase):
    def settings(self, **overrides):
        with patch.dict(os.environ, {}, clear=True):
            return Settings(
                _env_file=None,
                polish_api_key="test-key",
                polish_base_url="https://polish.example/v1",
                polish_model="qwen3.8-flash",
                **overrides,
            )

    def test_thinking_disabled_by_default(self):
        self.assertIs(self.settings().polish_enable_thinking, False)

    def test_environment_false_is_a_boolean(self):
        with patch.dict(os.environ, {"POLISH_ENABLE_THINKING": "false"}, clear=True):
            settings = Settings(_env_file=None)
        self.assertIs(settings.polish_enable_thinking, False)

    async def check_request(self, settings, expected):
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(200, json={
                "id": "test-completion", "object": "chat.completion",
                "created": 0, "model": settings.polish_model,
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": "哈希表是什么意思？"}}],
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            async with AsyncOpenAI(api_key="test-key", base_url=settings.polish_base_url,
                                   http_client=http, max_retries=0) as client:
                provider = OpenAICompatiblePolisher(settings, client)
                result = await provider.polish("哈希表是什么意思")
        self.assertEqual(result, "哈希表是什么意思？")
        self.assertEqual(len(requests), 1)
        self.assertEqual(str(requests[0].url), "https://polish.example/v1/chat/completions")
        body = json.loads(requests[0].content)
        for key, value in expected.items():
            self.assertEqual(body[key], value)
        self.assertNotIn("extra_body", body)
        return body

    async def test_qwen_default_serializes_false(self):
        body = await self.check_request(self.settings(), {"enable_thinking": False})
        self.assertIs(body["enable_thinking"], False)
        self.assertNotIn("thinking", body)

    async def test_qwen_explicit_false_serializes_false(self):
        body = await self.check_request(
            self.settings(polish_enable_thinking=False), {"enable_thinking": False},
        )
        self.assertIs(body["enable_thinking"], False)

    async def test_deepseek_keeps_disabled_parameter(self):
        settings = self.settings().model_copy(update={"polish_model": "deepseek-chat"})
        body = await self.check_request(settings, {"thinking": {"type": "disabled"}})
        self.assertNotIn("enable_thinking", body)

    async def test_openai_model_omits_vendor_thinking_parameters(self):
        settings = self.settings().model_copy(update={"polish_model": "gpt-4.1-mini"})
        body = await self.check_request(settings, {})
        self.assertNotIn("enable_thinking", body)
        self.assertNotIn("thinking", body)

    async def test_provider_failures_preserve_complete_asr_text(self):
        original = "哈希表是什么意思？\n请举例解释碰撞处理。"
        scenarios = [
            (401, {"error": {"code": "invalid_api_key", "message": "Unauthorized"}}, "configuration_error"),
            (200, {"id": "test", "object": "chat.completion", "created": 0,
                   "model": "qwen3.8-flash", "choices": [
                       {"index": 0, "finish_reason": "stop",
                        "message": {"role": "assistant", "content": ""}},
                   ]}, "empty_output"),
        ]
        for status, payload, reason in scenarios:
            with self.subTest(reason=reason):
                async with httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda request: httpx.Response(status, json=payload),
                )) as http:
                    async with AsyncOpenAI(api_key="test-key", base_url="https://polish.example/v1",
                                           http_client=http, max_retries=0) as client:
                        service = PolishingService(
                            OpenAICompatiblePolisher(self.settings(), client), enabled=True,
                        )
                        result = await service.polish(original)
                self.assertEqual(result.text, original)
                self.assertEqual(result.status, "fallback")
                self.assertEqual(result.fallback_reason, reason)
                self.assertFalse(result.polished)


if __name__ == "__main__":
    unittest.main()
