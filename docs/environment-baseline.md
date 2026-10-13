# 基础环境与继承记录

检查日期：2026-09-27。范围：环境检查、项目文档与用户授权的新 Git 初始化；未安装依赖、修改全局环境、连接账户或运行交易。

## 已验证

| 项目 | 当前证据 | 结论 |
| --- | --- | --- |
| 工作目录 | `/mnt/t/alpaca-bot` | 后续命令显式指定目录 |
| 系统 | Linux / WSL2，内核 `6.6.114.1-microsoft-standard-WSL2` | 沿用 Ubuntu/WSL 工作流 |
| 系统 Python | 3.14.4 | 尚未验证 Alpaca SDK 及未来依赖的兼容性 |
| 命令可发现 | rtk、git、python3、uv、docker | 仅确认路径存在；不代表 Docker daemon 或全部工具功能已验证 |
| 项目初态 | 只有 `.agents`、`.codex`、空 `.git` | 无应用代码与既有环境可继承 |
| Git | 用户授权后执行 `rtk git init -b main`，随后状态检查通过 | 有效本地仓库，分支 main，尚无提交 |

## 可迁移的基础习惯

读取来源：`/mnt/t/invest-project-variation/AGENTS.md`、`pyproject.toml`、`.gitignore`。用户已确认该路径与口述项目对应。

源项目使用 Python 包结构、pytest、Ruff、严格 mypy，依赖含 HTTPX、Pydantic、WebSockets。迁移的是工作方式；不照搬 Kraken 包名、版本区间、服务配置、密钥或 `.venv`。

建议后续以项目独立 `.venv`、`src/`、`tests/`、版本锁文件搭建。Python 具体版本、Alpaca SDK 或直接 HTTP/WebSocket 接口的选择，先做兼容性验证再冻结；当前没有完成安装或运行时验收。默认不引入 GPU、量子计算、常驻 LLM、多代理投票服务。

## Git 环境异常与修复记录

- status: RESOLVED（原 ENV_FAULT）
- category: workspace Git metadata
- evidence: `.git` 是空目录；`git -C /mnt/t/alpaca-bot status --short` 返回 128。
- affected_scope: Git 状态、差异、提交与版本追踪。
- safe_to_continue: 公共资料研究、独立文本文件编写与直接读回检查。
- resolution: 用户明确要求创建新 Git。首次初始化被沙箱的 `.git` 只读限制拦截；同一限定操作通过提权审核后成功。`rtk git status --short --branch` 返回 0，显示 main 尚无提交及项目文档。未配置远端、推送或修改全局权限。

未尝试访问密钥文件、读取环境变量值或搬迁共享配置。Headroom 当前输出可读，但未审计其全局持久设置；不宣称全局环境完整验收。
