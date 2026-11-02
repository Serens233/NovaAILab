# V3.0 Phase 1 模型接口契约与验证记录（DS-A）

- 任务：DS-A 模型接口（Model Provider / Model Router / 相关接口与测试）
- 基准 Commit：`3bb0f6a`（tag `dsh-baseline-20261101`）
- 工作分支：`feature/dsh-model-router`
- 独立 worktree：`E:\NovaAILab\worktrees\nova-dsh-model-router`
- 远程仓库：`https://github.com/Serens233/NovaAILab`；PR #2（draft，base `baseline/v3.0-phase1`，head `feature/dsh-model-router`）
- 记录时间：2026-11-01 22:47–22:52（Asia/Shanghai）；PR #2 由主会话于 23:04 创建
- 结论口径：只记录实际执行的命令和结果；未执行、失败和超出本任务文件所有权的事项分别列出。

## 1. 本任务范围

允许修改：

- `src/nova/chat/registry.py`
- `src/nova/gpu/backend.py`、`src/nova/gpu/streaming.py`、`src/nova/gpu/protocol.py`
- `src/nova/chat/cloud.py`
- `tests/test_router_registry.py` 等 `test_router_*`、`test_gpu_chat_stream.py`、`test_chat_cloud.py`
- `docs/qwen38-deployment.md`、`docs/architecture.md`、本文件

未修改（受保护或由其他任务负责）：

- `src/nova/gpu/manager.py` 及其状态机
- `src/nova/api/**`、`src/nova/main.py`、`src/nova/config.py`、`src/nova/storage/**`、`src/nova/chat/service.py`
- `tests/test_v3_*.py`、`tests/test_chat_api.py`
- 人格、记忆、训练、前端相关目录

## 2. 27B 实际标签对齐（代码路径）

本机 Ollama 实际安装：

| 名称 | 大小 | 量化 | 创建时间 |
| --- | ---: | --- | --- |
| `qwen3.8:27b-q3_k_xl` | 14,074,001,250 B（约 13.1 GiB） | Q3_K_L / 27.3B | 2026-11-01 22:19:11 +08:00 |
| `qwen3.5:9b` | 6,550,825,550 B | Q4_K_M / 9.0B | 2026-11-01 14:10:15 +08:00 |
| `qwen3.5:4b` | 3,324,173,934 B | Q4_K_M / 4.2B | 2026-11-01 01:50:41 +08:00 |

选择**代码路径**而不是 `ollama cp`：不复制权重、不产生第二个可变模型身份，别名映射、资源策略和测试都在 Nova 代码内可复核。`_qwen38_alias()` 现在接受三类严格白名单：

1. 规范名 `qwen3.8:27b`（大小写不敏感）。
2. 本机量化族 `qwen3.8:27b-<suffix>`，suffix 只允许 `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`。
3. 公开 HF 标识 `hf.co/unsloth/Qwen3.8-27B-GGUF:<tag>`。

以下仍被拒绝：`:cloud` / `-cloud` 后缀、其他组织或近似仓库、空后缀、带 `/`、`:`、空格或连续前导 `-` 的后缀、`qwen3.8:28b-*`、`qwen3.8:27b_q3*`。任意已安装大模型仍只进入 `discovered_models`，不可选、无资源策略。

## 3. 路由与门禁顺序

`ModelRegistry.resolve()` 的顺序固定为：

1. 解析请求标识（`None` 使用 `chat_default_model`，不会静默升级到 27B）。
2. 若请求命中实验族：先检查 `NOVA_QWEN38_ENABLED`，再读取系统物理内存做 28 GiB / 18 GiB 准入。
3. 通过后才查询 `/api/tags`；失败时清空已安装快照并标记 `reachable=False`。
4. 规范别名映射到实际已安装标识（优先规范名，其次配置的 HF 标识，再按稳定排序），复制同一份 RAM/运行参数策略。
5. 未知模型返回 `MODEL_UNKNOWN`（422）；实验关闭返回 `MODEL_EXPERIMENT_DISABLED`（503）；内存不足返回 `MODEL_RAM_INSUFFICIENT`（409）；Ollama 不可用返回 `OLLAMA_UNAVAILABLE`（503）；未安装返回 `MODEL_NOT_INSTALLED`（503）。

第 2 步必须先于第 3 步：本机 16 GB 场景下不能为了判断 27B 是否存在而查询 tags、驱逐常驻 9B/4B 或加载模型。

## 4. 传输与错误语义

`OllamaBackend` 只允许 `http://127.0.0.1:<port>`，无凭据、无路径、无重定向、无代理；固定访问 `/api/tags`、`/api/ps`、`/api/chat`、`/api/generate`。

