# Nova

Nova 是定位于“陪伴 + 成长 + 助手”的个人 AI 助手，目标是具有稳定人格、用户可控的长期记忆和受权限约束的工具执行能力。

**V3实验27B接入（2026-11-01）**：Qwen3.8-27B作为默认关闭的可选模型，9B/4B保留。用户选择后台下载；当前16GB系统RAM禁止加载，实际27B性能与下载校验尚未完成。代码658项及前端17项通过，Git发布缺少仓库/身份信息。当前仓库位于`AI-Workspace/nova`；启动、配置、卸载、回退和实际证据见[27B部署记录](docs/qwen38-deployment.md)，下方旧绝对路径为历史。

**Nova V2 本机Web版本（2026-11-01）**：独立Next.js/React/TypeScript + assistant-ui界面，FastAPI流式聊天、9B/4B注册切换、原SQLite会话历史与GPU保护接通。DeepSeek默认关闭，不自动收费回退。安装、启动、接口和边界见 [V2说明](docs/nova-v2.md)，实际验收状态以 [阶段验收](docs/ACCEPTANCE.md) 为准。

```powershell
Set-Location 'C:\Users\Administrator\Documents\Codex\nova'
.\.venv\Scripts\python.exe -B harness\run_web.py
# 打开 http://127.0.0.1:3000；输入回车或Ctrl+C正常停止。
```

以下为原版本历史记录；CS2核心链路已有 [实机补充验收](docs/cs2-live-acceptance.md)。

**无白名单升级已完成代码和自动回归（2026-11-01）**：自动读取GameConfigStore/Steam/Epic，组合前台/渲染/按进程GPU信号，增加独立显存压力保护；411项测试及开启新模式的17项真实Ollama回合通过，无新增依赖。无需维护EXE名单，启用方法见 [自动识别说明](docs/automatic-game-detection.md)。CS2和《红色沙漠》的实机运行测试仍待用户启动，不标为全部验收完成。下文保留旧版本历史。

**游戏白名单检测已完成开发和模拟验收（2026-11-01）**：5–10秒巡检、游戏退出持续60秒后恢复AUTO、手动GAMING独立保持。全量266项测试及15项本机模拟进程/Ollama检查通过，无新增依赖。真实游戏EXE尚待用户提供，默认保持关闭和空白名单，配置方法见 [游戏检测说明](docs/game-detection.md)。

**GPU 管理第一轮已验收（2026-11-01）**：新增手动游戏模式、受控模型加载/卸载、显存与模型状态查询；194 项离线测试、17 项本机真实验收通过。默认关闭，启用方法和 CLI 见 [GPU 管理说明](docs/gpu-resource-manager.md)。自动游戏检测、训练暂停、空闲监测服务和 vLLM 尚未实现。原有 Phase 1 结果保留在下文。

**Phase 1 后端基础架构已完成并通过本机验收**：FastAPI 应用、类型化配置、日志、统一异常、SQLite 基础及自动化测试。94 项测试通过，失败、跳过和警告均为 0；真实本地健康检查及数据库初始化通过。实际结果见 [阶段验收](docs/ACCEPTANCE.md)。尚未接入 DeepSeek、聊天、前端或长期记忆。

## 快速开始（Windows PowerShell）

打开项目目录：

```powershell
Set-Location 'C:\Users\Administrator\Documents\Codex\nova'
```

本机 uv 安装在用户工具目录，以下设置只影响当前 PowerShell 会话：

```powershell
$env:Path = "$env:LOCALAPPDATA\Nova\tools\uv-0.13.6;$env:Path"
uv --version
uv sync --locked
uv run --locked python -m nova
```

在另一个 PowerShell 中检查：

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health'
```

正常响应为 `{"status":"ok","database":"ok"}`。使用 Ctrl+C 停止服务。启动时按配置幂等初始化 SQLite，不需要 API Key；数据库无法初始化时启动失败，不假报健康。

默认配置即可运行。需要本地配置时，确认 `.env` 尚不存在后才复制示例，避免覆盖已有配置：

```powershell
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
```

完整安装、复现、配置和排障见 [Windows 开发说明](docs/windows-development.md)。如只想初始化数据库而不启动服务器，使用 `uv run --locked python -m nova --init-db`。

## 文档入口

| 文档 | 用途 |
| --- | --- |
| [AGENTS.md](AGENTS.md) | 代理与开发者的协作边界、密钥规范及验证规则 |
| [GOALS.md](GOALS.md) | 产品目标、非目标和阶段成功定义 |
| [PLANS.md](PLANS.md) | 当前阶段清单、退出条件和下一阶段入口 |
| [架构说明](docs/architecture.md) | 模块职责、依赖方向、关键数据流 |
| [技术决策](docs/decisions.md) | 选择理由、影响及重新评估条件 |
| [阶段路线图](docs/roadmap.md) | 分阶段交付物、验收条件与退出标准 |
| [质量规范](docs/quality.md) | 检查命令、测试策略、结果记录方式 |
| [开发进度](docs/progress.md) | 真实执行结果、问题和遗留事项 |
| [阶段验收](docs/ACCEPTANCE.md) | Phase 1 必需验收项与实际结果 |
| [当前状态](harness/context/CURRENT_STATE.md) | 下一次工作前应读取的阶段上下文 |
| [构建记录](build-log.md) | 依赖、命令、问题处理和结果索引 |
| [工程自检](harness/README.md) | 自检脚本的范围与限制 |

## 技术基线

CPython 3.14.10，uv 0.13.6 管理项目内 `.venv`、可编辑应用包及 `uv.lock`；pytest 负责自动化测试，Ruff 负责格式与静态检查。Phase 0 的 pytest 9.1.1 和 Ruff 0.17.3 保持不变。

本阶段使用 FastAPI、Uvicorn、Pydantic Settings 和标准库 sqlite3；HTTPX2 仅用于测试。使用 uv 原生构建后端进行 `src/nova` 可编辑安装，不发布包。没有引入 ORM、迁移框架或 Agent 框架；DeepSeek、Web 与 Flutter 仍属未来阶段。

## 配置与数据

配置优先级为进程环境变量高于本地 `.env`，高于默认值；路径以启动时当前工作目录为基准。`NOVA_HOST`、`NOVA_PORT` 和 `NOVA_LOG_LEVEL` 会用于实际启动。`.env` 可选，DeepSeek 相关变量只保留占位，当前不调用模型。真实密钥不能出现在源码、示例、日志或 Git 中。

`.gitignore` 排除 `.env`、虚拟环境、数据库及其 WAL/SHM 文件、日志、数据和备份目录。`uv.lock` 与 `.env.example` 应被跟踪。忽略规则不能替代提交前差异审查。

## 当前目录与未来模块

`src/nova/` 保存后端代码，`tests/` 保存离线测试，`docs/` 与 `harness/` 保存工程文档和检查。运行后 `data/` 保存未纳入 Git 的 SQLite 文件；`.git/` 保存仓库元数据，`.venv/` 保存开发环境。`agent`、`persona`、`memory`、`providers`、`tools` 和 `web` 仍未实现。

## 验证

```powershell
uv lock --check
uv sync --locked
uv run --locked pytest -ra
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked python harness/check_project.py
git diff --check
git status --short --branch
```

测试使用临时数据库和独立配置，不调用 DeepSeek。检查命令的实际执行结果以验收记录为准。Phase 1 验收后立即停止，由用户明确发起下一阶段。

