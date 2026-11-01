# 测量方法

## 固定条件

| 项目 | 值 |
| --- | --- |
| Ollama | `127.0.0.1:11434`，本机 HTTP API |
| 上下文 | `num_ctx=4096` |
| 生成上限 | 每个样例 `max_tokens`（128/160/192） |
| 采样 | temperature 0.2、top_p 0.9、top_k 40、seed 42、repeat_penalty 1.1 |
| 思考内容 | `think=false`，避免把隐藏思考 token 计入可见回复吞吐 |
| keep_alive | 热启动 `5m`；冷启动前用 `keep_alive=0` 卸载并轮询 `/api/ps` |
| 样例 | `cases/performance_zh.json` 的三个固定中文合成样例 |

## 冷启动与热启动

**冷启动**：确认目标模型不在 `/api/ps` 中（已从 Ollama 卸载），然后在同一进程内发起一次普通聊天请求。记录：

- `load_duration_ms`：Ollama 在最终 chunk 中报告的实际加载时长（纳秒换算）；
- `ttft_ms`：客户端从请求开始到第一个非空 `message.content` 的时间，包含加载、排队、prompt eval；
- `wall_time_ms`：请求开始到最终 `done` chunk 的墙钟时间。

冷启动**不等于**操作系统页缓存冷启动。没有使用 `rammap`、`EmptyStandbyList` 或类似工具清空 Windows 文件缓存，因此磁盘缓存仍可能是热的。报告里会明确写出这一点。

**热启动**：冷启动完成后模型仍在 `/api/ps` 中，按样例和重复次数继续请求；每次请求间隔很短，`keep_alive=5m` 保持驻留。热启动统计不包含第一次冷启动样本。

## 吞吐定义

- `tokens_per_second = eval_count / (eval_duration_ns / 1e9)`：Ollama 报告的纯生成阶段吞吐。
- `end_to_end_tokens_per_second = eval_count / wall_time_s`：包含加载、prompt eval、网络与调度的端到端吞吐。
- `prompt_tokens_per_second = prompt_eval_count / prompt_eval_duration`：prompt 处理速度。

报告同时给出 p50、p95、mean、min、max；样本数少于 3 时只作为工程参考，不做统计显著性结论。

## 资源测量

### GPU 显存

1. `nvidia-smi --query-gpu=memory.used,...` 每 0.5 秒采样整卡显存。
2. 记录运行前基线、峰值、峰值增量；整卡显存包含桌面/浏览器等基线，不能把峰值全算给模型。
3. 运行结束后读取 Ollama `/api/ps` 的 `size_vram` 和 `size`；这是 Ollama 自己报告的实际驻留量。
4. 若 `nvidia-smi --query-compute-apps` 给出 Ollama/llama 进程的 `used_gpu_memory`，保留该实测值；Windows/WDDM 下常为 `N/A`，此时字段为 `null`，**不会**用 GGUF 文件大小代替。
5. 只保留 Ollama/llama 进程；其他桌面进程名不写入结果，避免泄露用户软件使用情况。

### 内存

- 系统总内存、可用内存、已用内存来自 Windows `GlobalMemoryStatusEx`。
- `ollama.exe` 与 `ollama_llama_server.exe` / `llama-server.exe` 的工作集、私有字节通过 Windows `GetProcessMemoryInfo` 采样；失败时回退到 `psutil`（若安装），再失败则标记 `process_source="unavailable"`。
- 记录基线、峰值、系统可用最低值。

## 安全门禁与统一窗口

- 真实运行必须有租约；`max_duration_s` 不能超过租约的 `max_seconds`。
- 运行开始前读取 Nova 的 `gpu-mode.json`；Nova 已启用 GPU 管理但状态文件读不到时失败关闭。
- 游戏名单来自 `NOVA_GPU_GAME_PROCESSES` 或显式 `--game-process`；进程枚举失败视为不能确认无游戏。
- 默认不驱逐 `/api/ps` 中已有的模型；只有租约列出这些模型并显式 `--allow-unload-loaded` 时才接管，结束后不恢复。
- 从租约到期时间和 `--max-seconds` 取较早者作为硬截止线；每个请求前后和流式响应过程中检查，超时中止并写 `status=blocked` 结果。
- 27B 准入阈值直接读取 Nova settings（`NOVA_QWEN38_MIN_*`），不在 benchmark 里另存一套数字。
- 冷启动完成后按 `--settle` 等待（默认 1 秒）再开始热启动采样，避免把加载后的瞬时状态混入热启动统计。
- 默认不保存回复预览；`--preview` 只保存前 120 字符。

## 统计与报告

- 每个模型一次冷启动（`cold_repetitions=1`）。
- 每个样例 3 次热启动（`warm_repetitions=3`），三个样例共 9 次。
- 失败请求保留在 `runs` 和 CSV 中，`success=false`，不参与性能统计；模型级状态为 `partial`/`failed`。
- 运行结束卸载模型，并记录 `cleanup` 与 `loaded_after`。

## 已知限制

- 单机、单卡、单次运行；后台桌面程序会改变整卡显存基线。
- Windows 调度、驱动、Ollama 版本、模型首次 mmap 均会影响冷启动。
- 不同量化、上下文、采样和 `think` 设置的结果不可直接横比。
- 本工具不判断回答对错；性能结果不能替代质量/人格/记忆评测。
- 27B 在当前 16GB 内存机器上只做 `ollama show` 元数据与准入检查，不加载。