| 边界 | 实际行为 |
| --- | --- |
| 响应大小 | 普通 JSON 最多 2 MiB；NDJSON 单行最多 64 KiB、累计最多 2 MiB；超限返回固定 `BackendError`，不含上游原文 |
| 流完成 | 必须有合法 `message.role=assistant`、字符串 `content`/`thinking`、`done=true`；缺 `done`、空正文、非法 JSON、负数统计均失败 |
| 总时限 | `NOVA_GPU_REQUEST_TIMEOUT`（默认 120 秒，1–300）；每次读取都按绝对 deadline 重算，慢速分块不能续期 |
| 取消 | `StreamControl.cancel()` 设置标志并 `shutdown` 当前请求 socket；取消先于连接时不发 HTTP；取消后仍等待线程结束并由管理器复核卸载，不把断开当作显存已释放 |
| 思考内容 | Ollama 的 `message.thinking` 只参与首活动计时，不回调、不保存、不拼接；正文中的 `<think|thinking|analysis|reasoning>` 成对标签由 `AnswerFilter` 剥离；未闭合/不平衡标签失败且不泄露私有文本 |
| 云端 | 固定 `api.deepseek.com` HTTPS；`reasoning_content` 永不进入正文；只有 reasoning、缺 `[DONE]`、非法 SSE 或超时均失败；密钥/正文/上游原文不进入错误或日志 |
| 运行参数 | 只接受 `num_ctx`、`num_predict`、`keep_alive`、`temperature`、`num_gpu`、`think`；类型和范围有界，未知键拒绝 |

## 5. 实际测试记录

工作目录：`E:\NovaAILab\worktrees\nova-dsh-model-router`；解释器：`.venv\Scripts\python.exe`（CPython 3.14.10，`uv sync --locked` 创建）。

记录中的 `python -B -m pytest/ruff` 是本次实际执行方式；与项目标准入口 `uv run pytest` / `uv run ruff` 等价，不改变结果。

| 命令 | 实际结果 |
| --- | --- |
| `python -B -m pytest -q tests/test_router_registry.py` | **51 passed**，0 failed |
| `python -B -m pytest -q tests/test_router_registry.py tests/test_router_transport.py tests/test_router_cloud.py tests/test_router_scheduler.py tests/test_gpu_chat_stream.py tests/test_chat_cloud.py` | **156 passed**，0 failed，6.96s |
| `python -B -m ruff check src/nova/chat/registry.py tests/test_router_registry.py` | 退出码 0，`All checks passed!` |
| `python -B -m ruff format --check src/nova/chat/registry.py tests/test_router_registry.py` | 退出码 0，`2 files already formatted` |
| `python -B -m ruff check .` | 退出码 0，全仓静态检查通过 |
| `python -B -m ruff format --check .` | 退出码 1：4 个基线文件待格式化（`api/chat.py`、`chat/service.py`、`gpu/manager.py`、`gpu/protocol.py`）；前 3 个在本任务禁止修改范围，本任务改动文件均通过。留给 G0 或对应文件所有者处理 |
| `python harness/check_project.py`（PATH 加入 Git） | 5 组 PASS、0 问题，退出码 0；未加 PATH 时因 `git` 不在 PATH 会报 2 个无法完成项，属环境问题 |
| `uv lock --check` | 退出码 0，`Resolved 28 packages` |
| `python -B -m pytest -ra`（全量） | **757 passed、4 failed、0 skipped**，13.74s（重复运行 9.03–14.52s，机器负载波动）；4 个失败均在本任务文件所有权之外，见 §6 |

说明：本机 `TEMP`/`TMP` 的 8.3 短路径不存在时，Python `tempfile.gettempdir()` 会回退到当前目录，测试会在 worktree 根生成 `nova-gpu-*.lock` 与 `pytest-of-*`。最终全量回归显式把 `TEMP`/`TMP` 指向 `C:\Users\Administrator\AppData\Local\Temp`；这些产物不属于源码，已清理并仅在本地 `.git/info/exclude` 排除，未修改仓库 `.gitignore`。

新增/加强的自动化断言：

- 规范名、HF 标识、本机量化标签均解析到实际安装标识；直接请求本机标签保留 `requested_model` 与同一策略。
- 本机量化标签出现在实验条目而非 `discovered_models`，`local_policies()` 含实际标签。
- 16 GB 真实门禁：实际标签 `installed=true`、`available=false`、原因含内存不足；`resolve()` 在查询 tags 前返回 `MODEL_RAM_INSUFFICIENT`。
- 实验关闭时同样在查询 tags 前返回 `MODEL_EXPERIMENT_DISABLED`。
- 严格后缀白名单：空后缀、冒号、斜杠、空格、双前导 `-`、cloud、28B、下划线族均不可选。
- 4097 个标签的超量目录失败关闭，不保留部分快照。
- `/api/tags` 畸形/超量、`/api/chat` 非法 JSON/缺 done/空正文/超大单行、think 嵌套与未闭合、取消前后、总时限、云端仅 reasoning、错误与日志脱敏均沿用并通过既有 `test_router_transport.py`、`test_gpu_chat_stream.py`、`test_router_cloud.py` 用例。

