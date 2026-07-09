"""上下文管理（Day7）：token 预算、滑动窗口、自动摘要 / compaction。

模型上下文窗口有限。长任务里 messages 会越堆越长，迟早超预算。
策略：
  - 估算当前 messages 的 token 数；
  - 超过阈值时触发 compaction：把较早的对话摘要成一条 system 备忘，
    保留最近 K 轮原文 + 关键工具结果；
  - tool result 过长时先截断/摘要再注入。
"""
from __future__ import annotations
from typing import Any


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """字符数 / 4 粗估 token 数，够用就行。"""
    return sum(len(str(m.get("content", ""))) for m in messages) // 4


def _summarize(backend, chunk: list[dict]) -> str:
    """调后端把一段对话历史压缩成要点备忘。"""
    text = "\n".join(
        f"<{m['role']}>: {m.get('content', '')}" for m in chunk
    )
    prompt = (
        "把下面一段对话历史压缩成一条「历史备忘」，保留三样东西：\n"
        "1. 任务目标（用户要做什么）\n"
        "2. 已完成的关键步骤（读了哪些文件、跑了什么命令、改了哪里）\n"
        "3. 重要发现（bug 位置、测试结果、关键数据）\n"
        "---\n"
        f"{text}\n"
        "---\n"
        "输出纯文本，不要 Markdown 格式，不要多余前缀，直接以「历史备忘：」开头。"
    )
    try:
        resp = backend.chat([{"role": "user", "content": prompt}], tools=[])
        summary = resp.get("content", "")
        return summary if summary else f"历史备忘：共 {len(chunk)} 条消息，已完成 {sum(1 for m in chunk if m['role']=='assistant')} 步操作。"
    except Exception as e:
        return f"历史备忘：摘要生成失败 ({e})，保留 {len(chunk)} 条消息的结构信息。"


def maybe_compact(
    messages: list[dict[str, Any]],
    backend: Any,
    budget: int = 6000,
    keep_rounds: int = 4,
) -> list[dict[str, Any]]:
    """超预算则压缩历史，返回新的 messages。

    压缩策略（保留信息密度最高的部分）：
    1. 保留 system prompt（messages[0]）
    2. 保留最近 keep_rounds 轮原文（按 assistant 消息计数）
    3. 中间的历史调后端摘要成一条 system 备忘
    4. 返回 [system] + [备忘] + [最近 K 轮]
    """
    if estimate_tokens(messages) <= budget:
        return messages

    # 找到所有 assistant 消息的位置（每一条代表模型一次推理 + 可能跟若干个 tool 结果）
    assist_idx = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
    if len(assist_idx) <= keep_rounds:
        return messages  # 本身就没几轮，没必要压缩

    # 从倒数第 keep_rounds 条 assistant 处切开
    split_idx = assist_idx[-keep_rounds]
    system = messages[0]
    chunk_to_summarize = messages[1:split_idx]  # system 之后 ~ split 之前

    summary = _summarize(backend, chunk_to_summarize)

    memo: dict[str, str] = {"role": "system", "content": summary}
    compacted: list[dict[str, Any]] = [system, memo] + messages[split_idx:]

    return compacted


def truncate_observation(text: str, max_chars: int = 4000) -> str:
    """工具结果过长时截断并提示。"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n...[已截断，共 {len(text)} 字符]"
