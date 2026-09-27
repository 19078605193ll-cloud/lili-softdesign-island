from app.imports.markdown_parser import parse_markdown


def test_letters_in_stem_and_options_are_not_option_markers():
    units = parse_markdown('1. 数组 A 按行存储，A 的地址是？\nA.300\nB.310\nC.306\nD.296\n答案：A\n解析：使用地址公式。')
    assert len(units) == 1
    assert units[0].parts[0].stem_markdown.startswith('数组 A')
    assert not units[0].issues


def test_composite_five_parts_preserves_numbers_and_shared_text():
    rows = '\n'.join(f'{n}. A.one B.two C.three D.four' for n in range(71, 76))
    unit = parse_markdown(f'71-75. A low-code platform has (71) through (75).\n{rows}\n答案：A B C D A\n解析：shared')[0]
    assert [p.question_no for p in unit.parts] == list(range(71, 76))
    assert unit.material_markdown.startswith('A low-code')
    assert not unit.issues


def test_explanation_numbered_list_not_question():
    text = '1. 问题\nA.a B.b C.c D.d\n答案：A\n解析：\n1. 模块 A 有接口\n2. 其他解释\n2. 下题\nA.a B.b C.c D.d\n答案：B\n解析：内容'
    units = parse_markdown(text)
    assert len(units) == 2
    assert '1. 模块 A' in units[0].parts[0].explanation_markdown


def test_group_with_separate_answers():
    text = '8-9. 共享材料\n第8题\nA.a B.b C.c D.d\n答案：D\n解析：八\n第9题\nA.e B.f C.g D.h\n答案：C\n解析：九'
    unit = parse_markdown(text)[0]
    assert [p.correct_option_keys for p in unit.parts] == [['D'], ['C']]
    assert not unit.issues


def test_noncontinuous_and_over_75():
    text = '\n'.join(f'{n}. 问题\nA.a B.b C.c D.d\n答案：A\n解析：内容' for n in [3, 99, 105])
    assert [u.parts[0].question_no for u in parse_markdown(text)] == [3, 99, 105]


def test_standalone_diagram_after_options_stays_with_stem():
    text = '1. 看图选择\nA.a B.b C.c D.d\n\n![](fig.png)\n答案：A\n解析：内容'
    unit = parse_markdown(text)[0]
    assert '![](fig.png)' in unit.parts[0].stem_markdown
    assert '![](fig.png)' not in unit.parts[0].options[-1].content_markdown


def test_image_directly_under_option_stays_in_that_option():
    text = '1. 选择正确图形\nA. 图一\n![](a.png)\nB. 图二\nC. 图三\nD. 图四\n答案：A\n解析：图一正确'
    unit = parse_markdown(text)[0]
    assert '![](a.png)' in unit.parts[0].options[0].content_markdown
    assert '![](a.png)' not in unit.parts[0].stem_markdown


def test_diagrams_before_options_keep_their_position_in_stem():
    text = ('1. 前置说明\n\n![](first.png)\n\n中间说明\n\n![](second.png)\n\n'
            'A. 一 B. 二 C. 三 D. 四\n答案：A\n解析：一')
    stem = parse_markdown(text)[0].parts[0].stem_markdown
    assert stem.index('first.png') < stem.index('中间说明') < stem.index('second.png')


def test_unresolved_preserves_source():
    raw = '这是无法识别的原文'
    unit = parse_markdown(raw)[0]
    assert unit.raw == raw and unit.issues


def test_multiline_bare_options_do_not_shift_to_previous_part():
    raw = '54-55、关系 R 与 S 的结果为（），个数为（）。\n<table><tr><td>A1</td></tr></table>\nA {(2,1,4)}\nB {(2,1,4,8)}\nC {(3,4,4)}\nD {(4,6,7)}\n A 2,2,4    B 2,2,6    C 4,4,4    D 4,4,6\n答案：B D\n解析：第一问连接。第二问可忽略。'
    unit = parse_markdown(raw)[0]
    assert not unit.issues
    assert unit.parts[0].options[1].content_markdown == '{(2,1,4,8)}'
    assert unit.parts[1].options[3].content_markdown == '4,4,6'
    assert [p.correct_option_keys for p in unit.parts] == [['B'], ['D']]
    assert [p.explanation_markdown for p in unit.parts] == ['连接。', '可忽略。']
    assert '答案' not in unit.material_markdown and '{(2' not in unit.material_markdown


def test_shared_explanation_is_not_duplicated():
    unit = parse_markdown('1-3. 完整材料\nA.a B.b C.c D.d\nA.e B.f C.g D.h\nA.i B.j C.k D.l\n答案：A B C\n解析：统一说明')[0]
    assert unit.explanation_markdown == '统一说明'
    assert all(p.explanation_markdown is None for p in unit.parts)


def test_numbered_explanations_and_locator():
    from app.imports.markdown_parser import split_explanations, part_locator
    assert split_explanations('54、连接\n55、外连接', [54,55]) == {54:'连接',55:'外连接'}
    assert part_locator('R(A,B) 中（54）为结果',54)['marker'] == '（54）'
    assert part_locator('R(A,B) 中（）为结果',54)['marker'] is None


def test_safe_formula_and_table_rendering():
    from app.imports.markdown_render import render_markdown
    html = render_markdown('$x^2 + \\frac{1}{2}$\n\n<table><tr><td>R</td></tr></table><img src="https://invalid/image" onerror="alert(1)">')
    assert '<math' in html and '<mfrac>' in html and '<table>' in html
    assert 'onerror' not in html and 'https://invalid/image' not in html
