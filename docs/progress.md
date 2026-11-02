# Nova 开发进度

## 2026-11-02：V3.0 Phase 1 合并阻断修复

在 `integration/v3-phase1`（含 PR #1/#2/#3）上完成两个小修复分支：GPU 安全修复 `de7e61f` 和聊天重放/错误契约修复 `e708b30`。修复游戏排空超时后的补偿卸载、chat/wake/generate 共享 27B 策略、取消后的显式回收责任、训练预留首次写盘失败回滚、已完成请求的只读幂等重放，并统一缺失模型为 `MODEL_NOT_INSTALLED`。

实际全量 `python -B -m pytest -ra` 为 788 passed、0 failed、0 skipped；两个已知缺陷 xfail 已移除。Ruff 静态与格式检查（107 文件）、工程自检五组、前端 Node 单元测试 25 项均通过。真实 GPU 17 项、真实取消 5 项、16GB 27B 拒绝检查通过；真实游戏进程切换未执行，保留待验收。证据、限制与命令见 [阶段验收](ACCEPTANCE.md)。

## 2026-11-01：无白名单升级

已研究五个项目并独立实现自动目录、原生信号、连续分类和独立显存压力保护；411项回归及新模式17项真实Ollama接口回合通过，无新依赖。读取到了目标两游戏的安装记录；用户选择自行启动后通知，真实运行/FPS等验收仍待执行。算法、性能、许可和已知误判详见 [升级说明](automatic-game-detection.md)。

## 2026-11-01：游戏进程白名单检测

首轮五项验收后，用户要求执行下一项。实现可选tasklist巡检、来源分离、60秒退出确认和故障保护，无新增依赖。实际266项测试通过、15项本机模拟进程/Ollama检查通过，详情见 [游戏检测说明](game-detection.md) 与 [阶段验收](ACCEPTANCE.md)。真实游戏EXE尚待用户提供，默认关闭/空名单；未扩展空闲调度、训练或vLLM。

## 2026-11-01：GPU 管理第一轮

用户明确新增GPU资源管理需求，先交付手动控制、状态、显存监控和测试。沿用原后端与依赖，无数据库schema改动。194项全量测试通过，实际Ollama/Qwen本机门禁回合、卸载复核与按需恢复通过，详细命令和证据见 [GPU管理说明](gpu-resource-manager.md) 及 [阶段验收](ACCEPTANCE.md)。

硬件检查确认显卡已为16GB，内存仍单条16GB。第一轮不自动检测游戏，不接管外部训练，不调用DeepSeek收费API。保留Phase0/Phase1历史，不将本轮诊断入口称为完整聊天功能；本轮验收后停止。

## 记录规则

使用用户时区 Asia/Shanghai；日期为 2026-10-27。只记录实际执行结果，区分工具安装、工程检查与业务测试。本文是执行证据，不代替 [实施计划](../PLANS.md) 中的范围定义。

## 当前阶段：Phase 1 后端基础架构

状态：已完成并通过本机验收，停止于 Phase 1；下文 Phase 0 记录是已完成的历史，不代表当前仍停留在 Phase 0。

- 已先只读阅读工程规则、目标、计划、实际小写文件名 `docs/architecture.md`、决策、质量和 Phase 0 验收记录。
- 开始检查时 `docs/ACCEPTANCE.md`、`harness/context/CURRENT_STATE.md`、`build-log.md` 不存在；本阶段按用户要求新建，没有假称此前已阅读。
- 原路线图的 Phase 1 对话闭环已被用户明确调整为后端基础架构；未开始 DeepSeek、聊天、前端或长期记忆。
- 用户在方案后明确回复“确认”；保存 19 个原交付文件基线后开始实现，未自动提交、推送或部署。
- 保留 Python 3.14.10、uv 0.13.6、pytest 9.1.1 和 Ruff 0.17.3；新增已说明的 FastAPI、配置和测试依赖，用 uv_build 可编辑安装。
- 初次 HTTPX 0.28.1 与当前 TestClient 组合出现弃用提示，核实支持后改用 HTTPX2 2.16.0，不屏蔽警告，不升级原有工具。
- 主执行者中间测试为 86 passed；增加 CLI、真实 loopback 服务器安全和日志回归后，最终 `uv run --locked pytest -ra` 为 **94 passed、0 failed、0 skipped、0 warnings，2.08 秒，退出 0**。
- 最终完整检查时间为 2026-10-27 23:36:13 +08:00：锁文件、环境同步、Ruff 静态、Ruff 格式（35 文件）、工程自检（五组 PASS）和 Git 空白检查均退出 0。
- 真实 CLI 独立初始化退出 0；本地端口 56352 健康 200、响应符合约定，404/405 和请求标识有效；临时库 application_id=1313822273、user_version=1、tables=[]、integrity_check=ok。
- Ctrl+C 后生命周期与 Uvicorn 关闭日志可见，进程消失、端口关闭。外层 PTY 因交互中断返回 1，未伪称为进程正常退出 0。
- Phase 0 快照 19 文件对比当前 42 文件：23 新增、15 修改、4 未改、0 删除。Git diff 与 cached stat 为空是未跟踪状态造成，另有快照补丁；原日期目录仍为 0 文件、9 子目录。
- 日志审查补齐 Basic 认证和带空格口令处理，两项定向回归及最终全套通过；未泄漏合成敏感值，未通过屏蔽警告获得成功。
- 本阶段无未完成的必需项；未验证第二台 Windows、Linux 或 VPS，未提供公网鉴权或生产部署。关键决策见 [技术决策](decisions.md)，完整证据见 [阶段验收](ACCEPTANCE.md) 与 [build-log](../build-log.md)。已停止于 Phase 1，未执行 Phase 2。

