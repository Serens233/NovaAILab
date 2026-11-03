# Nova 当前状态

## 角色答复复验（2026-11-04）

安全提示调整后 989 项自动测试通过，两款模型各重跑 12 个固定案例。9B 的日程答复改善；4B 仍有周次擅补、误拒临时标记和无依据声称知道其他会话的问题。角色质量未通过验收，完整证据见 [Phase 2 候选报告](../../docs/PHASE2_ACCEPTANCE.md)。

## 当前 Phase 2 候选交付（2026-11-03）

Phase 2 独立分支 `feat/v3-phase2-persona` 基于 `4fad26f`。负责人已确认最小 Canon。已加入受校验的 Canon、批准 Persona、会话临时状态、纯上下文编排和内部成长提案验证。原有 GPU 准入与卸载规则、数据库和 13 条 API 路径保留。

实际回归为后端 989 项（818 既有、171 新增）、前端 25 项、真实 Next 加替身上游 34 项和真实 Ollama 29 项通过。空闲卸载实测 180.42 秒。固定合成评测为 9B 和 4B 各 12 题、各 19 次流式生成，已记录可复验的性能及角色质量差异。原始输出留在本机证据目录。人工验收、真实游戏切换和 27B 推理仍待执行。

代码、范围和验收细节见 [Phase 2 候选报告](../../docs/PHASE2_ACCEPTANCE.md)。Phase 1 基线在 [草稿 PR #7](https://github.com/Serens233/NovaAILab/pull/7)。以下为历史证据。

## 当前：Codex 复审修复与条件合并（2026-11-02）

用户已授权修复后测试通过合并 PR #5/#6 至 `integration/v3-phase1`。独立 worktree 的两项生产修复为：关闭时保留执行中补偿卸载的线程跟踪与实例锁；所有本地加载入口默认拒绝未知资源策略。稳定 9B/4B、受控 27B 的原资源门槛保持；无依赖升级或 DB schema 变更。设计见 [D018](../../docs/decisions.md)。

实际集成验收：809 passed、0 failed、0 skipped；前端 25 项、typecheck/lint/build、uv 锁定同步、Ruff/107 文件格式与工程自检通过。开启自动检测/压力守护的真实 Ollama 17 项通过，最终模型为空、验收后端已退出；没有启动真实游戏、训练或付费云端。硬件当前已升级为约32GB级系统内存，未加载27B。完整命令及剩余真实游戏验收见 [阶段验收](../../docs/ACCEPTANCE.md)；GitHub 为 PR 合并状态的权威来源。

本轮停止在复审修复，不自动进入 Phase 2。以下均为历史状态，旧目录、下载进度及 Git 状态不代表当前。

## 当前：V3实验模型已接入，后台下载与真实推理未完成

2026-11-01。实际仓库移至AI-Workspace/nova；uv已重新同步项目可编辑路径。备份123文件后切到feat/qwen38-27b，未改main。原Ollama0.40.13启动并修复用户PATH，E盘模型目录与9B/4B保留。

新增可选Qwen3.8-27B配置、系统RAM准入和运行参数，默认关闭，9B默认不变；原SSE保留，新增stream=false。全量658通过、前端17通过、真实RAM/GPU/Ollama+ASGI安全检查14通过。16GB实际内存不足，未加载27B，性能未测。

用户选择后台续传并先交付代码；18:52原生CLI快照约12%/1.6GB，未校验完成。Git无remote/身份且连接仓库为空，用户URL/姓名/邮箱待补，尚无Commit/PR。详细配置、日志、命令和待验收见[27B部署记录](../../docs/qwen38-deployment.md)。V2代码及此前536/17项与真实模型证据保留，不把旧状态文档误作V2未实现。以下历史未追溯改写。

## 最新补充：CS2 实机核心链路通过（2026-11-01）

用户自行启动并退出CS2。最终会话54样本、38样本确认GameConfigStore路径+渲染+持续3D；自动GAMING、生成/wake423、健康200及切回桌面保持保护通过。连续缺席61.215秒恢复AUTO且不预载，随后按需推理与清理成功。独立复核12项通过、0失败，Ollama最终为空，验收后端已正常关闭；未修改真实配置或启用常驻保护。

实际最小化标志未观察到、游戏启动前模型已空、FPS未测，红色沙漠仍待实机。因此不标为全部完成。早期工具过渡状态采样导致失败的原报告保留，工具已收紧检查。详细命令、证据和限制见 [CS2实机记录](../../docs/cs2-live-acceptance.md)。以下“待执行”为本补充前历史。

## 最新：无白名单升级（2026-11-01，游戏实机验收待执行）

已在原GPU Manager上实现自动索引、Win32前台/几何/渲染模块、PDH进程3D/Compute/视频与显存、DXGI完整LUID映射、连续分类及独立压力保护。默认安全关闭，通过`NOVA_GPU_AUTO_GAME_ENABLED`启用时不要求名单；旧模式保留。新压力开关与游戏开关独立，不强杀训练/其他应用。

实际411 passed、0 failed、0 skipped（5.77秒）；新模式开启的17项真实Ollama/CLI回合通过。只读6样本：冷281.677ms、热中位66.463ms，25.812秒CPU0.75秒（单核2.906%），工作集约26.48→35.43MiB。不是FPS测试或完整后端性能保证。

本机索引GameConfigStore3、Steam8、工具2、Epic0；已发现目标两游戏安装元数据，未启动游戏。用户选择自行启动后通知。两游戏运行、切屏/最小化、退出与FPS验收仍待执行，不能将当前结果写成全部验收通过。证据根目录`E:\NovaAILab\logs\gpu-auto-detection-20261101-043203-813`。

许可/算法/当前限制与后续操作见 [自动识别说明](../../docs/automatic-game-detection.md)。本轮没有新依赖/驱动修改/ETW服务/收费API，所有实测最终卸载模型并正常关闭验收后端；外部11434绕过边界仍在。以下为历史记录。

## 旧版：可选游戏检测（2026-11-01）

首轮验收通过后，用户明确授权下一项游戏检测。已增加只读Windows进程采集、白名单、5–10秒循环、连续成功缺席60秒恢复AUTO（不加载）、手动和自动GAMING来源持久化；首扫/异常期间保护，迟到和重叠扫描有回归。

实际全量266 passed、0 failed、0 skipped（4.98秒），静态和格式通过。真实Windows自有模拟进程+Ollama15项通过；实际退出确认约63秒，恢复时模型仍为空。全部验收使用独立状态/DB，正常结束自有模拟和验收后端，模型最终卸载。

真实游戏EXE尚未提供，`NOVA_GPU_GAME_DETECTION_ENABLED=false`、`NOVA_GPU_GAME_PROCESSES=[]`为默认值，未修改用户真实.env。不要猜CS2或其他游戏EXE来启用。不自动扩展空闲调度/训练/vLLM。配置和保护边界见 [游戏检测说明](../../docs/game-detection.md)。以下为首轮和Phase1历史。

## GPU 管理第一轮（2026-11-01，已完成）

以下为已完成的 GPU Resource Manager 第一轮记录；下文 Phase 1 是保留的后端基础验收历史。

已在原 FastAPI 模块化接入 `gpu/`、四个管理接口和一个受控诊断推理接口；无新增依赖、无数据库 schema 修改。实际全量 194 passed、0 failed、0 skipped；真实 Ollama/Qwen 验收 17 项通过。GAMING 先锁定入口，卸载并轮询 `/api/ps`；失败维持锁定，跨重启保留状态；回 AUTO 不预载。

实际显卡为 16311MiB（16GB），内存仍为单条16GB。显存一次卸载采样5708→1927MiB，结合 `/api/ps=[]` 和 llama-server.exe 退出确认，非固定显存阈值。

默认 `NOVA_GPU_ENABLED=false`；启用需要随机管理令牌、回环地址、单 worker。实际验收使用独立临时数据库/状态目录，未改用户数据，结束时卸载模型并正常退出验收后端，未设置自启。

外部直连 Ollama 可绕过门禁；原 Lab 直接推理脚本也在此边界。训练状态为 unmanaged，不能理解成训练已暂停。自动检测、独立空闲清理、AI_FOCUS、训练安全暂停和 vLLM 尚未实现。下一轮另行推进，详细命令见 [GPU 管理说明](../../docs/gpu-resource-manager.md)。

## 后端 Phase 1 历史

- 当前阶段：Phase 1 后端基础架构。
- 状态：已完成并通过本机验收（2026-10-27，Asia/Shanghai），停止于 Phase 1。
- 授权：用户已确认只读检查后的实施方案；不授权自动开始 Phase 2。
- 项目根目录：`C:\Users\Administrator\Documents\Codex\nova`。
- Phase 0 已完成；历史结果见 [进度记录](../../docs/progress.md)。

## 当前实现范围

已交付 FastAPI 工厂与 lifespan、集中路由和 `/health`、环境与 `.env` 配置、JSON 日志、统一错误、sqlite3 基础和 pytest。实际为 94 passed、0 failed、0 skipped、0 warnings；锁文件、同步、静态、格式、自检均通过。具体证据见 [实施计划](../../PLANS.md) 与 [验收文档](../../docs/ACCEPTANCE.md)。

## 已确定决策

保留 Python 3.14.10、uv 0.13.6、pytest 9.1.1、Ruff 0.17.3。使用 uv_build 的可编辑 src 应用包；FastAPI/Uvicorn/Pydantic Settings，TestClient 使用 HTTPX2；sqlite3 无 ORM、无业务表。完整理由见 [技术决策](../../docs/decisions.md)。

## 工作前注意

1. 阅读根 AGENTS、GOALS、PLANS、架构和本文件。
2. 检查 Git 状态；当前尚无提交，全部工程文件未跟踪。
3. 不读取或打印开发者 `.env`、密钥、完整环境和用户数据。
4. 使用临时资源测试；不调用 DeepSeek、不开发聊天、前端、长期记忆或业务表。
5. 未执行的命令不得写成成功，实际结果集中写入验收与 [build-log](../../build-log.md)。

## 验收与限制

真实本地 `/health` 返回 200；临时库 Nova application_id、版本 1、无业务表且完整性为 ok；Ctrl+C 后生命周期退出、进程消失、端口关闭。外层 PTY 的中断退出码为 1，未伪称正常进程退出码 0。

Phase 0 基线 19 文件对比当前 42 文件：23 新增、15 修改、4 未改、0 删除。原日期目录仍为空，未删除用户数据。

本阶段无必需遗留。尚未在第二台 Windows、Linux 或 VPS 验证；未提供公网鉴权或生产部署，不属于公网生产服务。Git 未提交或推送。

## 下一阶段建议

等待用户明确发起并重新确定最小对话切片及验收。不得根据原路线图自动接入 DeepSeek、开发 Web 或执行 Phase 2。
