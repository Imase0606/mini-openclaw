"""Skills 加载器（Day9）。

Skill 与 Tool 的区别：
  - Tool 是一次函数调用（read 一个文件）。
  - Skill 是一包"领域知识 + 操作流程 + 可选脚本/资源"，用一个 SKILL.md 描述，
    在合适的时候被加载进上下文，告诉模型"面对这类任务该怎么一步步做"。

SKILL.md 结构（约定）：
  ---
  name: pdf-report
  description: 一句话说明何时该用这个 skill（用于召回判断）
  ---
  正文：步骤、注意事项、可调用的脚本路径、示例。

加载器要做：扫描 skills/ 下每个含 SKILL.md 的目录，解析 frontmatter，
按需把正文注入系统提示词 / 作为可发现的能力清单。
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path
    triggers: tuple[str, ...] = ()


def parse_skill_md(text: str, path: Path) -> Skill:
    import yaml
    name = description = ""
    body = text
    if text.startswith("---"):
        _, fm, body = text.split("---", 2)   # 头尾两个 --- 之间是 frontmatter
        meta = yaml.safe_load(fm) or {}
        name = meta.get("name", "")
        description = meta.get("description", "")
    raw_triggers = meta.get("triggers", []) if text.startswith("---") else []
    if isinstance(raw_triggers, str):
        raw_triggers = [raw_triggers]
    triggers = tuple(str(value).strip().lower() for value in raw_triggers if str(value).strip())
    return Skill(name=name, description=description, body=body.strip(), path=path, triggers=triggers)


def load_skills(root: str = "skills") -> list[Skill]:
    """扫描 root 下所有 SKILL.md。"""
    skills: list[Skill] = []
    for md in Path(root).glob("*/SKILL.md"):
        skills.append(parse_skill_md(md.read_text(encoding="utf-8"), md))
    return skills


def skills_catalog(skills: list[Skill]) -> str:
    """生成包含说明文件路径的 skill 清单，供模型按需读取。"""
    return "\n".join(
        f"- {s.name}: {s.description}\n  instructions: {s.path.as_posix()}"
        for s in skills
    )


def match_skills(task: str, skills: list[Skill]) -> list[Skill]:
    """Return skills whose explicit trigger terms occur in the user task."""
    task_lower = task.lower()
    matched: list[Skill] = []
    for skill in skills:
        if skill.triggers:
            if any(trigger in task_lower for trigger in skill.triggers):
                matched.append(skill)
            continue
        terms = {
            token.strip(",.，。!！?？:：;；()（）[]【】'\"、/")
            for field in (skill.name, skill.description)
            for token in field.lower().replace("-", " ").replace("_", " ").split()
        }
        terms.discard("")
        strong_terms = {term for term in terms if len(term) >= 2}
        if any(term in task_lower for term in strong_terms):
            matched.append(skill)
    return matched