## Phase 0：项目基础设施与工程规范

状态：已完成并通过验收，停止于 Phase 0。最终完整检查执行于 2026-10-27 23:03:59 +08:00，随后仅补充验收文档并复查。

### 初始检查与授权

- 当前目录为 `C:\Users\Administrator\Documents\Codex`，只有 `2026-10-27` 和其下 9 个空子目录；未发现已有项目文件或适用的 AGENTS.md。
- `git rev-parse`、`git status`、`git log` 均报告当前不是 Git 仓库；未把这些结果当成已有仓库损坏。
- Git 2.53.0.windows.3 可用；系统 python 为 Microsoft Store 占位程序，py、uv、ruff 不在 PATH。
- 已先提供实施方案并等待确认；用户随后授权“全部交给你，需要什么就下载什么直至完成要求”。授权限于已讨论的 Phase 0，未扩展为开始 Phase 1。
- 新建独立 `nova/`，没有删除、覆盖、重命名或迁移原有文件和日期目录。

### 实施内容

- 建立中文工程协作、产品目标、实施清单、架构决策、阶段路线图、质量规范、Windows 说明和工程自检。
- 架构与路线图文档并行编写，最终由主执行者统一核对与检查。
- 规划模块化单体、FastAPI、SQLite、DeepSeek Provider、Web 与未来 Flutter；没有实现业务功能或业务空壳。
- 配置 CPython 3.14.10、uv、pytest、Ruff；直接运行时依赖为空。
- `.env.example` 密钥留空；建立密钥、运行数据、数据库及其 WAL/SHM、日志、缓存和环境忽略规则。
- 执行 `git init --initial-branch=main` 成功，退出码 0；没有创建提交、配置远程或推送。

### 工具安装与问题处理

| 操作 | 实际结果 | 处理 |
| --- | --- | --- |
| 下载并检查 uv 官方安装脚本 | 下载成功；脚本版本 0.13.6 | 安装到独立用户目录，不修改全局 PATH |
| 执行 uv 安装器 | 成功，uv --version 退出码 0 | 路径为 `%LOCALAPPDATA%\Nova\tools\uv-0.13.6\uv.exe` |
| uv python install 3.14 | 下载 CPython 3.14.10 后目录链接检查报错，uv 退出码 2 | 保留完整解释器，不声称该命令成功 |
| uv python install 3.14.10 --no-bin --no-registry | 同样退出码 2 | 未继续强制重装或删除目录 |
| 精确版本 python.exe --version 与标准库导入 | 退出码 0；Python 3.14.10；sqlite3、ssl、tomllib 可导入 | 固定精确补丁版本，避免依赖 minor-version 链接 |
| 下载官方 Python 3.14.10 x64 安装包 | SHA-256 与官方发布页一致，Authenticode 状态 Valid，签名方 Python Software Foundation | 作为备用安装尝试 |
| 官方 Python MSI 安装 | 退出码 3，日志为 0x80070003 路径错误，未完成安装 | 未采用该路径；项目使用已可用的 uv 下载解释器 |
| uv lock --default-index https://pypi.org/simple | 退出码 0，解析 8 项（含项目虚拟记录） | 生成真实 uv.lock |
| uv sync --locked | 退出码 0，创建项目 .venv，安装 7 个工具及传递依赖包 | 项目可复现环境已建立 |
| uv run --locked python / ruff / pytest 的版本命令 | 均退出码 0：3.14.10 / 0.17.3 / 9.1.1 | 只验证工具可执行，不算业务测试 |
| uv run --locked ruff format harness/check_project.py | 退出码 0，格式化 1 个新建脚本 | 仅调整本阶段自己新建的文件 |
| uv run --locked ruff check . | 退出码 0，All checks passed | 最终文件齐全后仍需统一验收 |

