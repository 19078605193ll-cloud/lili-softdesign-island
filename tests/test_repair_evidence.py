from __future__ import annotations

import pytest

from app.imports.schemas import VisionAnswer, VisionAnswerPage
from app.imports.service import ImportWorkflowError, _verify_repair_against_position


def test_visual_repair_cannot_override_positioned_answer() -> None:
    position = {"answers": {58: {"keys": ["D"], "explanation": "哈夫曼编码"}}}
    wrong = VisionAnswerPage(answers=[VisionAnswer(
        question_no=58, correct_option_keys=["B", "C"],
        explanation_markdown="SQL 解析",
    )])
    with pytest.raises(ImportWorkflowError, match="58"):
        _verify_repair_against_position("answers", wrong, [58], position)

    wrong_explanation = VisionAnswerPage(answers=[VisionAnswer(
        question_no=58, correct_option_keys=["D"],
        explanation_markdown="SQL 查询优化与索引策略",
    )])
    with pytest.raises(ImportWorkflowError, match="explanation"):
        _verify_repair_against_position("answers", wrong_explanation, [58], position)

    correct = VisionAnswerPage(answers=[VisionAnswer(
        question_no=58, correct_option_keys=["D"],
        explanation_markdown="哈夫曼编码",
    )])
    _verify_repair_against_position("answers", correct, [58], position)
