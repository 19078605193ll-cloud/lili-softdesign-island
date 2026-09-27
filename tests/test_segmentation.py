from __future__ import annotations

from app.imports.segmentation import segment_pages


def line(text: str, y: float, *, x: float = 0.1, red: bool = False) -> dict:
    return {"text": text, "bbox": [x, y, min(x + 0.7, 0.95), y + 0.02],
            "score": 0.98, "ink": "red" if red else "black"}


def page(number: int, *lines: dict) -> dict:
    return {"page_no": number, "lines": list(lines)}


def test_options_and_answer_continue_on_next_page() -> None:
    parsed = segment_pages([
        page(20,
             line("旅游定价系统应采用（46）模式，其主要意图是（47）。", 0.7),
             line("（46） A. 策略模式 B. 状态模式", 0.8),
             line("C. 观察者 D. 命令", 0.83),
             line("（47） A. 将请求封装为对象", 0.9)),
        page(21,
             line("B. 对象状态变化自动通知", 0.1),
             line("C. 允许对象改变行为", 0.14),
             line("D. 定义一系列算法并封装", 0.18),
             line("参考答案： A D", 0.25),
             line("【解析】：选择策略模式。", 0.29, red=True)),
    ], "doc")
    assert set(parsed["questions"][46]["options"]) == set("ABCD")
    assert set(parsed["questions"][47]["options"]) == set("ABCD")
    assert parsed["answers"][46]["keys"] == ["A"]
    assert parsed["answers"][47]["keys"] == ["D"]
    assert any(ref["page_no"] == 21 for ref in parsed["questions"][47]["refs"])


def test_page_top_answer_belongs_to_previous_question_not_next() -> None:
    parsed = segment_pages([
        page(25, line("哈夫曼编码中不可能的是（58）。", 0.8),
             line("（58） A. 方案甲 B. 方案乙 C. 方案丙 D. 方案丁", 0.84)),
        page(26, line("参考答案： D", 0.1),
             line("【解析】：哈夫曼编码为前缀码。", 0.15, red=True),
             line("广度优先遍历复杂度是（59）。", 0.4)),
    ], "doc")
    assert parsed["answers"][58]["keys"] == ["D"]
    assert "哈夫曼" in parsed["answers"][58]["explanation"]
    assert 59 not in parsed["answers"]


def test_conflicting_answer_and_furniture_are_not_silently_accepted() -> None:
    parsed = segment_pages([
        page(1, {**line("软考达人页眉", 0.01), "furniture": True},
             line("UML 顺序是（42）。", 0.3),
             line("（42） A. 一 B. 二 C. 三 D. 四", 0.4),
             line("参考答案： A", 0.5),
             line("参考答案： D", 0.7)),
    ], "doc")
    assert parsed["answers"][42]["keys"] == ["A"]
    assert parsed["conflicts"][42]
    assert "页眉" not in parsed["questions"][42]["stem"]