uv 安装脚本 SHA-256 为 `D00580051699BEE58A2E2D9B925F45788B35F7E0FFF7966FD5BEDFC3C547DC7D`，记录用于识别本次下载，不作为独立可信签名。

官方 Python 安装包 SHA-256 为 `92480239264744fe0386a1bd8275786fefa126e3129e56a68e22b52c73329965`，对照 [官方发布页](https://www.python.org/downloads/release/python-31410/) 核验。下载产物和安装日志保留在本次系统临时目录，不进入 Git。

### 最终验收

首次统一检查时，锁定、同步和 Ruff 检查已成功，但自检的 Git 忽略矩阵误报 27 个路径。实际原因是 Windows 文本 stdin 将 LF 转为 CRLF，Git 把 CR 当成路径的一部分。用直接路径检查确认规则有效后，将脚本改为 `-z` / NUL 分隔输入输出，并固定脚本输出为 UTF-8。修复后完整复查成功，没有放宽忽略规则或跳过验收。

| 实际命令或检查 | 退出码 / 结果 |
| --- | --- |
| uv lock --check | 0，锁文件与项目配置一致 |
| uv sync --locked | 0，已检查 7 个安装包 |
| uv run --locked ruff format --check . | 0，无需格式化 |
| uv run --locked ruff check . | 0，All checks passed |
| uv run --locked python harness/check_project.py | 0，必需文件、配置、环境示例、Git 边界、文本与链接五组全部 PASS，0 个问题 |
| uv run --locked pytest --version | 0，pytest 9.1.1；仅工具验证 |
| git diff --check | 0；未跟踪文件另由 harness 检查 |
| git status --short --branch | 0，main 尚无提交，只有本阶段交付文件未跟踪 |
| git remote -v | 0，无远程仓库 |
| 父目录与原目录复核 | 原日期目录下仍为 0 个文件、9 个子目录；父目录只新增 nova |
| 独立文档审查 | 架构、路线图、Windows 命令、边界及本地链接一致，无必修问题 |

交付文件共 19 个；拟交付目录为 `docs/` 和 `harness/`。本地 `.git/` 是仓库元数据，`.venv/` 和 `.ruff_cache/` 被忽略。没有创建 `src/`、`tests/`、`web/`、`data/` 业务目录。

### 工程自检故障注入验证

在系统临时目录为每个场景建立独立 Git 仓库，仅复制 19 个交付文件；未复制或修改正式 `.git`、`.venv`，未使用真实凭据。用项目 `.venv\Scripts\python.exe` 执行一次性 `run_fault_checks.py`，整体退出码 0，Python 版本 3.14.10。

| 场景 | 预期退出码 | 实际退出码 |
| --- | --- | --- |
| 正常基线 | 0 | 0 |
| 缺少 docs/quality.md | 1 | 1 |
| 非法 pyproject.toml | 1 | 1 |
| 环境示例敏感字段非空 | 1 | 1 |
| 删除 .env 忽略规则 | 1 | 1 |
| Markdown 本地链接不存在 | 1 | 1 |
| 强制跟踪应忽略的 .env | 1 | 1 |

各场景命中对应诊断，捕获输出均不含合成凭据值。证据目录为 `C:\Users\Administrator\AppData\Local\Temp\nova-phase0-fault-injection-d3d875631ace4a73bfec27aca180d793`，其中 `fault-check-results.json` 保存完整命令、输出与判定。临时材料不属于正式工程或业务测试套件。

### 未完成事项与限制

Phase 0 必需交付项无遗留。uv 的 minor-version 目录链接检查和备用 MSI 安装路径在本机仍有兼容性限制，未声称修复其机制；当前工程通过已经验证的精确 CPython 3.14.10 及项目虚拟环境运行，不依赖失败路径。排障和复现步骤见 [Windows 说明](windows-development.md)。

没有在第二台 Windows 机器、Linux 或 VPS 上验证；不声称跨平台部署已经完成。启发式密钥扫描和本地链接检查有明确范围，不能代替人工提交审查。Git 尚无提交和远程，符合本阶段约定。

### 业务测试与外部行为

业务测试未执行：尚无业务代码和测试用例；没有将空测试集视为通过。没有创建真实 `.env`、提供密钥、调用 LLM API、创建业务数据库、启动服务、发布或部署。

### 下一阶段建议

用户明确开始 Phase 1 后，再说明 FastAPI 与 DeepSeek HTTP 适配所需依赖，建立可测试的最小对话闭环和最小 Web；默认用 fake Provider 测试故障路径，真实 API 调用另由用户提供本地配置并明确发起。当前停止点为 Phase 0 验收完成。