### 5.1 真实 Ollama 只读验证

证据目录：`E:\NovaAILab\logs\nova-v30-ds-a-20261101-2251\`（`real-ollama-contract.json`、`real-ollama-contract.log`）。执行时持有 `E:\NovaAILab\worktrees\.gpu.lock`，前后 `/api/ps` 均为空；只调用 `/api/tags` 和内存读取，没有加载模型。

实际结果：

- `/api/tags` 返回 `qwen3.5:4b`、`qwen3.5:9b`、`qwen3.8:27b-q3_k_xl`。
- `ModelRegistry.list()` 将 `qwen3.8:27b-q3_k_xl` 标为 `experimental=true`、`installed=true`、`available=false`，原因“系统内存不足以安全加载27B”。
- 真实内存（总 15,477 MiB）下 `resolve("qwen3.8:27b")` 返回 `MODEL_RAM_INSUFFICIENT`（409），未查询 tags、未加载模型。
- 仅把内存读取替换为 32 GiB 的测试替身后，`resolve("qwen3.8:27b")` 映射到 `qwen3.8:27b-q3_k_xl`，策略为 `required_mib=4096`、`num_ctx=4096`、`num_predict=256`、`keep_alive=180`、`temperature=0.7`。该替身只验证映射，不代表 27B 已真实加载。

## 6. 未完成项与接口变更请求（不自行扩大范围）

> 2026-11-02 合并阻断修复（`fix/v3-chat-replay-contract`）：第 1 项已通过只读 `get_completed_run` 在资源解析前重放；第 2 项按统一契约保留 `MODEL_NOT_INSTALLED`（503）并同步服务端、测试和文档。以下为当时的交接记录，保留不改写。

以下 4 个全量失败均需要修改本任务禁止的文件；本任务只报告，不代改：

1. `tests/test_v3_api.py::test_saved_experiment_reply_replays_without_reloading_under_low_ram`
   - 现象：已完成回复的幂等重放，在内存降低后返回 409，期望 200 且不重新推理。
   - 原因：`ChatService._prepare()` 先 `_resolve()` 再 `begin_run()`；重放判断在资源解析之后。
   - 建议接口变更：在 `ChatService`/`ChatStore` 增加只读的已完成 run 查询（例如 `find_completed_run(thread_id, request_id, model, content)`），先返回既有回复，再对非重放请求解析模型；不新增表、不改 schema。
2. `tests/test_v3_api.py::test_missing_experiment_rejects_without_evicting_stable_model[False|True]`（2 个参数）
   - 现象：27B 未安装时返回 `MODEL_NOT_INSTALLED`，旧集成测试期望 `GPU_PREFLIGHT_FAILED`。
   - 原因：新注册表在进入 GPU 管理器前完成安装检查；`test_router_registry.py` 已明确期望 `MODEL_NOT_INSTALLED`。
   - 建议：由 API/服务层决定是否把 `MODEL_NOT_INSTALLED` 映射为对外兼容码，或由集成测试负责人更新契约；不要在两个测试间二选一硬改注册表。
3. `tests/test_gpu_api.py::test_gaming_blocks_generation_and_wake_but_keeps_database_healthy`
   - 现象：`training.state` 实际为 `idle`，旧测试与旧文档写 `unmanaged`。
   - 裁定（2026-11-02，主会话）：对外保留 `idle` 表示“当前没有登记训练租约”；`unmanaged` 是引入训练租约前的旧值，不能描述 `preparing`/`reserved`/`unknown_after_restart` 状态。由 B 分支同步旧集成测试与 `docs/*`；本任务不改 GPU Manager 状态机。
4. 真实 27B 推理、中文质量、首 token、tokens/s、加载/卸载仍未执行：本机 15.11 GiB < 28 GiB 门禁，按设计拒绝；不得用 9B/4B 数据代替。

## 7. 安全与依赖

- 未新增依赖，未修改 `pyproject.toml`/`uv.lock`，未访问收费 API。
- 未读取或输出 `.env`、真实密钥、用户聊天内容；真实 Ollama 验证只读取 tags/ps 和模型元数据。
- 27B 仍默认关闭、仍受 28 GiB 总内存 / 18 GiB 可用内存 / 显存 / 游戏与训练门禁；本次没有加载或卸载 27B。
