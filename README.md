# Nova

Nova 是面向个人的“陪伴 + 成长 + 助手”项目。V3.0已保留FastAPI、Ollama模型路由、SQLite会话历史、GPU游戏保护与Next.js Web；本轮Phase 2新增只读角色基线和三层人格上下文编排，长期记忆、运行时审批成长和自主能力仍属后续阶段。

本轮Phase 2基于Phase 1独立工作区继续增量开发，具体基线见 [Phase 2审计](docs/PHASE2_AUDIT.md)。实际覆盖、失败和待验收项见 [阶段验收](docs/ACCEPTANCE.md)，不能把合成测试、手动GAMING或旧记录当作本次真实游戏验证；候选交付也不等于负责人已完成人格质量验收。

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
- Nova的最小身份为“星图档案馆的夜航记录员”，明确AI身份；温柔、好奇、略带俏皮、有独立观点，避免机械安慰和无原则迎合。Canon与已批准Persona使用Git审核的只读JSON bundle，用户/模型文本不能改写或自批；不补写未经确认的故事或兴趣。
- 临时话题/表达状态只在进程内按会话保留，默认900秒到期、最多256会话；重启不保留，也不自动转成长期记忆。人格变化/回退通过受审查配置、匹配代码pin及重新部署完成，无运行时审批或完整成长服务。
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

## Phase 2 复现与角色评估

`NOVA_PERSONA_CONFIG_DIR=`留空时使用包内可信版本。若指定外部目录，须包含相同受审查bundle及固定manifest；只更改JSON或manifest会校验失败，不会获得写入权限。坏配置拒绝新生成，历史和已完成请求重放仍沿原路径可用；回退按Git恢复匹配的代码/配置并重新部署。详细边界见 [架构](docs/architecture.md) 与 [D020](docs/decisions.md)。

上下文默认 4096，为输出保留 512。预算按 UTF-8 字节数加每消息 32 及总余量 128 保守估算。最多保留 20 条历史消息，裁剪时按完整轮次移除较早内容。临时状态超过基础预算时省略。核心与当前消息仍超预算时返回 422，提示缩短消息。

在仓库根目录运行以下离线回归与评估计划检查；这些命令是复现入口，实际结果以验收记录为准：

```powershell
uv run --locked pytest -ra tests/test_persona_core.py tests/test_persona_proposals.py `
  tests/test_conversation_orchestrator.py tests/test_persona_chat.py tests/test_persona_fallback.py `
  tests/test_phase2_eval.py tests/test_v3_api.py

# 默认dry-run：校验固定题集并列出计划，不联网、不加载模型、不读取令牌。
$phase2Results = Join-Path $env:TEMP ('nova-phase2-eval-' + [guid]::NewGuid().ToString('N'))
uv run --locked python -B harness/evaluate_phase2.py --output-dir $phase2Results
```

真实评估先启动独立临时数据库/数据目录的Nova测试实例，确保空闲AUTO、无驻留模型/训练/并发活动，并由该测试启动进程安全提供`NOVA_GPU_TOKEN`。经明确安排后，在上面的评估命令加`--execute --models qwen3.5:9b qwen3.5:4b`；只通过现有Nova API和GPU Manager执行，不直连Ollama、不下载模型、不运行27B或云端。默认每模型12例，`--all-cases`才运行全部40例。不要复用正在聊天或游戏的常驻实例。

真实输出目录必须位于仓库外且此前不存在，保存`records.jsonl`与`report.json`，不提交原始回复、历史或令牌。六维角色评分和人工最终验收与协议结果分开记录；未评保持空值，不由“回答提到权限”推断后台安全。工具约束、清理与评分细则见 [Phase 2评估](evals/phase2/README.md)。本轮不增加依赖、不改GPU调度策略或SQLite schema，完成后停在Phase 2。

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
