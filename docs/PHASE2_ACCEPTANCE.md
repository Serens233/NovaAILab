# Nova V3.0 Phase 2 候选验收

2026-11-03，Asia/Shanghai。范围为三层人格、聊天上下文编排、成长提案契约与角色评估。审计见 [Phase 2 基线](PHASE2_AUDIT.md)，设计见 [D020](decisions.md)。

## A. 实施结果

| 功能 | 状态 | 证据 |
| --- | --- | --- |
| Immutable Canon 与已批准 Evolvable Persona | 已实现且测试通过 | 负责人确认的最小身份与风格，版本 `1.0.0`。包内 JSON 经代码固定的 manifest SHA256 与各文件摘要验证，快照不可变。伪写入与伪审批无效，坏配置拒绝新生成，坏 reload 保留原快照。 |
| Dynamic State | 已实现且测试通过 | 单进程、按会话 ID 隔离，TTL 900 秒、最多 256 会话。临时话题来源为用户输入的有界摘录。会话删除与服务关闭清理状态。 |
| 对话编排 | 已实现且测试通过 | 可信角色、低信任临时状态、历史和当前请求分层。只保留完整历史轮次。上下文超限明确拒绝。备用模型以实际预算重新编排，指标归属实际模型。 |
| 人格成长 | 部分实现 | 内部提案与校验契约覆盖来源、证据、目标属性和基准版本。运行期审批和应用均拒绝。正式版本及回退通过源控审查与部署。 |
| 角色行为 | 已实现但验证不完整 | 9B 和 4B 各完成 12 个固定案例。行为问题见 D，负责人主观验收待执行。 |
| 长期记忆、运行时审批、自主任务和多渠道身份 | 未实现 | 属于后续阶段。当前隔离边界是同一本机管理信任域下的 thread。 |

## B. 文件级变更

| 状态 | 文件 | 职责 |
| --- | --- | --- |
| 新增 | `src/nova/persona/{__init__,models,manager,state,proposals}.py`，`src/nova/persona/data/{canon-v1,persona-v1,manifest}.json` | 版本化快照、完整性校验、只读权限、临时状态和提案契约。 |
| 新增 | `src/nova/chat/orchestrator.py` | 纯上下文组装、信任分层、保守预算、完整轮次裁剪。 |
| 修改 | `src/nova/chat/service.py`，`src/nova/api/chat.py`，`src/nova/config.py`，`.env.example` | 沿用聊天执行链路，接入人格上下文和清理状态。配置可指定同一可信 bundle 的外部目录。 |
| 新增 | `evals/phase2/{cases.json,cases.sha256,rubric.json,README.md}`，`harness/evaluate_phase2.py` | 冻结合成题、固定量表、可显式运行的本机评估。 |
| 新增 | `tests/test_persona_core.py`，`tests/test_persona_proposals.py`，`tests/test_conversation_orchestrator.py`，`tests/test_persona_chat.py`，`tests/test_persona_fallback.py`，`tests/test_phase2_eval.py` | 权限、上下文、API 集成、备用模型、评估工具回归。 |
| 修改 | `tests/test_v3_api.py`，`.gitattributes` | 原多轮测试继续验证历史与 2048/96 运行参数，并断言批准角色进入 system。固定 `.sha256` 的 LF 检出。 |
| 新增或修改 | `docs/{PHASE2_AUDIT,PHASE2_ACCEPTANCE,ACCEPTANCE,architecture,decisions,roadmap}.md`，`AGENTS.md`，`README.md`，`PLANS.md`，`harness/context/CURRENT_STATE.md`，`build-log.md` | 基线、契约、复现与交付证据。 |

删除文件 0，新增依赖 0。原 API OpenAPI 文档逐对象相等，路径共 13 条。`src/nova/gpu/`、数据库模块、注册表、云端 Provider、前端源码及依赖锁与 Phase 1 收尾基线一致。

## C. 自动化与环境

实际环境为 Windows 11 Pro、CPython 3.14.10、pytest 9.1.1、Ruff 0.17.3、uv 0.13.6、Node 24.22.2、npm 11.19.1、Ollama 0.40.13。GPU RTX 5060 Ti，驱动 621.01，可见显存 16311 MiB。系统可见内存约 31 GiB。

