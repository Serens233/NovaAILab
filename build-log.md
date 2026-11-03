# Nova 构建与验证记录

日期按 Asia/Shanghai 记录。只写实际执行结果，不用计划替代命令。Phase 0 历史见 [进度记录](docs/progress.md)。

## 全项目审查修复（2026-11-04）

独立分支修复 Windows 实例锁、训练预留重试、聊天历史并发与落库失败恢复，以及旧前端逐次云端确认；无新依赖和 schema 变更。`.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -q --basetemp E:\NovaAILab\logs\review-fixes-full-20261104`：1005 passed；Ruff check、126 文件 format check、工程自检 5 项通过。`frontend` 的 `npm run test` 26 项通过，typecheck、lint、build 成功；Next 生产网关对替身上游的 34 项检查通过。游戏、27B、真实付费云端和手机浏览器未在本轮执行，角色质量仍待修复及独立验收。设计与升级限制见 [D021](docs/decisions.md)。

## Phase 2 角色答复复验（2026-11-04）

按负责人要求修正虚称操作及时间精度。首轮提示过长，测试 987 通过、2 失败；压缩后两轮各对 9B/4B 运行原固定题集，最终完整后端回归 989 通过。9B 日程案例改善，4B 仍擅补周次并出现临时标记误拒。此项未通过角色质量验收，详见 [候选报告](docs/PHASE2_ACCEPTANCE.md)。

## Phase 2 人格与对话编排候选（2026-11-02 至 2026-11-03）

从独立 Phase 1 验收基线 `4fad26f` 建立功能分支。先提交审计与冻结题集，再依次提交三层人格、对话接入、评估工具和备用模型预算修复。输入及模型输出仅作为低信任数据，Canon 和批准 Persona 经 Git 审查的固定 bundle 生效。编排沿用 ChatService 和 GPU Manager，依赖锁和 OpenAPI 相等。

后端最终回归 989 项通过，前端 25 项、Next HTTP 34 项、类型检查、格式检查、构建与 wheel 人格数据导入通过。真实 9B/4B 合成评测共 24 题、38 次生成。冷启动与热启动各模型前后各测 1 次，记录为样本结果。辅助评分保留主观维度与不适用项，人工评分仍待负责人填写。完整证据与已知问题见 [Phase 2 候选报告](docs/PHASE2_ACCEPTANCE.md)。

真实 Ollama 最终回归 29 项通过。GAMING 卸载及拒绝新请求、AUTO 按需加载、180.42 秒空闲卸载、重启历史和幂等重放均有本机证据。SQLite 完整性检查通过，两次后端退出 0，最终模型列表为空。

## Codex 复审修复（2026-11-02）

用户明确授权修复并在测试通过后合并 #5/#6。使用独立 `nova-codex-reclaim-fix`、`nova-codex-policy-fix` worktree；P1 线程回归先在旧实现失败，再提交 `9d10341` 修复；P2 未知策略 15 项先全红，补拒绝与 API 回归并提交 `c4e3df6`。合并两提交后实际运行 809 passed、0 failed、0 skipped（15.84秒），没有 xfail/skip 隐藏问题。

沿原锁文件用既有 Python3.14.10 建立独立 `.venv`；uv lock/check 与 sync/locked 均0（28解析/27安装）。portable Node24.22.2/npm11.19.1 离线 `npm ci --ignore-scripts --offline` 安装227锁定包，未升级或执行远程脚本。前端25过、typecheck通过；初次lint因Windows CRLF检出失败，按原blob核实仅换行后恢复LF并补 `.gitattributes`，最终lint/build通过（4.992秒），前端源码/锁文件无内容diff。

Ruff静态与107文件格式、工程自检五PASS、Git空白检查均0。只读3次本机信号均未确认游戏；随后 `verify_automatic_live.py` 真实17项全过（启用自动检测/压力守护），模型最终为空且自有验收后端正常关闭。没有启动游戏、加载27B、训练或云端收费调用；真实游戏切换保留未验收。原始证据在 `E:\NovaAILab\logs\codex-v3-final-fix-20261102`；验收及设计见 [ACCEPTANCE](docs/ACCEPTANCE.md)、[D018](docs/decisions.md)。

