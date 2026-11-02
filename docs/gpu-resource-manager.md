# GPU Resource Manager：第一轮

最新升级：已提供无需手工名单的自动索引/多信号分类和独立显存压力保护；原手动模式与接口保留，见 [自动识别说明](automatic-game-detection.md)。代码回归通过，真实游戏验收待用户启动；以下为首轮历史。

后续进展（2026-11-01）：可选的游戏白名单检测和退出延迟已完成开发/模拟验收，详见 [游戏检测说明](game-detection.md)。下文保留首轮实现与验收；真实游戏EXE尚待提供，检测默认关闭。

本轮把 GPU 模型调度接入现有 FastAPI 后端，只覆盖五项：Ollama 状态查询、手动游戏模式、模型卸载与恢复、显存监控、自动化测试。这里的“恢复”是重新允许按需推理，不是自动开始训练。

当前 Nova 还没有完整聊天界面和会话业务。本轮新增 `/api/gpu/generate` 作为受控推理诊断入口，用来验证模式门禁；它不创建聊天记录、记忆或 Agent 流程。现有健康检查、SQLite 和后端服务继续使用原有实现。

**本轮已于 2026-11-01 完成本机验收**：全量194项离线测试通过、0失败、0跳过；17项真实Ollama/接口检查通过。显卡实测16311MiB，内存仍为单条16GB。实际命令和结果记录在 [构建记录](../build-log.md) 与 [阶段验收](ACCEPTANCE.md)。

## 模块怎样协作

请求依次经过 HTTP 权限检查、`GpuManager` 请求门禁、`OllamaBackend`，最后到本机 Ollama。显存监控单独查询 `nvidia-smi`，不把“清空 Python 变量”当作卸载模型。

| 模块 | 负责什么 |
| --- | --- |
| `src/nova/api/gpu.py` | 校验本机来源、Bearer 令牌和输入，沿用统一错误响应 |
| `src/nova/gpu/manager.py` | 管理 AUTO/GAMING、正在运行的请求、卸载顺序、故障锁定与模式状态 |
| `src/nova/gpu/backend.py` | 调用 Ollama 官方 API，查询 GPU 和计算进程，不下载模型、不调用云端 |
| `harness/gpu_cli.py` | 通过标准库 HTTP 客户端调用受保护接口，供本机命令行验证 |

后端适配器提供 `unload_model()`、`sleep_model()`、`wake_model()`、`get_model_status()`、`get_gpu_memory_usage()`。当前 `sleep_model()` 使用 Ollama 卸载；没有假设 Ollama 具备 vLLM 的原生 Sleep Mode。

不新增 Python 依赖，不安装 psutil 或 vLLM，不修改显卡驱动，也不更改现有数据库结构。

## 两种已实现模式

### AUTO

AUTO 允许新的受控推理请求进入。切回 AUTO 本身不加载模型；后续诊断推理或显式 `/wake` 才会触发加载。

一次只接纳一个受管加载/推理请求。已有请求运行时，新请求返回 `GPU_BUSY`；如果发现另一个模型驻留，返回 `GPU_MODEL_CONFLICT`，不在普通推理请求中擅自驱逐其他模型。

每次受控请求向 Ollama 显式传入 `keep_alive=180`，即 180 秒驻留参数。**这只是请求参数，本轮没有实现独立的三分钟空闲监测任务，也没有完成三分钟自动休眠验收。** 其他客户端可使用不同的驻留参数，不能据此声称整台电脑已具备自动休眠策略。

### GAMING

手动进入游戏模式按以下顺序执行：

1. 先保存 GAMING 状态并关闭新的受控推理/唤醒入口。
2. 等待已经接纳的请求完成，默认最多等待 30 秒。
3. 查询 `/api/ps`，枚举当前驻留模型。
4. 对每个模型向 `/api/generate` 发送空 prompt 与 `keep_alive=0`。
5. 再查询 `/api/ps`，确认没有驻留模型，保存卸载时间和显存前后采样。

状态查询最多等待 5 秒，单模型卸载请求最多等待 10 秒，卸载后还有有界状态复核。等待已有推理超时、Ollama 不可用或复核失败时，不宣称卸载成功，入口继续锁定；不会强行结束 Python、Ollama 或训练进程。

排空超时不会只试一次就放弃：管理器登记一个显式补偿任务，等待已接纳推理真正结束后，再通过原有卸载流程重试并复核 `/api/ps`；失败时按退避重试，避免频繁冲击 Ollama 或状态文件。补偿不会清除手动 GAMING/游戏检测来源，也不会自动切回 AUTO；空闲调度因故障锁不会接管这条恢复路径。

GAMING 期间的 `/generate` 和 `/wake` 返回 `GPU_GAMING`，不会调用 Ollama 加载模型。DeepSeek 回退保持关闭，即使已有 API Key 也不会因此触发收费调用。

