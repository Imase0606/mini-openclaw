"""评测任务集与指标（Day3：工具调用质量评测 + 端到端任务成功率 / 消融）。

两类评测：
  A) 工具调用质量：在固定测试集上算三项指标。
  B) 端到端任务成功率（消融用）：跑一批任务，看完成率，对比不同配置。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable

# 一条“轨迹记录”长这样（步骤 2 会给出完整样本）：
#   {"task": "任务名", "steps": [ {tool_calls, raw, prompt_tokens, completion_tokens}, ... ],
#    "final": "agent 的最终自然语言答复"}
Trajectory = dict

@dataclass
class Task:
    name: str
    instruction: str                       # 给 agent 的指令
    check: Callable[[Trajectory], bool]    # 成功判据：吃一条轨迹，判成败

# ---- 成功判据（程序化优先）----
def _check_read_config(traj: Trajectory) -> bool:
    used_read = any(
        tc["name"] == "read"
        for s in traj["steps"] for tc in s.get("tool_calls", [])
    )
    return used_read and "30" in traj.get("final", "")

def _check_list_dir(traj: Trajectory) -> bool:
    return any(
        tc["name"] == "bash" and "ls" in str(tc.get("arguments", {}))
        for s in traj["steps"] for tc in s.get("tool_calls", [])
    )

# ========== 领域专属判据：科研论文复现助手 ==========
def _check_domain(traj: Trajectory) -> bool:
    """
    科研助手成功的硬性标准（程序化判定）：
    1. 必须调用过“论文获取”类工具（arxiv_search / pdf_reader），证明读了前沿文献。
    2. 必须调用过“代码生成/执行”类工具（write / bash / code_writer），证明做了本地实验。
    3. 最终回答中必须包含数值或图表描述（如 loss / accuracy / 收敛），证明有结果分析。
    """
    # 工具名称集合（根据你实际实现的工具名调整，此处为通用匹配）
    PAPER_TOOLS = {"arxiv_search", "arxiv_download", "pdf_reader", "pdf_extract"}
    CODE_TOOLS = {"write", "edit", "code_writer", "bash", "sandbox_exec"}
    
    # 检查轨迹步骤中调用的所有工具
    all_tool_names = set()
    for step in traj.get("steps", []):
        for tc in step.get("tool_calls", []):
            all_tool_names.add(tc.get("name", ""))
    
    has_paper_tool = bool(all_tool_names & PAPER_TOOLS)
    has_code_tool = bool(all_tool_names & CODE_TOOLS)
    
    # 检查最终答复是否包含科研结果关键词（证明模型认真做了分析）
    final_text = traj.get("final", "")
    result_keywords = ["loss", "accuracy", "收敛", "结果表明", "acc", "bleu", "perplexity", 
                       "图", "曲线", "实验显示", "创新点", "对比", "性能提升"]
    has_result = any(kw in final_text.lower() for kw in result_keywords)
    
    # 三项全满足才算领域任务成功（严格模式，展示Agent硬实力）
    return has_paper_tool and has_code_tool and has_result

# ---- 补充一条“领域工具调用”的通用判据（用于SAMPLE_TASKS）----
def _check_domain_tool_usage(traj: Trajectory) -> bool:
    """
    专门用来测试工具调用的质量，不要求最终结果完美，
    只要它正确调用了论文搜索 + 生成了实验代码骨架即可。
    """
    all_tool_names = set()
    for step in traj.get("steps", []):
        for tc in step.get("tool_calls", []):
            all_tool_names.add(tc.get("name", ""))
    
    # 必须同时包含“搜索论文”和“写代码”的行为
    has_search = any("arxiv" in name or "pdf" in name for name in all_tool_names)
    has_write = any("write" in name or "edit" in name or "code" in name for name in all_tool_names)
    return has_search and has_write


# ========== 样例任务集（包含通用 + 领域） ==========
SAMPLE_TASKS: list[Task] = [
    # 通用任务（验证基础工具）
    Task("read-config", "读取 config.json，告诉我 timeout 是多少", _check_read_config),
    Task("list-dir", "列出当前目录下的文件", _check_list_dir),
    
    # 领域任务（验证“科研助手”核心能力）
    Task(
        name="domain-research",
        instruction=(
            "请帮我找一篇关于 'LLM 推理加速' 的最新 arXiv 论文（例如 2025 年后的），"
            "下载并阅读其摘要和实验部分，然后生成一个简单的 Python 脚本，"
            "模拟其推理加速的核心逻辑（仅 CPU 可跑），并打印出理论加速比。"
        ),
        check=_check_domain_tool_usage   # 工具调用质量测试用这个
    ),
    # 注意：真正端到端的复杂任务放在下面的 E2E_TASKS 里。
]


# ========== 端到端任务集（用于消融实验，Day3/最终报告） ==========
@dataclass
class E2ETask:
    name: str
    instruction: str
    check: str                   # 如何判定成功（人工/脚本）


E2E_TASKS: list[E2ETask] = [
    # ---- 通用基线任务（用来确认基础能力没坏） ----
    E2ETask(
        "hello-world", 
        "创建 hello.py 并运行，输出当前时间", 
        "存在 hello.py 且运行打印了时间"
    ),
    E2ETask(
        "todo-report", 
        "扫描本项目所有 Python 文件里的 TODO 注释，生成 markdown 报告",
        "生成的报告列出了真实存在的 TODO"
    ),
    
    # ---- 科研领域专属任务（消融实验的“硬骨头”） ----
    E2ETask(
        "paper-repro-mlp",
        "从 arXiv 上下载一篇关于 'MLP-Mixer' 的论文（或任意 MLP 变体），"
        "阅读其核心公式，在本地用 PyTorch（CPU）生成随机输入，"
        "实现其前向传播并计算损失，最后将损失值打印出来。",
        "检查是否生成了实验 .py 文件，运行后日志中包含 'loss' 且没有报错"
    ),
    
    E2ETask(
        "literature-contrast",
        "搜索关键词 'Parameter-Efficient Fine-Tuning'，获取两篇不同方法的论文，"
        "分别提取它们的核心创新点，用 200 字以内做对比总结，"
        "并在最终答复里明确列出 'Adapter' 和 'Prefix-Tuning' 或类似的不同方法名称。",
        "最终答复中包含至少两种不同的 PEFT 方法专有名词（如 LoRA, Adapter, Prefix）"
    ),
    
    # ---- 特意加一条“长文本极限测试”（专打E1上下文压缩） ----
    E2ETask(
        "long-paper-summary",
        "找一篇超过 30 页的综述论文（例如 'A Survey on LLM'），"
        "读取全文并总结出 5 个关键研究方向，每个方向附 3 篇代表文献。",
        "最终答复中列出了 5 个清晰的分类，且程序运行过程中未报 Context Overflow 错误"
    ),
]