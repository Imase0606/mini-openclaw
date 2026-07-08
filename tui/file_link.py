"""文件引用检测：在文本中高亮 file:line 模式。"""

from __future__ import annotations
import re
from typing import Iterator

# 匹配各种路径的 file:line 模式
# 支持: Windows (C:\path\file.py:42), Unix (/path/file.py:42),
#       相对路径 (path/file.py:42, ./file.py:42, ../file.py:42)
FILE_REFERENCE_RE = re.compile(
    r'(?P<path>(?:[A-Za-z]:[\\/][^\s:()]+|[\\/][^\s:()]+|(?:\.\.?[\\/])[^\s:()]+|[a-zA-Z_][a-zA-Z0-9_\-]*[\\/][^\s:()]+))\s*[:(]\s*(?P<line>\d+)'
)


def extract_file_references(text: str) -> list[tuple[str, int]]:
    """从文本中提取 (路径, 行号) 对。"""
    results = []
    for match in FILE_REFERENCE_RE.finditer(text):
        path = match.group("path")
        line = int(match.group("line"))
        results.append((path, line))
    return results


def highlight_references(text: str) -> str:
    """将 file:line 模式替换为高亮 Rich 标记。

    由于 Textual 的 RichLog 支持 Rich 标记，
    我们用 [cyan underline] 高亮文件引用。
    """
    def _replace(m: re.Match) -> str:
        path = m.group("path")
        line = m.group("line")
        return f"[cyan underline]{path}:{line}[/cyan underline]"

    return FILE_REFERENCE_RE.sub(_replace, text)