重复进入 GAMING 会再次检查和卸载，不会先恢复模型。`POST /unload` 也会保守地进入 GAMING：它不是“卸载后立即允许其他请求重新加载”的快捷命令。

## 启用与访问权限

默认 `NOVA_GPU_ENABLED=false`，不会因导入模块就联网或加载模型。启用时要求后端仅监听本机，并提供至少 32 字符的随机令牌。

| 环境变量 | 默认值 / 用途 |
| --- | --- |
| `NOVA_GPU_ENABLED` | `false`，显式启用管理模块 |
| `NOVA_GPU_TOKEN` | 空；启用时必须提供至少 32 字符随机令牌 |
| `NOVA_GPU_OLLAMA_URL` | `http://127.0.0.1:11434`，只允许带端口的本机 HTTP 服务根地址 |
| `NOVA_GPU_MODEL` | `qwen3.5:4b`，必须是本机已安装模型 |
| `NOVA_GPU_DRAIN_TIMEOUT` | `30` 秒，允许配置 0.1–120 秒 |
| `NOVA_GPU_REQUEST_TIMEOUT` | `120` 秒，允许配置 1–300 秒，用于加载/生成 |

下面的 PowerShell 代码把随机令牌直接保存到当前进程环境，**不在屏幕显示令牌**。不要把它改成明文常量，不把环境内容、认证头或真实令牌写入聊天、日志或 Git。

```powershell
Set-Location 'C:\Users\Administrator\Documents\Codex\nova'
$env:NOVA_GPU_ENABLED = 'true'
$env:NOVA_GPU_TOKEN = & '.\.venv\Scripts\python.exe' -c 'import secrets; print(secrets.token_urlsafe(32))'
$env:NOVA_GPU_OLLAMA_URL = 'http://127.0.0.1:11434'
$env:NOVA_GPU_MODEL = 'qwen3.5:4b'
```

`token_urlsafe(32)` 使用 32 个随机字节，编码后的令牌超过 32 字符。环境变量只影响当前终端及其随后启动的子进程，不会修改全局 Windows 环境。

后端和控制命令必须使用**同一个令牌**。不要在两个独立终端分别生成令牌，否则认证会失败。已有后端应按原方式正常停止后，带新配置重新启动；不要并行启动第二个调度实例。

用于本机试用时，可在上述同一 PowerShell 中把后端作为隐藏子进程启动，然后继续使用当前环境中的令牌调用 CLI：

```powershell
New-Item -ItemType Directory -Path '.\logs' -Force | Out-Null
$taskStamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$taskServer = Start-Process -FilePath (Resolve-Path '.\.venv\Scripts\python.exe').Path `
    -ArgumentList @('-m', 'nova') -WorkingDirectory (Get-Location).Path `
    -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput ".\logs\gpu-$taskStamp.stdout.log" `
    -RedirectStandardError ".\logs\gpu-$taskStamp.stderr.log"
.\.venv\Scripts\python.exe harness\gpu_cli.py --help
```

这不是 Windows 服务或开机自启配置。GPU 卸载过程中后端继续运行；不需要关闭后端来释放模型显存。上面的进程启动方式不用于暂停训练任务。

所有 GPU 接口都要求本机客户端和 `Authorization: Bearer <令牌>`。浏览器带 `Origin` 的请求被拒绝，本轮只面向本机 CLI；无令牌返回 401，非本机或浏览器来源返回 403，未启用模块返回 503。不接受查询参数中的令牌，不配置公开管理端点。

## HTTP 接口与命令行顺序

| 方法与路径 | 请求体 | 行为 |
| --- | --- | --- |
| `GET /api/gpu/status` | 无 | 查询模式、门禁、模型状态、GPU 采样和最近操作 |
| `POST /api/gpu/mode` | `{"mode":"GAMING"}` 或 `{"mode":"AUTO"}` | 手动切换；GAMING 会排空并卸载，AUTO 不加载模型 |
| `POST /api/gpu/unload` | 无 | 保守进入 GAMING 并显式卸载复核 |
| `POST /api/gpu/wake` | 无 | AUTO 下预加载配置中的模型；GAMING 下拒绝 |
| `POST /api/gpu/generate` | `{"prompt":"请用中文回答你好"}` | 诊断推理，经同一门禁，不是完整聊天业务 |

新手可按“状态 → 游戏模式 → 尝试推理被拒绝 → 状态仍无模型 → AUTO → 推理成功”的顺序验证。在后端已经就绪、令牌相同的当前终端执行：

```powershell
.\.venv\Scripts\python.exe harness\gpu_cli.py status
.\.venv\Scripts\python.exe harness\gpu_cli.py mode GAMING
.\.venv\Scripts\python.exe harness\gpu_cli.py generate --prompt '请只回答你好'
.\.venv\Scripts\python.exe harness\gpu_cli.py status
.\.venv\Scripts\python.exe harness\gpu_cli.py mode AUTO
.\.venv\Scripts\python.exe harness\gpu_cli.py generate --prompt '请只回答你好'
```

GAMING 中那次诊断推理应被拒绝并返回非零退出码，这是预期行为；随后状态查询仍应无驻留模型。CLI 还提供 `unload`、`wake`，可用 `--url http://127.0.0.1:其他端口` 指定本机测试实例。问题若涉及真实私密内容，省略 `--prompt` 后按提示输入，避免把内容写进命令行历史。具体参数以 `harness/gpu_cli.py --help` 为准。

