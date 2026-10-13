"""Repeatable draft-only examples. Publication always uses the review workflow."""

from sqlalchemy import select

from app.models import ExamSubject, KnowledgeNode
from app.recall.models import Recall
from app.recall.schemas import VersionIn
from app.recall.service import add_version, lock


def pilots():
    def t(value):
        return {"text": value}

    def slot(key):
        return {"blank_key": key}

    def blank(key, label, answer, hints, aliases=None, math=False):
        return {
            "key": key,
            "label": label,
            "answer": answer,
            "hints": hints,
            "acceptable_answers": aliases or [],
            "normalization": {
                "case_sensitive": math,
                "whitespace": "remove" if math else "collapse",
                "aliases": {"→": "->", "⇒": "->", "²": "^2", "log₂": "log", "lg": "log"}
                if math
                else {},
            },
        }

    columns = ["排序算法", "最好时间", "平均时间", "最坏时间", "辅助空间", "稳定性"]
    algorithms = [
        ("insert", "直接插入", ["O(n)", "O(n^2)", "O(n^2)", "O(1)", "稳定"]),
        ("bubble", "冒泡", ["O(n)", "O(n^2)", "O(n^2)", "O(1)", "稳定"]),
        ("select", "简单选择", ["O(n^2)", "O(n^2)", "O(n^2)", "O(1)", "不稳定"]),
        ("quick", "快速", ["O(n log n)", "O(n log n)", "O(n^2)", "O(log n)", "不稳定"]),
        ("heap", "堆", ["O(n log n)", "O(n log n)", "O(n log n)", "O(1)", "不稳定"]),
        ("merge", "归并", ["O(n log n)", "O(n log n)", "O(n log n)", "O(n)", "稳定"]),
    ]
    rows, blanks = [], []
    for code, name, values in algorithms:
        row = [t(name)]
        for i, answer in enumerate(values):
            key = f"{code}_{i}"
            row.append(slot(key))
            hint = (
                (
                    "想一想相等元素的先后次序。",
                    "常规实现保持相等元素的相对顺序。"
                    if answer == "稳定"
                    else "非相邻交换可能改变相等元素的相对顺序。",
                )
                if i == 4
                else (
                    "考虑比较、移动次数或额外存储。",
                    {
                        "O(1)": "只需要固定数量的辅助变量。",
                        "O(n)": "数量随元素个数成正比。",
                        "O(n^2)": "最主要的次数来自两重随 n 增长的循环或累加。",
                        "O(n log n)": "每层处理 n 个元素，层数是对数级。",
                        "O(log n)": "平均递归深度是对数级。",
                    }[answer],
                )
            )
            blanks.append(
                blank(
                    key,
                    name + " · " + columns[i + 1],
                    answer,
                    list(hint),
                    ["stable"]
                    if answer == "稳定"
                    else ["unstable"]
                    if answer == "不稳定"
                    else [],
                    math=i != 4,
                )
            )
        rows.append(row)
    sorting = {
        "title": "六种排序算法对比",
        "content": {
            "type": "table",
            "columns": columns,
            "rows": rows,
            "note": "约定：数组实现；冒泡含无交换提前退出；归并为常规二路归并，不含有序段跳过优化。快速排序空间列采用平均递归栈 O(log n)，最坏为 O(n)。堆排序按互异键的常规实现讨论；时间填写常见紧确数量级。log 的底数不影响此处数量级。",
        },
        "blanks": blanks,
        "provenance": {
            "references": ["https://algs4.cs.princeton.edu/cheatsheet/"],
            "suitability_reason": "固定复杂度与稳定性适合横向辨析",
        },
    }
    acid_values = [
        (
            "atomicity",
            "原子性",
            "事务中的操作要么全部成功，要么全部不生效，这称为",
            ["关注事务是否能只完成一部分。", "把整个事务视为不可分割的工作单元。"],
            ["Atomicity"],
        ),
        (
            "consistency",
            "一致性",
            "正确事务使数据库从一个满足完整性约束的状态转到另一个满足约束的状态，这称为",
            ["关注业务规则与约束是否成立。", "事务前后都应处于合法状态。"],
            ["Consistency"],
        ),
        (
            "isolation",
            "隔离性",
            "并发事务之间的相互影响受隔离级别约束，这一性质称为",
            [
                "关注多个事务同时运行时的关系。",
                "最高标准下，并发结果应等价于某种串行执行。",
            ],
            ["Isolation"],
        ),
        (
            "durability",
            "持久性",
            "事务成功提交后，其结果应在故障恢复后保留，这称为",
            ["关注提交之后发生故障的情况。", "已提交的改变不能因重启而丢失。"],
            ["Durability", "永久性"],
        ),
    ]
    acid = {
        "title": "事务的 ACID 四种性质",
        "content": {
            "type": "sentence",
            "paragraphs": [
                [t(desc), slot(key), t("。")] for key, _, desc, _, _ in acid_values
            ],
            "note": "一致性还依赖正确的事务逻辑与约束；不同隔离级别允许的并发现象不同，不把低隔离级别等同于完全互不影响。",
        },
        "blanks": [
            blank(key, "性质 " + str(i + 1), ans, hints, aliases)
            for i, (key, ans, _, hints, aliases) in enumerate(acid_values)
        ],
        "provenance": {
            "references": [
                "https://www.postgresql.org/docs/18/tutorial-transactions.html"
            ],
            "suitability_reason": "短定义与固定术语适合句子填空",
        },
    }
    rules = [
        (
            "reflexive",
            "Y ⊆ X",
            "自反律",
            "X→Y",
            ["从属性集合包含关系思考。", "集合总能决定它自己的子集。"],
        ),
        (
            "augment",
            "X→Y",
            "增广律",
            "XZ→YZ",
            ["观察依赖两端同时增加的属性。", "在两侧并入相同的 Z。"],
        ),
        (
            "transitive",
            "X→Y 且 Y→Z",
            "传递律",
            "X→Z",
            ["把两个首尾相接的依赖连接起来。", "用中间属性集合 Y 作为桥梁。"],
        ),
    ]
    graph_nodes, edges, graph_blanks = [], [], []
    for i, (key, premise, name, answer, hints) in enumerate(rules):
        graph_nodes.extend(
            [
                {"id": key + "_in", "label": t(premise), "x": 120, "y": 80 + i * 160},
                {
                    "id": key + "_out",
                    "label": slot(key + "_result"),
                    "x": 580,
                    "y": 80 + i * 160,
                },
            ]
        )
        edges.append(
            {
                "source": key + "_in",
                "target": key + "_out",
                "label": slot(key + "_rule"),
            }
        )
        graph_blanks.extend(
            [
                blank(
                    key + "_rule",
                    f"推导 {i + 1} 的规则",
                    name,
                    hints,
                    [name.replace("律", "性"), name.replace("律", "")],
                ),
                blank(
                    key + "_result", f"推导 {i + 1} 的结论", answer, hints, math=True
                ),
            ]
        )
    armstrong = {
        "title": "Armstrong 公理：前提到结论",
        "content": {
            "type": "relation",
            "nodes": graph_nodes,
            "edges": edges,
            "steps": [],
            "note": "X、Y、Z 为属性集合；XZ 表示 X∪Z；箭头表示函数依赖。可输入 → 或 ->。增广律结论中的 XZ、YZ 按图中顺序填写。",
        },
        "blanks": graph_blanks,
        "provenance": {
            "references": [
                "https://courses.cs.washington.edu/courses/cse344/26sp/lectures/lec11_fd.pdf"
            ],
            "suitability_reason": "前提、推理规则与结论有明确关系",
        },
    }
    return [
        (
            "pilot-sorting",
            sorting,
            [
                "data-structures-algorithms.search-sort." + s
                for s in [
                    "insertion-sort",
                    "exchange-sort",
                    "selection-sort",
                    "merge-radix",
                ]
            ],
        ),
        ("pilot-acid", acid, ["database.transaction.acid"]),
        (
            "pilot-armstrong",
            armstrong,
            ["database.relational-theory.functional-dependency"],
        ),
    ]


async def seed(db, user_id):
    subject = await db.scalar(
        select(ExamSubject).where(ExamSubject.code == "software-designer.foundation")
    )
    if not subject:
        from app.core.errors import fail

        raise fail(422, "SUBJECT_NOT_FOUND", "请先导入软件设计师知识目录")
    result = []
    for slug, payload, codes in pilots():
        await lock(db, f"recall-slug:{subject.id}:{slug}")
        old = await db.scalar(
            select(Recall).where(Recall.subject_id == subject.id, Recall.slug == slug)
        )
        if old:
            result.append(str(old.id))
            continue
        ids = list(
            await db.scalars(
                select(KnowledgeNode.id).where(
                    KnowledgeNode.subject_id == subject.id,
                    KnowledgeNode.code.in_(codes),
                )
            )
        )
        if len(ids) != len(codes):
            from app.core.errors import fail

            raise fail(422, "RECALL_KNOWLEDGE", "试点所需知识点不完整")
        r = Recall(subject_id=subject.id, slug=slug)
        db.add(r)
        await db.flush()
        await add_version(
            db,
            r,
            VersionIn.model_validate({**payload, "knowledge_node_ids": ids}),
            user_id,
        )
        result.append(str(r.id))
    return result
