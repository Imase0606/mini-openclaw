"""对话模板渲染器（Day3 的核心交付物）。

目标：把结构化的 messages + tools，渲染成模型真正看到的**一整段文本/token**。
关键认知：模型从不"接收一个 messages 列表"——它只接收一段拼好的字符串，
里面用特殊标记区分角色，工具 schema 也只是被塞进 system 段的普通文本，
模型输出的 <tool_call>{...}</tool_call> 同样只是它学会生成的普通 token。

Day3 你要：
  1. 用 tokenizers 库观察 GLM tokenizer 对这些特殊标记的切分；
  2. 不借助任何 function-calling API，纯字符串拼接实现下面的 render_prompt；
  3. 送入本地模型，手动解析它生成的工具调用。
"""
from __future__ import annotations
from typing import Any
import json

# 不同模型的对话模板不同（ChatML / Llama / GLM）。这里以 GLM 风格为例占位。
# ChatML compatibility markers used by the legacy prompt-rendering evaluator.
ROLE_TOKENS = {
    "system": "<|im_start|>system\n",
    "user": "<|im_start|>user\n",
    "assistant": "<|im_start|>assistant\n",
    "tool": "<|im_start|>tool\n",
}


def render_tools_block(tools: list[dict[str, Any]]) -> str:
    """把 tool schema 列表渲染成放进 system 段的文本说明。"""
    if not tools:
        return ""
    lines = [
        "You have access to the following tools. To call a tool, output exactly:",
        '<tool_call>{"name": "tool_name", "arguments": {...}}</tool_call>',
        "where 'arguments' is a JSON object with the required parameters.",
        "",
        "Available tools:"
    ]
    for t in tools:
        f = t["function"]
        lines.append(f"- {f['name']}: {f['description']}")
        lines.append(f"  Parameters schema: {json.dumps(f['parameters'], ensure_ascii=False)}")
    return "\n".join(lines)


def render_prompt(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> str:
    """使用 ChatML 模板拼接 messages + tools -> 整段文本"""
    parts = []
    
    # 1. 构建 system 内容（包含工具说明）
    system_content = ""
    for msg in messages:
        if msg.get("role") == "system":
            system_content = msg.get("content", "")
            break
    # 如果有 tools，附加工具说明
    if tools:
        tools_block = render_tools_block(tools)
        if system_content:
            system_content += "\n\n" + tools_block
        else:
            system_content = tools_block
    # 如果 system_content 不为空，以 ChatML 格式添加
    if system_content:
        parts.append(f"<|im_start|>system\n{system_content}<|im_end|>")
    
    # 2. 添加其他消息（跳过已处理的 system）
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if role == "system":
            continue  # 已处理
        if role in ("user", "assistant", "tool"):
            parts.append(f"<|im_start|>{role}\n{content}<|im_end|>")
        else:
            # 未知角色，当作普通文本
            parts.append(f"<|im_start|>user\n{content}<|im_end|>")
    
    # 3. 末尾加上 assistant 起始标记，提示模型开始生成
    parts.append("<|im_start|>assistant\n")
    
    return "\n".join(parts)


import re,json

import re, json

def parse_tool_calls(text: str) -> list[dict[str, Any]]:
    calls = []
    for m in re.finditer(r"<tool_call>(.*?)</tool_call>", text, re.DOTALL):
        raw = m.group(1).strip()
        try:
            obj = json.loads(raw)
            calls.append({"name": obj.get("name"),
                          "arguments": obj.get("arguments", {})})
        except json.JSONDecodeError:
            continue   # 容错：JSON 不合法就跳过这一段
    return calls
