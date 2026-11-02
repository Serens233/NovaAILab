# Nova 阶段验收

## Codex 复审两项修复：实际验收通过（2026-11-02）

用户明确授权修复后在测试通过时合并 PR #5/#6 至 `integration/v3-phase1`。在独立 worktree 追加提交，保留其他 Agent 原工作区；GitHub PR 状态为合并结果的权威来源。

- P1：执行中的补偿卸载保持跟踪，关闭超时保留安全状态与实例锁。新增真实 `to_thread`/Event 回归在旧实现上 1 失败、1 通过，修后通过；覆盖重复关闭、第二实例拒绝、线程真实完成后释放。
- P2：未知、空、非法目标一律在后端调用前返回 `MODEL_POLICY_INVALID`（503）；旧未知目标 15 项回归先全部失败，修后通过，另有 2 项 API 装配回归。旧测试显式登记合成资源策略，没有新增生产绕过开关。
- 新增依赖为 0；锁文件及前端版本未改。为 Windows 干净检出补充前端扩展名 `eol=lf`，前端内容与 Git 原 blob 一致。初次 lint 因 CRLF 失败的日志保留，规范化后重验通过。

| 实际检查与命令 | 结果 |
| --- | --- |
| `.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -ra` | **809 passed，0 failed，0 skipped**，15.84 秒，退出 0 |
| `python -B -m ruff check --no-cache .` / `ruff format --check --no-cache .` | 均退出 0，107 文件格式通过 |
| `python -B harness/check_project.py` / `git diff --check` | 五组 PASS、0 问题 / 退出 0 |
| `uv lock --check` / `uv sync --locked --python <既有3.14.10解释器>` | 均退出 0，28 解析、27 安装；独立项目 `.venv` |
| `npm ci --ignore-scripts --offline` | 退出 0，227 锁定包；未执行安装脚本或升级依赖 |
| `npm run test` / `typecheck` / `lint` / `build` | **25 passed、0 failed、0 skipped**；其余均退出 0，构建 4.992 秒 |
| `python -B harness/probe_auto_games.py --samples 3 --interval 5 --output <新文件>` | 退出 0，3 次未确认游戏；预热后采集无降级，属于只读采样 |
| `python -B harness/verify_automatic_live.py --output-dir <新目录>` | **17 项真实 Ollama/GPU 检查通过**，退出 0；开启自动检测/压力守护，手动 GAMING、生成/wake 拒绝、卸载复核、数据库健康、重启保持和按需恢复通过；最终模型为空且验收后端正常退出 |

全部原始日志位于 `E:\NovaAILab\logs\codex-v3-final-fix-20261102`：`pytest-final.txt`、`frontend-validation-summary.json`、`native-preflight.json`、`live-automatic-final/report.json`。日志、DB 和模型权重不进入 Git。

当前 Windows 已实际可见约 32GB 级系统内存；本轮仅用既有 4B 做真实推理，没有加载 27B、训练、调用收费 API 或启动游戏。**真实游戏进程切换仍未验收**，不能用本次手动模式或替身回归代替。Phase 2 不在本次范围内。以下验收为历史记录。

## V3.0 Phase 1 合并阻断修复（2026-11-02，Asia/Shanghai）

集成基线为 `integration/v3-phase1`（PR #1 `2124e45` + PR #2 `984b890` + PR #3 `4c8d71f`）。修复分支：`fix/v3-gpu-safety`（`de7e61f`，游戏排空补偿、取消回收、训练预留回滚）和 `fix/v3-chat-replay-contract`（`e708b30`，共享27B策略、只读重放、`MODEL_NOT_INSTALLED` 契约），后者基于前者。验收使用包含两分支的 `integration/v3-phase1-verified`（取该分支验收时的 HEAD）。

- Draft PR #5 GPU 安全：https://github.com/Serens233/NovaAILab/pull/5
- Draft PR #6 聊天重放与错误契约（基于 #5）：https://github.com/Serens233/NovaAILab/pull/6
- 两个 PR 均保持 Draft，不自动合并 main；#5 合并后需把 #6 base 改为 `integration/v3-phase1`。

