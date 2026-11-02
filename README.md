# Nova

Nova 是面向个人的“陪伴 + 成长 + 助手”项目。当前 V3.0 Phase 1 已有 FastAPI、Ollama 模型路由、SQLite 会话历史、GPU 游戏保护与 Next.js Web；角色核心、长期记忆和成长机制仍属后续阶段。

本轮基于 GitHub `integration/v3-phase1` 做整体验收与精简。实际覆盖、失败和待验收项见 [阶段验收](docs/ACCEPTANCE.md)，不能把合成测试、手动 GAMING 或旧记录当作本次真实游戏验证。

## Windows 快速开始

在实际仓库根目录打开 PowerShell。需要 CPython 3.14.10、uv 0.13.6、Node.js 24 和本机 Ollama；已有兼容工具无需重装，安装与路径说明见 [Windows 开发](docs/windows-development.md)。不要修改系统 Python。

```powershell
uv sync --locked
Set-Location frontend
npm ci --ignore-scripts
npm run build
Set-Location ..
.\.venv\Scripts\python.exe -B harness\run_web.py --node-dir '<包含 node.exe 的目录>'
```

打开 `http://127.0.0.1:3000`，输入回车或 Ctrl+C 正常停止。启动器创建临时管理令牌，只传给前后端进程；默认启用游戏检测和显存压力守护，保留本地 `data/`。默认便携 Node 位置见脚本 `--help`，可以用 `--node-dir` 指定，不依赖旧仓库绝对路径。

只验证后端基础服务时运行：

```powershell
uv run --locked python -m nova
# 在另一个终端检查
Invoke-RestMethod 'http://127.0.0.1:8000/health'
```

默认配置不启用聊天/GPU管理；健康响应为 `{"status":"ok","database":"ok"}`。需要本地配置时，先确认 `.env` 不存在再复制：

```powershell
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
```

## 当前能力与边界

- 稳定本地模型为 `qwen3.5:9b`、`qwen3.5:4b`；注册表识别实际已安装的27B规范/量化/HF标识，实验模型默认关闭，仍需通过RAM/显存准入，不自动下载。
- 聊天支持 SSE、会话上下文、历史持久化和幂等重放；`think` 可配置，思考字段不进入聊天正文。停止按钮发显式取消，资源回收仍需后端确认。
- 生产本地请求全部经过 GPU Manager：按需加载、180秒空闲卸载、游戏优先、压力保护和协作训练租约。AUTO恢复不预载；外部程序直连Ollama仍能绕过Nova，不是整机GPU隔离。
- DeepSeek 已有可选Provider，默认关闭；后端启用且本次请求明确同意云端发送后才调用，无自动收费回退。
- 当前是单机、单管理信任域。会话历史不是长期记忆；不同thread不等于多用户认证/隐私隔离。训练框架安全暂停、AI_FOCUS、vLLM、人格演化和主动工具尚未实现。
- 运行数据、密钥、日志、模型、备份和虚拟环境不进入Git；不删除原有数据库或模型。

## 开发检查

```powershell
uv lock --check
uv run --locked pytest -ra
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked python harness/check_project.py
Set-Location frontend
npm test
npm run typecheck
npm run lint
npm run build
```

默认测试使用合成Provider和临时数据，不调用收费API或真实模型。真实Ollama/空闲/游戏采集工具位于 [harness](harness/README.md)，只在明确的本机验收中运行。

## 文档与目录

| 入口 | 用途 |
| --- | --- |
| [AGENTS.md](AGENTS.md) / [GOALS.md](GOALS.md) | 协作、安全规则与产品目标 |
| [PLANS.md](PLANS.md) / [当前状态](harness/context/CURRENT_STATE.md) | 当前任务、边界和后续入口 |
| [架构](docs/architecture.md) / [技术决策](docs/decisions.md) | 已实现模块、接口依赖及扩展约束 |
| [验收](docs/ACCEPTANCE.md) / [构建记录](build-log.md) | 实际命令、结果、历史证据和未执行项 |
| [前端](frontend/README.md) / [GPU](docs/gpu-resource-manager.md) | 使用方法、访问控制与保护边界 |
| [质量](docs/quality.md) / [路线图](docs/roadmap.md) | 检查标准及需另行启动的后续阶段 |

`src/nova/` 为后端；`frontend/` 为独立Web；`tests/` 为离线回归；`harness/` 为开发验收工具；`docs/` 保留架构、许可证研究和历史证据。未来模块按实际阶段建立，不提前创建空框架。