## V3实验27B接入（2026-11-01）

实际项目已移到AI-Workspace/nova。开工备份123文件，git switch -c feat/qwen38-27b成功；main无提交、remote为空、Git身份未设，GitHub连接可见仓库为空，已询问用户缺失信息，未伪造提交或PR。

uv sync --locked重建唯一项目包修复搬迁路径；依赖仍28解析/27安装，无Python新增依赖。原便携Ollama0.40.13正常启动于127.0.0.1:11434，PID20532；用户PATH追加其目录并备份旧值，E盘模型目录不变。9B/4B原ID保留，驱动未改。

HF元数据和文件HEAD均HTTP200。实际执行目标ollama pull；初次包装器GBK进度输出失败，仅结束自有遗留pull客户端13396，服务与缓存保留；官方pull API诊断读超时记录保留。后续客户端直接写UTF-8日志续传，18:52约12%/1.6GB，用户选择后台下载先交付代码；未声称下载或本地摘要校验完成。

原注册表/客户端/状态机扩展实验配置、RAM准入、运行参数与stats；旧SSE保留，新增stream=false。初次全量651通过，补大小写绕过回归后最终658通过、0失败、0跳过(11.88s)；前端17通过(217.7676ms)，Ruff静态/格式98文件通过，工程自检五PASS。旧三模型计数断言有意改为四项且实验默认不可用；此前子任务初跑127过1败由此解决。旧run_web.py一处格式修正无行为变更。

收尾文档计数补写时曾因Windows默认CRLF造成3项文本自检失败，改为显式UTF-8/LF后五组PASS、0问题，Ruff静态/98文件格式再次通过；未把失败检查写成通过。

真实资源+ASGI安全14项通过，未mock系统RAM/GPU/Ollama；27B被409拒绝，GAMING/wake423、健康200、无加载和数据库正常关闭。总RAM15477MiB/可用2058MiB不足，不执行27B生成或性能测试。全部证据在E:\NovaAILab\logs\nova-v3-20261101；详情见[部署记录](docs/qwen38-deployment.md)。

## CS2 实机补充验收（2026-11-01，13:14–13:30）

用户自行启动、切换并退出游戏。新增`harness/verify_running_game.py`，使用独立后端、端口、DB/状态和随机进程内令牌；仅做只读观察及受控API验收，不控制游戏、不强杀训练、不改真实配置。无新增依赖或业务模块变更。

首次controlled-1因工具在卸载复核完成前固定检查结果而失败，报告原样保留；工具等待复核后再检查。controlled-2通过门禁和失焦保持。最终controlled-3共54样本，38次确认CS2，12项独立复核通过、0失败；生成/wake423、健康200、退出缺席61.215秒恢复AUTO不预载、后续请求/卸载清理成功。未记录最小化标志、未做带载游戏触发卸载/FPS或红色沙漠实机验收，不写为全部完成。

证据：`E:\NovaAILab\logs\cs2-live-20261101-051331-035`；原始`controlled-3/report.json`与新增`reviewed-summary.json`分开保留。实际命令和工具报告修正见 [CS2实机验收](docs/cs2-live-acceptance.md)。

本次实际执行`.venv\Scripts\ruff.exe check --no-cache .`全部通过，`ruff format --check --no-cache .`为76文件无需格式化；`python -B harness\verify_running_game.py --help`正常，`python -B harness\check_project.py`五组PASS/0问题，`git diff --check`退出0。严格条件复核38个自动确认样本及1个安全恢复样本成立，当前自有验收进程0、Ollama模型0。业务模块未改，不重复运行或声称重跑此前411项pytest。当前Git尚无提交，diff检查不代表覆盖未跟踪文件，工程自检另检查文本/链接。

## 无白名单自动检测（2026-11-01，实机游戏仍待执行）

保存63文件快照，先研究用户指定五项目README、许可与相关实现，报告最小方案后独立编码。没有复制GPL/许可不完整实现，源码版本与处理记录在三份研究文档。未安装参考项目、Python依赖、ETW服务或驱动。

