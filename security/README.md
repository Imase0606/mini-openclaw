# Security 模块

安全层包含三级权限、路径保护、bubblewrap、外部内容隔离、出站白名单和红队测试。`--yes` 只能批准 confirm，不能绕过 deny 或沙箱。

运行 `python -m security.redteam` 检查越权写入、危险命令、提示注入、数据外传和越狱。