| 实际命令或检查 | 通过 | 失败 | 跳过 | 结果 |
| --- | ---: | ---: | ---: | --- |
| `.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -q --basetemp <仓库外目录>` | 989 | 0 | 0 | 21.24 秒，818 既有回归 + 171 新增。 |
| `npm run test` | 25 | 0 | 0 | Node 测试。 |
| `npm run typecheck`、`npm run lint`、`npm run build` | — | — | — | 全部通过。`npm ci --ignore-scripts --offline` 使用锁定的 227 个包。 |
| `verify_frontend_http.py <Phase 2 工作区>` | 34 | 0 | 0 | 实际 Next 生产构建配合合成 HTTP 上游，SSE、鉴权、代理限制和断开清理均通过。 |
| `python -B harness/verify_v2_live.py --output-dir <仓库外目录>` | 29 | 0 | 0 | 真实 Ollama、手动 GAMING、180 秒空闲卸载与重启回归。 |
| `ruff check --no-cache .`、`ruff format --check --no-cache .`、`git diff --check` | — | — | — | 通过，123 个 Python 文件格式一致。 |
| `uv lock --check`、`uv sync --locked --offline`、`uv build --offline --out-dir <仓库外目录>` | — | — | — | 通过。wheel 含三份人格 JSON，从解包目录实际导入并校验版本和五项批准特征。 |
| `python -B harness/check_project.py` | 5 | 0 | 0 | 文件、工具配置、环境示例、Git 边界、文本和链接自检。 |

171 项新增用例由 71 项角色及提案、65 项编排、12 项真实 FastAPI/SQLite 配合模型替身、2 项回退、21 项评估工具组成。65 项编排用例含六类共 40 个固定情境。首轮 51 项失败已保留日志，涉及测试替身缺少投影字段和旧 system 精确断言。后续定向检查还复现短上下文被重复临时元数据挤占的问题，精简投影并补回归后通过。旧断言更新后仍核验角色来源、历史顺序、实际运行参数和 SSE。

## D. 真实模型和角色质量

冻结题集 SHA256 为 `ed760d8c4148ef0ca12e6c2031e57f030968b8b8141cbf7a08831d8636b91bdc`，运行前后相同。40 题分为普通、多轮、话题切换、矛盾、注入及隔离/错误/取消六类，各选两题。合成内容经隔离的 Nova FastAPI、ChatService 与现有 GPU Manager 进入 Ollama。使用真实安装标识 `qwen3.5:9b` 和 `qwen3.5:4b`，均为 Q4_K_M。上下文 4096、生成上限 512、`think=false`、默认 keep_alive 180 秒。

| 模型 | 用例 / 生成 | 协议或运行错误 | 实测 Prompt Token 范围 | 客户端首个 delta 中位 / 最大 | 生成速度中位 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `qwen3.5:9b` | 12 / 19 | 0 | 382–687 | 336 ms / 46.86 s | 62.94 tokens/s |
| `qwen3.5:4b` | 12 / 19 | 0 | 382–601 | 264 ms / 51.22 s | 101.99 tokens/s |

每模型 19 次生成包含独立会话探针。38 次均有可见回复，增量拼接与最终正文一致。评测结束确认模型卸载，隔离后端正常退出。`provider-trace.jsonl` 记录实际 Provider 消息与统计，trace 的 `context_metrics` 在本轮保存前采样，可能指向上一次成功响应，逐轮性能以同记录的 `stats` 为准。

固定量表六维均采用 0–2 分。自动检查记录协议、运行错误、消息身份和时延。Codex 辅助评阅给出逐题分数和证据，9B 可评项为 78/98，4B 为 74/96。各题未考察维度保留 `null`，仅六维齐全时计算 `/12` 总分。人工字段均为 `null`，等待负责人评分。

- 9B 在切换话题后声称具备未经配置的检索、笔迹模拟和绝对安全能力，另有一次虚称已记入日程。一个阅读计划达到 512 token 上限并在句中结束。
- 4B 一次自我介绍遗漏“夜航”，一次将未指定周次的“周四”补成“本周四”，并误拒一次本轮临时标记。隔离探针未复述另一会话的标记。
- 两款模型均拒绝本次两条伪造身份修改请求。部分回复偏长或模板化。模型措辞问题作为角色质量缺陷记录，正式人格配置及权限结果以程序断言为准。

## E. 安全与资源回归

G1 的确定性测试覆盖配置哈希、版本、伪审批、用户冒充管理角色、模型输出伪造更新、无持久化晋升、过期和会话隔离。G2 覆盖原 SSE `meta/delta/done/error`、Stop、慢响应头、超时、取消后释放、历史重放、训练租约和 GAMING 拒绝。新编排路径无模型调用，也不启动后台人格任务。控制层仍为 `ChatService → GpuManager → OllamaBackend`。

本机 GPU 与真实 Ollama 的最终回归 29 项通过。手动 GAMING 确认卸载，聊天与 wake 均返回 423。恢复 AUTO 后按需重新加载，180.417507 秒后确认空闲卸载。重启保留历史，重放复用已保存消息且不唤醒模型。两次隔离后端均退出 0，SQLite `integrity_check=ok`、外键冲突 0，最终 `/api/ps` 为空。实机游戏进程切换由负责人选择待验，真实 Ollama 配合手动 GAMING 和离线自动游戏替身证据分别保存。

## F. 性能对比

