# Skills 模块

Skill 是按需加载的领域流程，不是一次函数调用。加载器读取 `SKILL.md` frontmatter，并对高置信任务预加载正文。

`video-summary` 规定 B站探测、转写、可选 OCR、类型化知识库和忠实性边界。`personal-video-knowledge` 负责检索历次提炼结果，并把个人知识回答与模型常识补充分区。`personal-video-knowledge-manager` 负责软删除、恢复、导出和永久清理，修改操作始终需要确认。

Skill frontmatter 可声明 `triggers` 列表，用于稳定召回中文意图；未声明时继续使用名称和描述词匹配。
