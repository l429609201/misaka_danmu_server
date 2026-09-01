"""Markdown 格式转换工具：标准 Markdown ↔ Telegram MarkdownV2 ↔ 纯文本。

原实现位于 TelegramChannel，现抽取为独立模块。
"""
import re
from typing import Optional

_MDV2_SPECIALS = frozenset(r'_*[]()~`>#+-=|{}.!')
_MDV2_TOKEN_RE: Optional[re.Pattern] = None


def _get_token_re() -> re.Pattern:
    global _MDV2_TOKEN_RE
    if _MDV2_TOKEN_RE is None:
        _MDV2_TOKEN_RE = re.compile(
            r'(?P<fence>```[\s\S]*?```)'
            r'|(?P<code>`[^`\n]+`)'
            r'|(?P<image>!\[(?P<img_alt>[^\]]*)\]\((?P<img_url>[^)\s]+)\))'
            r'|(?P<link>\[(?P<link_text>[^\]]*)\]\((?P<link_url>[^)\s]+)\))'
            r'|(?P<bold>\*\*(?P<bold_in>[^\n]+?)\*\*|__(?P<bold_in2>[^\n]+?)__)'
            r'|(?P<strike>~~(?P<strike_in>[^\n]+?)~~)'
            r'|(?P<italic>\*(?P<italic_in>[^*\n]+?)\*|_(?P<italic_in2>[^_\n]+?)_)'
            r'|(?P<url>https?://[^\s<>()]+)'
        )
    return _MDV2_TOKEN_RE


def _mdv2_escape_plain(s: str) -> str:
    if not s:
        return ""
    out = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == '\\':
            # 反斜杠本身需要转义
            out.append('\\\\')
        elif c == '_':
            prev = i > 0 and s[i - 1].isalnum()
            nxt = i + 1 < n and s[i + 1].isalnum()
            out.append('_' if (prev and nxt) else '\\_')
        elif c in _MDV2_SPECIALS:
            out.append('\\' + c)
        else:
            out.append(c)
        i += 1
    return ''.join(out)


def _mdv2_escape_code(s: str) -> str:
    return s.replace('\\', '\\\\').replace('`', '\\`')


def _mdv2_escape_url(url: str) -> str:
    return url.replace('\\', '\\\\').replace(')', '\\)')


def _split_table_row(line: str) -> list:
    parts = line.split('|')
    return [p.strip() for p in parts if p.strip()]


def _is_table_separator(line: str) -> bool:
    s = line.strip()
    if not s.startswith('|') or not s.endswith('|'):
        return False
    core = s.strip('|').replace(' ', '')
    if not core:
        return False
    cells = [c.strip() for c in core.split('|') if c.strip()]
    return all(set(c) <= {'-', ':'} and '-' in c for c in cells)


def _convert_inline(text: str) -> str:
    token_re = _get_token_re()
    result = []
    last_end = 0

    for m in token_re.finditer(text):
        if m.start() > last_end:
            result.append(_mdv2_escape_plain(text[last_end:m.start()]))

        if m.group('fence'):
            full = m.group(0)
            lines = full.split('\n')
            lang = lines[0][3:].strip() if lines else ''
            body = '\n'.join(lines[1:-1]) if len(lines) > 2 else ''
            result.append(f'```{lang}\n{_mdv2_escape_code(body)}\n```')
        elif m.group('code'):
            inner = m.group(0)[1:-1]
            result.append(f'`{_mdv2_escape_code(inner)}`')
        elif m.group('image'):
            alt = m.group('img_alt')
            url = m.group('img_url')
            result.append(f'![{_mdv2_escape_plain(alt)}]({_mdv2_escape_url(url)})')
        elif m.group('link'):
            txt = m.group('link_text')
            url = m.group('link_url')
            result.append(f'[{_mdv2_escape_plain(txt)}]({_mdv2_escape_url(url)})')
        elif m.group('bold'):
            inner = m.group('bold_in') or m.group('bold_in2')
            result.append(f'*{inner}*')
        elif m.group('strike'):
            inner = m.group('strike_in')
            result.append(f'~{inner}~')
        elif m.group('italic'):
            inner = m.group('italic_in') or m.group('italic_in2')
            result.append(f'_{inner}_')
        elif m.group('url'):
            result.append(m.group(0))
        else:
            result.append(m.group(0))

        last_end = m.end()

    if last_end < len(text):
        result.append(_mdv2_escape_plain(text[last_end:]))
    return ''.join(result)


