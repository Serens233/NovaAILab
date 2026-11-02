# Phase 2 审计与文件级方案

2026-11-02，Asia/Shanghai。Phase 2 分支 `feat/v3-phase2-persona` 从本地 Phase 1 收尾 `4fad26f` 隔离；该提交基于远端 `integration/v3-phase1` 的 `936b56a`。未提交的21份Phase 1改动均为本会话来源，已先独立提交，没有覆盖其他用户或Agent工作区。新分支修改前工作区干净。

Git审计：没有本地main；`origin/main=2b6f37f`，只有初始README；`origin/integration/v3-phase1=936b56a`，含已远端合并的PR #5/#6。PR #1–#4仍开启，不能把集成分支当作已合并main。本地Phase 1清理提交尚未推送，Phase 2依赖它，交付PR将明确依赖，不正式合并。

## 能力矩阵（修改前）

| 能力 | 状态 | 文件/关键符号、当前行为 | 既有测试 | 问题与处理 |
| --- | --- | --- | --- | --- |
| FastAPI入口与路由 | 已实现 | `main.create_app/lifespan`；`api/router/chat/gpu/health`，13条路径 | test_app/health/chat_api/gpu_api | 复用，不新增人格管理HTTP接口 |
| Next前端与协议 | 已实现 | `frontend/components/nova-runtime`、`lib/model-adapter`、固定代理；SSE meta/delta/done/error | 前端25项、真实Next+替身HTTP34项 | 复用，不另起前端 |
| Ollama与模型策略 | 已实现 | `chat.registry.ModelRegistry`、`gpu.backend.OllamaBackend`；9B/4B、受控实验模板，流/思考/总时限 | test_router_*、test_v3_*、header_deadlines | 本阶段只用真实9B/4B和Mock，禁止27B |
| GPU与游戏/空闲调度 | 已实现 | `gpu.manager.GpuManager`；AUTO/GAMING、独立来源、取消/排空/重试/实例租约 | GPU系列，29项本机模型/历史/空闲回合 | 冻结调度/检测/卸载代码及策略 |
| 真实游戏过程验证 | 待确认 | 用户上阶段选择暂不启动，旧CS2有部分历史证据 | 仅替身/手动模式和旧实机记录 | 不宣称本轮真实游戏已验 |
| SQLite历史与幂等 | 已实现 | `storage.database.Database`、`storage.chat.ChatStore`；独立schema标识、非破坏性扩展 | test_database/chat_storage/v3_merge_contract | 复用，不改schema或私人数据 |
| System Prompt与上下文 | 部分实现 | `chat.service.SYSTEM_MESSAGE`为通用提示；最近20条完成消息/6000字符预算 | test_chat_api/lifecycle/v3_api | 增量抽取纯ConversationOrchestrator，保留ChatService执行生命周期 |
| 身份认证/授权 | 部分实现 | `api.gpu.require_gpu`，loopback+Bearer，单管理信任域 | API/proxy安全测试 | 不冒称多用户隔离；角色配置只读Git管理 |
| Character Bible/Canon | 未实现 | 全仓及Lab文档检索无正式资料；GOALS记录最小角色目标 | 无人格测试 | 用户已明确批准最小身份/风格，不新增故事事实 |
| 三层人格/成长审批 | 未实现 | 无对应运行模块、状态或管理路由 | 无 | 新增只读受校验bundle、临时会话状态、内部提案契约；无自动审批或后台反思 |
| 测试与工具 | 已实现 | CPython3.14.10、uv、pytest、Ruff、锁定前端 | Phase1收尾818项/前端25项；详见ACCEPTANCE | 本轮重新运行，新增数和失败单列 |

## 角色来源与性能基线

用户本轮明确确认：Nova／星图档案馆的夜航记录员，明确AI身份；温柔、好奇、略带俏皮、有独立观点，避免机械安慰和无原则迎合。其余出身、经历、兴趣均未知，不补造。

历史冷请求TTFT约46–52秒，Ollama加载约25–28秒；原始证据在 `E:\NovaAILab\logs\phase1-acceptance-20261102`，配对摘要见 [Phase1性能](phase1-performance-baseline.json)。没有热启动可比样本，不能由此宣称Phase2性能提升；本轮固定输入额外保存前后冷/热测量。

## 最小实施方案

| 文件范围 | 变更与边界 |
| --- | --- |
| `src/nova/persona/`、包内版本化JSON | 只读Canon/已批准Persona、来源/完整性/版本校验；TTL会话临时状态；无HTTP管理写入、无数据库迁移 |
| `src/nova/persona/proposals.py` | 未来成长提案数据契约与确定性拒绝/状态验证，不自动应用，不运行反思服务 |
| `src/nova/chat/orchestrator.py` | 纯上下文组装、来源隔离、保守token上界与输出预算，超限明确拒绝/裁剪 |
| `src/nova/chat/service.py` | 在既有上下文位置接入编排；生成、取消、持久化、模型调用仍沿原流程 |
| `tests/test_persona_*`、`test_conversation_*` | G1确定性权限/完整性/隔离、至少40固定场景、G2路由/游戏/取消回归 |
| `evals/phase2/`、`harness/` | 固定题集和真实9B/4B各至少12案例；原始输出外置、主观评分与人工验收分开 |
| 文档/配置示例 | 审计、ADR、接口、实际结果、命令和风险；无新增依赖 |

不得修改冻结的GPU调度/游戏检测/保活/卸载代码，不主动使用27B，不接入DeepSeek Agent，不正式合并远端分支，不自动开始Phase3。核心角色新事实与重大设计变动先申请。