也可在保有同一令牌的 PowerShell 中调用接口：

```powershell
$taskHeaders = @{ Authorization = 'Bearer ' + $env:NOVA_GPU_TOKEN }
$taskBase = 'http://127.0.0.1:8000/api/gpu'
Invoke-RestMethod -Uri "$taskBase/status" -Headers $taskHeaders
Invoke-RestMethod -Method Post -Uri "$taskBase/mode" -Headers $taskHeaders `
    -ContentType 'application/json' -Body '{"mode":"GAMING"}'
```

GAMING 返回成功后，先查看 `backend.models`、`unload_verified` 和 `last_operation`。模式已变成 GAMING 与模型已成功卸载是两个判断：前者表示门禁关闭，后者必须通过实际 Ollama 状态复核。

## 状态应该怎样理解

- `mode`：当前 AUTO 或 GAMING。
- `local_requests_blocked`：当前是否禁止新的受控加载；故障和切换过程中也可能为 true。
- `active_requests`：管理器仍在跟踪的加载/推理数量；客户端断开不等于上游工作已结束。
- `pending_unloads`：已显式登记、尚未完成的卸载/回收任务数；大于 0 时不能把取消或排空超时视为资源已释放。
- `backend.available`、`backend.models`：Ollama 查询结果；查询失败时 models 为未知，不冒充空列表。
- `gpu_memory`：nvidia-smi 的设备总显存、已用显存、GPU 计算进程；查询失败显式报告不可用。
- `unload_verified`：最近卸载操作已成功确认，且当前查询仍无驻留模型。
- `last_unload_at`：最近一次成功卸载的时间，随模式状态持久化；它不能单独证明当前仍无模型。
- `last_operation`：当前进程最近的操作信息、模型加载耗时与显存采样；另有持久化事件记录供核对。
- `training.state`：没有登记训练租约时为 `idle`；登记后为 `preparing`、`reserved` 或重启后的 `unknown_after_restart`。`idle` 不代表没有外部训练进程，更不代表训练已暂停；`unmanaged` 是引入租约前的旧值，已不再返回。
- `game_detection`：首轮默认关闭；后续可选检测与状态字段见 [游戏检测说明](game-detection.md)。`cloud_fallback.enabled=false` 继续保持。

`nvidia-smi` 的总显存使用包含桌面、游戏、运行时及其他程序；Ollama 的 `size_vram` 只描述它报告的模型驻留量。Windows WDDM 下某些计算进程的显存为 N/A，接口保留为未知值，不能当作 0。

不要求显存下降到固定数值。判断卸载应结合 `/api/ps`、卸载响应、GPU 采样及计算进程；不能因为总显存不为 0 就认定卸载失败，也不能只凭显存下降宣称成功。

## 并发、重启和故障恢复

同一个用户、同一个 Ollama 地址只允许一个 GPU 调度进程。管理器使用按 Ollama 地址区分的操作系统文件锁，避免第二个 worker 各自维护一套模式。启动保持单 worker；不要绕过锁文件或用不同入口并行控制同一 Ollama。

模式、最近成功卸载时间与未确认操作标记保存在 `NOVA_DATA_DIR/gpu-mode.json`，通过临时文件和替换保存。GAMING 会跨后端重启保留；若上次操作没有确认完成、状态文件不可读或保存失败，管理器保守锁定本地入口，而不是启动后自动恢复 AUTO。

操作元数据另存到同目录 `gpu-events.jsonl`，包括操作类型、时间、耗时和显存采样；不记录 prompt、模型回答或认证令牌。该文件是运行记录，不应提交到 Git。

客户端取消请求不会提前从活动集合中移除仍运行的上游任务。取消只表示 Stop 已接收，不代表显存已释放；管理器会显式登记回收任务。若控制锁被 AUTO 状态查询等不会卸载的操作占用，回收任务等待锁释放和当前上游结束后，仍执行原卸载与 `/api/ps` 复核；失败则保留故障并退避重试。已有请求等待超时、上游生成结果不确定、卸载失败或 Ollama 停止时，都需要先检查状态。恢复的一般顺序是：

1. 确认 Ollama 服务可用，并等待原推理结束。
2. 显式调用 `/unload`，直到成功确认没有驻留模型。
3. 手动切回 AUTO。
4. 需要推理时再调用诊断入口或 `/wake`。

训练预留首次写入状态文件失败时，内存中的 `preparing` 租约会被撤销，因为该租约尚未交付给训练程序，调用方也没有可用的 lease id；同时保留 `STATE_WRITE_FAILED` 和 GAMING 安全锁定。修复状态目录权限或磁盘问题后，按上面的“显式卸载复核 → AUTO”流程即可继续，无需重启；已经持久化并交付给训练程序的租约不会被自动清除，也不会强制结束训练。

如果故障来自状态目录权限或磁盘问题，先修复实际原因；不要靠删除安全状态来绕过门禁。恢复 AUTO 不会自动恢复训练。

## 当前保护边界

2026-11-02 复审修复：关闭时只取消仍在等待/退避的补偿任务；已经执行的卸载可能含 `to_thread` HTTP，需保持跟踪并纳入有界等待。超时保留 `SHUTDOWN_OPERATION_UNCERTAIN` 和实例租约，不能让旧线程在新管理器加载后继续卸载。上游真实结束后，再次关闭可安全释放实例租约。

所有本地加载目标必须已登记受校验的资源策略；未登记名称、自定义别名及无效目标返回 `MODEL_POLICY_INVALID`（503），不先查询、加载或驱逐模型。已有 9B/4B 与受控 27B 模板沿用原门槛。需要支持新模型时应先登记策略并测试，不能通过更换 `NOVA_GPU_MODEL` 名称获得无限制加载。

**门禁只保护经过 Nova 管理器的请求。** Ollama 没有提供禁止所有本机客户端加载的全局游戏锁；其他程序直接调用 `127.0.0.1:11434`，仍可绕过管理器并重新加载模型。原 Lab 直接调用 Ollama 的脚本属于这一边界，游戏期间不要用它们发起推理。

后续 Nova Provider、聊天入口和训练启动器必须接入同一调度策略，不能直接调用底层 backend 来绕过门禁。本轮不通过防火墙修改或进程终止来承诺整台电脑的 GPU 隔离。

外部 PyTorch/Unsloth 训练尚未注册到管理器，不能安全自动暂停。游戏模式不会保存训练检查点，也不会结束 Python 进程；若有外部训练，需由用户使用该训练项目自身的保存与正常退出流程处理。

## 验收与下一轮

本轮真实验收已验证：中文生成及GPU驻留、GAMING卸载后`/api/ps=[]`、生成和唤醒返回423、重复GAMING不加载、后端/数据库健康200、重启保留GAMING、AUTO不预载、显式wake和下一条推理恢复、最终卸载。17项全部通过；Ollama不可用、卸载HTTP200但未消失、取消/竞态、训练以外的写盘故障等由离线替身测试覆盖。

一次实测卸载的显存为5708→1927MiB，同时模型列表为空，`llama-server.exe`从GPU进程清单消失。WDDM进程显存N/A保留未知，不按0计算。真实证据位于`E:\NovaAILab\logs\gpu-round1-20261101-034738-331\live-with-cli\report.json`；初次成功报告另保留在`live-1`，没有覆盖。

运行真实验收（会加载、卸载本机已安装模型；输出目录必须新建且尚不存在）：

```powershell
.\.venv\Scripts\python.exe -B harness\verify_gpu_live.py --output-dir 'E:\NovaAILab\logs\新的验收目录'
```

验收程序使用随机的进程内令牌、独立数据库和临时端口；不把令牌写文件/日志，正常结束验收后端。运行后模型保持卸载；不会替用户常驻启用GPU管理或改现有配置文件。

首轮之后已实现可选游戏白名单检测和游戏退出延迟恢复，单独验收见 [游戏检测说明](game-detection.md)。仍未实现：独立 AUTO 三分钟空闲监测、AI_FOCUS、训练启动互斥、安全检查点暂停/恢复、DeepSeek 回退、vLLM Sleep Mode 和图形页面。对应测试不能用本轮结果代替。

首轮五项通过后，用户已授权并完成下一项游戏检测的开发和模拟验收；不据此继续扩展其他调度功能。vLLM 需要按当时官方兼容条件核对，未来 Level 1 Sleep 需要开发模式，其管理端点必须保持本机鉴权，不得公开暴露。

Ollama 协议依据：[生成与 keep_alive](https://docs.ollama.com/api/generate)、[驻留模型查询](https://docs.ollama.com/api/ps)、[驻留及并发说明](https://docs.ollama.com/faq)。
