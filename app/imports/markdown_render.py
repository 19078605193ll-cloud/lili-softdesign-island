"""Local Markdown/MathML rendering with a single final sanitization boundary."""
import html
import re

import bleach
from latex2mathml.converter import convert
from markdown_it import MarkdownIt
from mdit_py_plugins.dollarmath import dollarmath_plugin


def render_markdown(value: str, *, audience: str = 'admin') -> str:
    def math(value, options):
        try:
            return convert(value, display='block' if options.get('display_mode') else 'inline')
        except Exception:
            return '<code>' + html.escape(value) + '</code>'

    renderer = MarkdownIt('commonmark', {'html': True}).enable('table')
    renderer.use(dollarmath_plugin, renderer=math)
    math_tags = {'math', 'mrow', 'mi', 'mn', 'mo', 'mtext', 'msup', 'msub', 'msubsup',
                 'mfrac', 'msqrt', 'mroot', 'mover', 'munder', 'munderover', 'mtable',
                 'mtr', 'mtd', 'mspace', 'mstyle', 'mpadded', 'menclose', 'mphantom'}
    tags = set(bleach.sanitizer.ALLOWED_TAGS) | math_tags | {
        'p', 'br', 'pre', 'code', 'img', 'table', 'thead', 'tbody', 'tr', 'th', 'td',
        'h1', 'h2', 'h3', 'h4', 'hr', 'span', 'div', 'sub', 'sup'}

    def attributes(tag, name, value):
        if tag == 'img' and name == 'src':
            if audience == 'learner':
                return bool(re.fullmatch(r'/api/v1/question-assets/[0-9a-fA-F-]{36}|/api/v2/learning/attempts/[0-9a-fA-F-]{36}/assets/[0-9a-fA-F-]{36}', value))
            return bool(re.fullmatch(r'/api/v1/admin/question-assets/[0-9a-fA-F-]{36}', value))
        if tag in math_tags:
            return name in {'display', 'mathvariant', 'stretchy', 'fence', 'separator',
                            'accent', 'accentunder', 'columnalign', 'rowalign', 'linethickness'}
        return name in {'alt', 'title', 'colspan', 'rowspan'}

    return bleach.clean(renderer.render(value), tags=tags, attributes=attributes, strip=True)