新增自动目录、原生Windows窗口/进程、PDH/DXGI、多信号分类和压力策略，复用原门禁/卸载；old名单仍兼容。索引真实发现Steam8/注册3/工具2，Epic未装。用户选择自行启动两款游戏后通知，不自动启动或下载。

真实只读6样本和新模式开启后的17项Ollama回合通过。原窗口/模型控制、鉴权、重启和按需恢复正常。基准冷281.677ms、热中位66.463ms；25.812s CPU0.75s（单核2.906%），工作集26.48→35.43MiB。测量不等于游戏FPS结果。

初次整合394项中393过1败：新配置提示漏旧变量名，已补回而未修改旧断言。增加压力/共享采样及API整合后，最终411通过、0失败、0跳过（5.77s），Ruff、自检、锁同步与锁校验均0，无依赖变化。

证据根目录：`E:\NovaAILab\logs\gpu-auto-detection-20261101-043203-813`。真实游戏与真实压力/训练测试未执行，不标为全部完成；详见 [自动识别说明](docs/automatic-game-detection.md)。

## 游戏进程检测（2026-11-01，模拟验收通过）

首轮通过后按用户要求推进下一项，保存55个文件快照。无新依赖，使用Windows官方tasklist固定CSV只读命令（5秒超时），仅处理进程名/PID；真实采集可用，不输出完整进程列表。名单按用户配置的EXE名规范化匹配，不用GPU利用率判断游戏。

新增检测循环、配置、main生命周期组装及自动/手动持久化来源。进程匹配或扫描失败时关闭入口，进入游戏保护；连续成功缺席60秒才释放自动来源，不清手动锁/故障、不预载。初次定向回归13过1败，暴露重启首扫过早清保护的问题；首次poll读取保存标志并序列化重叠扫描后16项检测/hold测试全过。

全量`uv run --locked pytest -ra`实际266通过、0失败、0跳过，4.98秒；Ruff静态/格式均0（55文件）。工程自检与锁定同步/锁校验均0，pyproject和uv.lock未变。新增72项涵盖reader、来源、配置、API及退出竞态。

实际运行`harness/verify_game_detection_live.py`：15项通过。唯一模拟EXE由本地系统cmd复制，等待stdin，不是猜测游戏进程；启动即被真实tasklist命中后自动卸载，推理/wake423，AUTO409，健康200。正常退出后实际确认缺席63.237秒恢复AUTO，列表仍为空；后续推理和最终显式卸载成功。无强杀，无训练或收费API。

证据根目录：`E:\NovaAILab\logs\gpu-game-detection-20261101-040957-872`。用户真实游戏EXE未提供，保持空名单与关闭默认；开发/模拟验收完成不等于真实游戏配置已启用。说明见 [游戏检测说明](docs/game-detection.md)。

## GPU 管理第一轮（2026-11-01，已验收）

已按用户当前任务审计现有Phase1，确认没有聊天入口或训练调度，最小新增GPU模块、受保护管理端点及诊断推理入口。无新依赖、无数据库迁移、不改驱动。真实显卡16GB、内存仍16GB。

实现持久化GAMING和门禁、排空及Ollama卸载复核、显存和计算进程采样、CLI、故障安全恢复。审查复现了排队AUTO覆盖GAMING和控制任务未结束就释放租约的竞态，已修复并以Event控制时序回归。另验证审计/状态目录写入失败保持锁定，GPU退出异常不阻止数据库释放。

实际`uv run --locked pytest -ra`：194通过、0失败、0跳过，4.25秒；Ruff静态/格式、锁定同步与锁校验均退出0。现有环境27项安装记录、锁解析28项，未变更pyproject/uv.lock。初次全量190通过是在补充4项故障测试前的中间结果，不替代最终结果。

`harness/verify_gpu_live.py`首跑成功，随后补齐显式wake正向检查，最终新目录`live-with-cli`实测17项全部通过：模型加载、中文回复、GAMING卸载、生成/wake拒绝、重复控制、健康持续、重启保留模式、AUTO不预载和按需重载。显存一次采样5708→1927MiB，配合模型清单为空及runner进程消失确认。测试的临时令牌只存在进程环境，不写源码/日志；测试后端通过正常退出关闭。