def _convert_table(lines: list, start_idx: int) -> tuple:
    header_line = lines[start_idx]
    if start_idx + 1 >= len(lines) or not _is_table_separator(lines[start_idx + 1]):
        return (_convert_inline(header_line), 1)

    headers = _split_table_row(header_line)
    consumed = 2
    rows = []
    for i in range(start_idx + 2, len(lines)):
        line = lines[i].strip()
        if not line.startswith('|'):
            break
        cells = _split_table_row(line)
        row_parts = []
        for idx, cell in enumerate(cells):
            h = headers[idx] if idx < len(headers) else f'列{idx + 1}'
            row_parts.append(f'{h}：{_convert_inline(cell)}')
        rows.append('• ' + '；'.join(row_parts))
        consumed += 1
    return ('\n'.join(rows), consumed)


def escape_v2(text: str) -> str:
    """转义 MarkdownV2 保留字符。"""
    if not text:
        return ""
    special = r'_*[]()~`>#+-=|{}.!'
    out = []
    for ch in str(text):
        if ch in special:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def markdown_to_v2(text: str) -> str:
    """标准 Markdown → Telegram MarkdownV2。"""
    if not text:
        return ''
    lines = text.split('\n')
    result = []
    i = 0
    in_fence = False
    fence_lang = ''
    fence_body = []

    while i < len(lines):
        line = lines[i].rstrip()

        if line.startswith('```'):
            if not in_fence:
                in_fence = True
                fence_lang = line[3:].strip()
                fence_body = []
            else:
                result.append(f'```{fence_lang}\n{_mdv2_escape_code(chr(10).join(fence_body))}\n```')
                in_fence = False
            i += 1
            continue

        if in_fence:
            fence_body.append(line)
            i += 1
            continue

        if line.strip().startswith('|'):
            converted, consumed = _convert_table(lines, i)
            result.append(converted)
            i += consumed
            continue

        if line.startswith('#'):
            level = len(line) - len(line.lstrip('#'))
            title_text = line[level:].strip()
            result.append(f'*{_convert_inline(title_text)}*')
            result.append('')
            i += 1
            continue

        if re.match(r'^\d+\.\s', line):
            content = re.sub(r'^\d+\.\s+', '', line)
            result.append(f'• {_convert_inline(content)}')
            i += 1
            continue

        if line.startswith('- ') or line.startswith('• '):
            content = line[2:].strip()
            result.append(f'• {_convert_inline(content)}')
            i += 1
            continue

        if line.startswith('>'):
            result.append(f'>{_convert_inline(line[1:].strip())}')
            i += 1
            continue

        if line.strip() == '---' or line.strip() == '***':
            result.append('─' * 20)
            i += 1
            continue

        if line:
            result.append(_convert_inline(line))
        else:
            result.append('')
        i += 1

    if in_fence and fence_body:
        body = _mdv2_escape_code('\n'.join(fence_body))
        result.append(f"```{fence_lang}\n{body}\n```")

    return '\n'.join(result)


def strip_markdown(text: str) -> str:
    """清洗 Markdown 符号，返回纯文本。"""
    if not text:
        return ""
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', str(text))
    text = re.sub(r'^\|[-:| ]+\|$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\| ', '', text, flags=re.MULTILINE)
    text = re.sub(r' \|$', '', text, flags=re.MULTILINE)

    lines = []
    for line in text.split("\n"):
        if line.startswith(">"):
            line = line[1:].strip()
        out = []
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "\\" and i + 1 < len(line):
                out.append(line[i + 1])
                i += 2
            elif ch in ("*", "`", "~"):
                i += 1
            elif ch == "_":
                prev = i > 0 and line[i - 1].isalnum()
                nxt = i + 1 < len(line) and line[i + 1].isalnum()
                if prev and nxt:
                    out.append("_")
                    i += 1
                else:
                    i += 1
            else:
                out.append(ch)
                i += 1
        lines.append("".join(out))
    return "\n".join(lines)