| 实际命令/检查 | 结果 |
| --- | --- |
| `python -B -m pytest -ra` | 退出0，**790 passed、0 failed、0 skipped**；两个 strict xfail 已删除并用回归覆盖 |
| `python -B -m ruff check .` | 退出0，All checks passed |
| `python -B -m ruff format --check .` | 退出0，107 files already formatted |
| `python -B harness/check_project.py` | 退出0，五组 PASS、0 问题 |
| 前端 `node --test tests/*.test.mjs` | 退出0，25 passed、0 failed、0 skipped |
| `harness/verify_gpu_live.py --output-dir <新目录>` | 退出0，17项真实Ollama/GPU检查通过 |
| 真实取消复核脚本 | 5项通过：取消返回 `CHAT_CANCELLED`、卸载复核、`/api/ps=[]`、故障清除、无待办卸载 |
| 16GB真实内存准入脚本 | 注册表、wake、generate 均在查询 tags 前返回 `MODEL_RAM_INSUFFICIENT`；未加载27B |
| `git diff --check` | 退出0 |

证据：pytest `E:\NovaAILab\logs\v3-merge-final-pytest.txt`；自检 `v3-merge-final-selfcheck.txt`；真实GPU `v3-merge-live-20261102-003208\report.json`；真实取消 `v3-merge-cancel-20261102-080601\cancel-report.json`；16GB准入 `v3-merge-16gb-20261102-080702.log`；Ruff `v3-verified-final-pytest-20261102-082029.txt` 同文件尾部。

**未执行/待验收**：未启动真实游戏，真实游戏进程切换仍待用户启动游戏后验收；手动GAMING切换、重启保持、生成/wake 423和卸载复核已由真实GPU脚本覆盖。前端 `typecheck`/`lint`/`build` 未执行：本机缺少已安装的 `node_modules` 和 npm 包管理器；仅运行了不依赖安装的 Node 单元测试。`uv lock --check`/`uv sync --locked` 未执行：本机 PATH 无 uv，测试使用既有 CPython 3.14.10 虚拟环境和 `PYTHONPATH=src`。

## V3实验接入：代码/安全检查通过，下载及真实27B推理待验

2026-11-01。`python -B -m pytest -ra`实际658 passed、0 failed、0 skipped，11.88秒；前端npm test17项通过，Ruff静态与98文件格式通过。工程自检五组PASS。原模型/API回归保留，新增实验项默认关闭及9B默认不变。

真实Windows/NVIDIA/Ollama采集及ASGI接口14项安全检查通过：总RAM15477MiB、当时可用2058MiB，实验SSE/JSON请求409且不加载，GAMING/wake423，健康保持200，AUTO不预载、数据库正常关闭。模拟27B生成/上下文/原提示词/参数/失败回退通过，不能当作真实27B推理。

用户选择后台下载并先交付代码，当前权重未校验完成；真实中文、CPU/GPU比例、首Token与tokens/s待内存升级后实测。功能分支已建，Commit/推送/PR缺remote及作者身份。原始日志、配置、7项交付和限制见[27B部署记录](qwen38-deployment.md)，不标完整部署完成。

## 补充：CS2 实机核心链路通过，完整游戏验收仍有未测项

2026-11-01，Asia/Shanghai。用户自行启动/退出，最终连续会话54样本、38次确认；12项复核通过、0失败：自动识别/来源、门禁和空模型复核、生成/wake423、健康200、失焦保持、退出61.215秒恢复AUTO不预载、按需回复及清理。Ollama最终为空，临时验收后端已正常关闭，没有改真实.env或注册自启。

最小化未记录到IsIconic=true，游戏前模型已空，未测FPS，红色沙漠尚未运行；这四项保留。原始第一轮工具提前采样的失败报告不覆盖；后两轮通过。原始报告、独立复核、工具修正范围与实际命令见 [CS2实机验收](cs2-live-acceptance.md)。此前411项业务回归是前轮实际结果，本次未重复执行。

