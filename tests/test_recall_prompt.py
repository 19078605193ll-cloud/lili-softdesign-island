import copy
import hashlib
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.recall import generation
from tests.test_recall_generation import generated


@pytest.fixture
def prompt_file(tmp_path, monkeypatch):
    path = tmp_path / "主动回忆.md"
    monkeypatch.setattr(generation, "PROMPT_PATH", path)
    return path


def test_prompt_bom_snapshot_and_contract(prompt_file):
    prompt_file.write_text("请生成清晰的知识结构。", encoding="utf-8-sig")
    snapshot = generation.load_prompt()
    assert snapshot["text"] == "请生成清晰的知识结构。"
    assert snapshot["sha256"] == hashlib.sha256(snapshot["text"].encode()).hexdigest()
    system = generation._system_prompt(snapshot)
    assert system.startswith(snapshot["text"])
    assert "JSON Schema" in system and "evidence_references" in system
    assert "相同语义的空位必须保留原 key" in system


@pytest.mark.parametrize("kind,label", [("sentence","要点填空"),("table","表格对比"),("relation","关系推演"),("auto","自动选择")])
def test_prompt_slots_are_filled_once(prompt_file, kind, label):
    template = "知识点={{knowledge_point}}；类型={{ memory_type }}；补充={{extra_requirements}}"
    prompt_file.write_text(template, encoding="utf-8")
    data = {"node":{"name":"事务ACID"}, "type":kind, "instruction":"精简 {{memory_type}}"}
    result = generation.render_prompt(generation.load_prompt(), data)
    assert "知识点=事务ACID" in result["text"]
    assert "类型=" + label in result["text"]
    assert "补充=精简 {{memory_type}}" in result["text"]
    assert result["template_text"] == template
    assert result["template_sha256"] != result["sha256"]
    assert generation.render_prompt(result, data) == result


def test_optional_instruction_slot(prompt_file):
    prompt_file.write_text("{{extra_requirements}}", encoding="utf-8")
    assert generation.render_prompt(generation.load_prompt(), {"type":"auto"})["text"] == "无额外要求"


@pytest.mark.parametrize("state", ["missing", "empty", "whitespace", "invalid", "directory"])
async def test_bad_prompt_never_calls_model(prompt_file, monkeypatch, state):
    if state == "empty":
        prompt_file.touch()
    elif state == "whitespace":
        prompt_file.write_text("\ufeff \n\t", encoding="utf-8")
    elif state == "invalid":
        prompt_file.write_bytes(b"\xff\xfe\xff")
    elif state == "directory":
        prompt_file.mkdir()
    def forbidden(**kwargs):
        pytest.fail("Model must not be invoked without a configured prompt")
    monkeypatch.setattr(generation, "AsyncOpenAI", forbidden)
    with pytest.raises(generation.RecallPromptError) as error:
        await generation.generate({"type": "auto"})
    assert error.value.code == "RECALL_PROMPT_NOT_CONFIGURED"


@pytest.mark.parametrize("model", ["fixture", "qwen3.8-flash"])
async def test_retry_pins_prompt_and_new_task_reads_changes(prompt_file, monkeypatch, model):
    prompt_file.write_text("第一份提示词", encoding="utf-8")
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_api_key", "fixture")
    monkeypatch.setattr(settings, "ai_recall_model", model)
    calls = []
    async def create(**kwargs):
        assert kwargs["model"] == model
        if model.startswith("qwen"):
            assert kwargs["extra_body"] == {"enable_thinking": False}
        else:
            assert "extra_body" not in kwargs
        calls.append(copy.deepcopy(kwargs["messages"]))
        prompt_file.write_text("第二份提示词", encoding="utf-8")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content="invalid" if len(calls) == 1 else generated().model_dump_json()
        ))])
    class Client:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
    monkeypatch.setattr(generation, "AsyncOpenAI", Client)
    data = {"type": "auto"}
    await generation.generate(data)
    assert calls[0][0] == calls[1][0]
    assert calls[0][0]["content"].startswith("第一份提示词")
    assert "prompt" not in calls[0][1]["content"]
    assert data["prompt"]["text"] == "第一份提示词"
    await generation.generate({"type": "auto"})
    assert calls[2][0]["content"].startswith("第二份提示词")