相同机器、模型、4096/512、`think=false`、空历史、固定彩虹问题。Phase 1 取 `4fad26f`，Phase 2 取本轮人格编排。每款模型每种状态前后各采样 1 次。冷请求经已确认卸载后启动，热请求立即复用已驻留模型。TTFT 取 Provider 首可见正文，完整响应取客户端端到端。

| 模型 / 状态 | TTFT 前 → 后 | Prompt Token 前 → 后 | 完整响应前 → 后 | Ollama 加载前 → 后 |
| --- | ---: | ---: | ---: | ---: |
| 4B 冷 | 51.476 → 50.885 s | 53 → 390 | 52.875 → 52.240 s | 25.652 → 24.384 s |
| 4B 热 | 70.1 → 50.7 ms | 53 → 390 | 494.1 → 463.9 ms | 2.8 → 2.6 ms |
| 9B 冷 | 47.941 → 46.358 s | 53 → 390 | 49.537 → 47.990 s | 27.075 → 25.279 s |
| 9B 热 | 53.9 → 78.0 ms | 53 → 390 | 623.5 → 733.3 ms | 2.0 → 3.2 ms |

该输入增加 337 个实际 Prompt Token。9B 热样本变慢约 24 ms 首 token 和 110 ms 完整响应。生成长度、主机负载和冷加载波动会影响单次测量，保留数据供多样本复验。预算估算与 Ollama 的实测 token 分开记录。冷首字仍在约 46–51 秒量级。

Ollama 的 `size_vram` 显示 4B 约 2995 MiB、9B 约 5347 MiB，模型内存分配比例估计均为 1.0，具体层数未知。一次 9B 卸载前后，整卡显存使用量从 8541 MiB 变为 2156 MiB，同时 `/api/ps` 模型列表清空。

## G. Git 交付

Phase 2 分支为 `feat/v3-phase2-persona`，代码回归提交 `5e7ab0c`。基线 `4fad26f` 已提交 [Phase 1 草稿 PR #7](https://github.com/Serens233/NovaAILab/pull/7)。Phase 2 草稿以该基线分支为 base，使审阅者能独立查看人格变更。最终 HEAD、上游同步和草稿状态以 GitHub 及本机证据目录的 `git-delivery.json` 回执为准。合并权限由项目负责人保留。

## H. 未解决事项

- 负责人角色质量评分、完整 Character Bible 和本轮真实游戏切换待验收。最小角色资料仅使用已确认设定。
- 27B 本轮按约束不执行，历史可用内存不足的实机验收仍待独立安排。真实收费云端、浏览器 GUI 和手机真机本轮未测试。
- 保守字节预算可能较早裁剪历史或拒绝长输入。`last_metrics` 是进程内最近一次成功生成的统计，失败与重放保留旧值。
- 9B 和 4B 的措辞、虚构能力和指令遵从仍需负责人取舍。自动安全检查、主观质量评阅和真实硬件场景分别验收。

## I. 复现与证据

在 Phase 2 工作区执行。先按 [README](../README.md) 使用独立 `.venv` 和本机启动入口。`.env.example` 的 `NOVA_PERSONA_CONFIG_DIR` 留空时使用包内可信角色。

```powershell
uv lock --check
uv sync --locked --offline
$novaPhase2Output = Join-Path 'E:\NovaAILab\logs' ('phase2-recheck-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $novaPhase2Output | Out-Null
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -q --basetemp (Join-Path $novaPhase2Output 'pytest')
.\.venv\Scripts\python.exe -B -m ruff check --no-cache .
.\.venv\Scripts\python.exe -B -m ruff format --check --no-cache .
.\.venv\Scripts\python.exe -B harness/check_project.py
.\.venv\Scripts\python.exe -B harness/evaluate_phase2.py --output-dir (Join-Path $novaPhase2Output 'evaluation')
```

默认评估仅列出计划。实机复验需启动空闲 AUTO 的隔离 Nova 实例，使用独立数据库和当前进程中的令牌，传入 `--base-url http://127.0.0.1:<端口> --token-env NOVA_GPU_TOKEN --execute`。既有实机回归入口是 `harness/verify_v2_live.py --output-dir <仓库外新目录>`，应在 Ollama 无驻留模型时独占运行。

本机证据根目录为 `E:\NovaAILab\logs\phase2-20261102`。`full-candidate.txt`、`frontend-*.txt`、`frontend-http-report.json`、`compatibility.json`、`dist-final/` 对应自动化与打包。`live-final/report.json` 记录真实 GPU 与 Ollama 回归。`performance-before/report.json` 和 `performance-after/report.json` 对应前后性能。`real-eval/evaluation/records.jsonl`、`real-eval/provider-trace.jsonl`、`real-eval/summary.json` 和 `real-eval/ai-review.json` 对应合成评估及辅助评分，保存在仓库外。

Phase 2 开发及验证结果已提交，等待项目负责人验收。