## 无白名单升级：代码回归通过，真实游戏验收未完成

2026-11-01。用户要求先研究五项目和现有实现、给最小方案，再编码测试；均已执行。旧GameManager、控制接口、人工模式和数据保留。

| 实际验收/命令 | 结果 |
| --- | --- |
| `python -B -m pytest -ra` | 411通过、0失败、0跳过，5.77秒；含原266项及145项新增回归 |
| Ruff静态/格式、工程自检 | 全部退出0；格式检查74文件无需格式化 |
| `uv sync --locked` / `uv lock --check` | 均0；27项安装记录/28项解析，无新增依赖 |
| `harness/verify_automatic_live.py --output-dir <新目录>` | 实际开启auto/pressure，无白名单；17项真实Ollama/CLI/HTTP检查通过 |
| `harness/probe_auto_games.py` 6样本、5秒间隔 | PDH/Win32/DXGI实机可用，当前桌面/浏览器/壁纸未确认成游戏 |
| 性能测量 | 冷281.677ms、热中位66.463ms；25.812秒内CPU0.75秒（单核2.906%）；工作集26.48→35.43MiB |
| 本机自动索引 | GameConfigStore3、Steam8、工具2、Epic0；包含两目标游戏安装信息，不是运行证明 |

新增回归覆盖Steam/Epic/注册、未知全屏、浏览器/视频/AI/训练计算/壁纸排除、最小化/切屏/PID复用、counter不可用、共享采样、独立压力和空闲卸载/解除余量。全量初跑393过1败，是安全错误提示兼容字段遗漏，修复后最终411全过。没有弱化旧名单测试。

**未执行**：用户自行启动CS2和红色沙漠后的实际游戏检测、最小化/切屏/退出和FPS影响；没有制造真实显存耗尽或运行真实训练。压力触发为合成遥测测试，不能伪称真实高负载验收。开发/自动回归通过不等于所有用户验收已完成。

证据：`E:\NovaAILab\logs\gpu-auto-detection-20261101-043203-813`下`pytest-final.txt`、`native-probe.json`、`live-automatic/report.json`。详细文件、依赖、算法、性能和已知误判见 [升级说明](automatic-game-detection.md)，五项目源码/许可研究见该文档链接。

## 游戏进程检测（2026-11-01，开发与模拟验收通过）

用户要求首轮通过后执行下一项。仅实现可选进程白名单检测，不扩展独立空闲调度、训练或vLLM。

| 实际命令/检查 | 结果 |
| --- | --- |
| `uv run --locked pytest -ra` | 退出0，266通过、0失败、0跳过，4.98秒；原194项全部回归 |
| `ruff check --no-cache .` / `ruff format --check --no-cache .` | 均退出0；55个Python文件无需格式化 |
| 工程自检、`uv sync --locked`、`uv lock --check` | 均退出0；无新依赖，27项安装记录/28项锁解析 |
| `python -B harness/verify_game_detection_live.py --output-dir <新目录>` | 退出0，15项真实本机检查通过 |
| 自有模拟进程出现 | 真实tasklist命中该唯一EXE名，自动GAMING、卸载复核成功，推理/wake为423 |
| 手动AUTO绕过 | 模拟进程存在时返回409，后端健康继续200 |
| 正常退出与恢复 | 自有模拟进程正常退出，连续缺席约63.237秒才恢复AUTO，未预载；随后推理可恢复 |
| 卸载与资源 | 一次采样5716→1937MiB，配合模型状态；最终显式卸载，模拟和验收后端正常结束 |

离线回归另验证：手动GAMING或显式/unload不会因游戏退出解除；故障/重现重置60秒计时；扫描失败不伪装为空；来源跨重启保持；启动首扫门禁；迟到/重叠扫描不能绕过保护；循环及数据库正常关闭。曾发现检测器构造早于状态加载导致首扫过早解锁，已修复为首次扫描读取保存标志，未削弱测试断言。

