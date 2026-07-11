# Skills 模块

Skill 是按需加载的领域流程，不是一次函数调用。加载器读取 `SKILL.md` frontmatter，并对高置信任务预加载正文。

`video-summary` 规定 B站探测、转写、可选 OCR、类型化知识库和忠实性边界。仓库只保留一个视频 Skill，避免重复触发。
