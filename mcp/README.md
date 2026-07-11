# MCP 模块

最小 stdio JSON-RPC 客户端实现 initialize、tools/list 和 tools/call。启动与请求均有超时，失败时关闭子进程；WSL 要求 Linux npx 优先于 Windows npx。

MCP 工具进入统一 Registry 后仍受权限确认。视频最小权限模式不启动通用 MCP server。