证据：`E:\NovaAILab\logs\gpu-game-detection-20261101-040957-872\live-1\report.json`；全量输出为同目录上级`pytest-1.txt`。模拟使用系统cmd副本等待标准输入，正常写入一行退出，不杀用户进程、不启动真实游戏。

**部署限制**：真实游戏EXE尚待用户提供，默认检测关闭/空名单；没有宣称已验证真实游戏兼容性。原始Ollama11434仍可绕过Nova门禁，外部训练未受接管。见 [游戏检测说明](game-detection.md)。

## GPU 管理第一轮（2026-11-01，已通过）

用户明确要求第一轮只交付模型查询、手动游戏模式、卸载/恢复、显存监控和自动化测试。本节为当前新增验收，下文保留 Phase1 历史。

| 实际命令/验收 | 结果 |
| --- | --- |
| `uv run --locked pytest -ra` | 退出0，194 passed、0 failed、0 skipped，4.25秒；包含原94项回归 |
| `ruff check --no-cache .` | 退出0 |
| `ruff format --check --no-cache .` | 退出0，48个文件无需格式化 |
| `uv sync --locked` / `uv lock --check` | 均退出0；27项环境安装记录/28项锁解析记录，无新依赖 |
| `python -B harness/verify_gpu_live.py --output-dir <全新目录>` | 退出0；17项本机真实验收全部通过 |
| 中文诊断推理 | Qwen3.5:4b实际回复中文、GPU模型驻留为正 |
| GAMING | 模式先锁定，卸载后`/api/ps=[]`；生成/wake均423，重复GAMING不重载 |
| 恢复与持久化 | 重启验收后端仍GAMING；切AUTO不预载；显式wake和下一条推理均可恢复 |
| 后端/DB | 游戏模式下`/health`保持200；仅使用独立验收库，未改用户数据库/schema |
| 显存与进程 | 一次采样5708→1927MiB；模型列表空且llama-server.exe消失，非固定显存门槛 |

显卡实际为RTX5060Ti 16311MiB；内存仍为单条16GB，Windows11专业版build26100。离线失败测试覆盖卸载未完成、服务不可达、排空超时、取消和控制竞争、重启/坏状态、审计/写盘失败、权限拒绝及清理故障时DB释放。

实测证据：`E:\NovaAILab\logs\gpu-round1-20261101-034738-331\live-with-cli\report.json`；全量测试输出为同目录上级的`pytest-release.txt`，开工快照为`baseline.json`。首次实测成功记录保留在`live-1`。真实验收结束时卸载模型、正常退出验收后端；Ollama服务保留，不启动收费调用或任意训练。

**范围限制**：没有现成Nova聊天界面，本轮通过受控诊断推理入口验收门禁。训练租约为空时显示 `training.state=idle`（不表示没有外部训练），未验证真实训练暂停；独立3分钟空闲调度、游戏进程模拟检测、AI_FOCUS、DeepSeek、vLLM和UI未实现，对应总体任务中的测试留待后续。外部直接访问11434的程序仍可加载模型。首轮通过后停止，使用方法见 [GPU管理说明](gpu-resource-manager.md)。

## 当前状态

Phase 1 后端基础架构已完成并通过本机验收，停止于 Phase 1。最终完整检查时间为 **2026-10-27 23:36:13 +08:00（Asia/Shanghai）**，随后仅进行验收文档收尾和文档检查。Phase 0 的实际验收历史位于 [进度记录](progress.md)，本文件在 Phase 1 新建，不声称 Phase 0 时已经存在。

## 验收范围

FastAPI 应用、生命周期与健康检查；环境和 `.env` 类型化配置；标准日志；统一异常响应；SQLite 连接、事务、关闭与非破坏性初始化；自动化测试和 Windows 本地运行。

不接入 DeepSeek、不实现聊天、前端、长期记忆、业务表或复杂 Agent 框架。

## 必需结果清单