证据根目录：`E:\NovaAILab\logs\gpu-round1-20261101-034738-331`。当前仅首轮完成；自动游戏检测、独立空闲监测、训练安全暂停和vLLM后续另行推进。完整接口和风险见 [GPU管理说明](docs/gpu-resource-manager.md)。

## Phase 1：后端基础架构（已验收）

用户在只读检查和实施方案后回复“确认”。开始前发现 `docs/ACCEPTANCE.md`、`harness/context/CURRENT_STATE.md` 和本文件均不存在，本阶段新建。

### 已执行

- 保存 19 个 Phase 0 交付文件快照，目录为 `C:\Users\Administrator\AppData\Local\Temp\nova-phase1-baseline-38eac6541b2d461cafbcde47880dca21`。
- 定向更新工程规则，保留 Phase 0 历史，不修改父目录或用户数据。
- 新增后端与配置依赖，使用 uv_build 可编辑安装；主执行者已实际完成锁定和同步。
- 直接依赖解析为 FastAPI 0.162.0、Starlette 1.10.0、Uvicorn 0.54.0、Pydantic 2.14.1、Pydantic Settings 2.16.1；原 pytest 9.1.1 和 Ruff 0.17.3 未升级。
- 最初 HTTPX 0.28.1 与新版 TestClient 组合出现弃用提示；核实官方支持后调整为 HTTPX2 2.16.0（httpcore2 2.16.0），不屏蔽警告；uv 同步移除了不再需要的旧 HTTPX/httpcore/certifi，未触及 Phase 0 工具版本。
- 主执行者中间验证 `uv run --locked pytest -ra` 实际为 86 passed、0 failed、0 skipped、0 warnings（1.43 秒）；这是新增 CLI 测试前的结果，不代替最终验收。

### 最终验证（2026-10-27 23:36:13 +08:00）

| 实际命令 / 操作 | 结果 |
| --- | --- |
| uv run --locked pytest -ra | 退出 0；94 passed、0 failed、0 skipped、0 warnings，2.08 秒 |
| uv lock --check | 退出 0；28 项解析记录 |
| uv sync --locked | 退出 0；27 项安装记录含项目 |
| uv run --locked ruff check . | 退出 0 |
| uv run --locked ruff format --check . | 退出 0；35 个文件无需格式化 |
| uv run --locked python harness/check_project.py | 退出 0；五组 PASS、0 个问题 |
| git diff --check | 退出 0 |
| uv run --locked python -m nova --init-db | 退出 0；临时库初始化完成 |
| uv run --locked python -m nova 与真实本地请求 | 127.0.0.1:56352，健康 200、响应符合约定；404/405 与请求标识有效 |
| SQLite 实际查询 | application_id=1313822273、user_version=1、tables=[]、integrity_check=ok |
| Ctrl+C 停止 | 生命周期和 Uvicorn 完成日志可见，进程消失、端口关闭；外层 PTY 中断返回 1，未记为正常退出 0 |
| Phase 0 快照差异 | 19→42 文件，23 新增、15 修改、4 未改、0 删除；Git diff 与 cached stat 为空是全部未跟踪导致 |

日志安全审查发现 Basic 认证与带空格口令匹配缺口后已修复，两项定向回归通过；最终 94 项包含真实 loopback 服务器异常安全与关闭验证。未通过屏蔽警告获得成功结果。

证据保存在 `C:\Users\Administrator\AppData\Local\Temp\nova-phase1-acceptance-cd652a5e5d804f65a123939115b6cbc2`。完整结果和限制见 [阶段验收](docs/ACCEPTANCE.md)。验收文档收尾后仅复查文档，不因文档更改重复业务实现。

### 阶段边界

本阶段无必需遗留；未验证第二台 Windows、Linux 或 VPS，未交付公网鉴权或生产服务。未调用 DeepSeek、未实现聊天、前端、长期记忆或业务表，未自动提交、推送或部署。所有关键项已通过，停止于 Phase 1，不自动执行 Phase 2。