| 验收项 | 实际命令 / 结果 | 状态 |
| --- | --- | --- |
| 依赖锁文件 | `uv lock --check`，退出 0，解析 28 项记录 | 通过 |
| 环境同步 | `uv sync --locked`，退出 0，27 项安装记录含项目 | 通过 |
| 自动化测试 | `uv run --locked pytest -ra`，退出 0，94 passed、0 failed、0 skipped、0 warnings，2.08 秒 | 通过 |
| 健康检查 | 实际 GET `http://127.0.0.1:56352/health` 为 200，响应 `{"status":"ok","database":"ok"}`，请求标识有效；另验证 404/405 | 通过 |
| 数据库初始化 | `uv run --locked python -m nova --init-db` 退出 0；实际元数据、完整性和无业务表检查见下文 | 通过 |
| Ruff 静态检查 | `uv run --locked ruff check .`，退出 0 | 通过 |
| Ruff 格式检查 | `uv run --locked ruff format --check .`，退出 0，35 个文件无需格式化 | 通过 |
| 工程自检 | `uv run --locked python harness/check_project.py`，退出 0，五组 PASS、0 个问题 | 通过 |
| Git 与快照差异 | `git diff --check` 退出 0；19→42 个交付文件，23 新增、15 修改、4 未改、0 删除 | 通过 |
| 阶段退出 | 生命周期退出日志可见，进程消失、端口关闭；所有关键项通过，停止于 Phase 1 | 通过 |

## 自动化与安全验证

94 个测试覆盖配置、应用生命周期、健康、统一错误、SQLite 会话与初始化、日志和 CLI。全部使用独立配置及临时资源，不调用 DeepSeek。测试包含实际 loopback Uvicorn 500 异常的响应与日志安全、服务器关闭；日志审查补齐 Basic 认证及带空格口令处理后，两项定向回归通过，最终全套无警告。

## 真实本地运行与数据库

在临时验收目录设置数据库路径及端口 56352，先运行 `uv run --locked python -m nova --init-db`，再运行 `uv run --locked python -m nova`。实际健康、404、405 和请求标识检查成功。

临时库实际检查结果：`application_id=1313822273`（`0x4E4F5641`）、`user_version=1`、`tables=[]`、`integrity_check=ok`。没有创建聊天或记忆业务表。

Ctrl+C 后，日志出现 `application_stopped` 与 `Application shutdown complete`，进程 23396 消失且端口关闭。**外层 PTY 因 Ctrl+C 返回 1**，属于本次交互中断记录；不把它写成进程正常退出码 0。关闭行为另有自动化测试覆盖。

## 差异与证据

Git `main` 尚无提交，工程文件均未跟踪；`git diff --stat` 和缓存差异为空，不能表示没有改动。以 Phase 0 快照比较得到 23 新增、15 修改、4 未改、0 删除；没有暂存、提交或推送。原日期目录仍为 0 文件、9 个子目录。

证据目录为 `C:\Users\Administrator\AppData\Local\Temp\nova-phase1-acceptance-cd652a5e5d804f65a123939115b6cbc2`，包含 `pytest-release.txt`、`check-results.json`、`http-results.json`、`database-results.json`、`shutdown-results.json`、`diff-summary.json` 和 `phase0-to-phase1.patch`。补充最终验收文档会影响补丁行数，因此不将文档收尾前的行数当作最终统计。

## 结果记录要求

记录实际执行命令、日期和时区、退出码、通过/失败/跳过数量与输出摘要。未运行、失败和跳过分别说明，不能预测结果或把版本查询当作测试。

## 已知限制

- 本阶段无未完成的必需交付项。Git `main` 尚无提交和远程；未跟踪状态及差异证据已如实说明。
- 不声称在第二台 Windows、Linux 或 VPS 上验证。
- 后端仅供本地开发，未交付公网鉴权、生产部署或业务能力。
- 本机 Phase 0 的安装器兼容性问题及可用精确解释器路径保留在进度记录。

下一阶段须由用户明确发起，不能从本验收或路线图自动继续。
