# Nova 本地模型 Benchmark 汇总

- 运行 ID：`20261101T225657+0800-625f`
- 状态：`partial`
- 统一基准 Commit：`3bb0f6a7ffe868be1dc014772601fe41fa9036a6`
- 运行代码 Commit：`11866bfce23871cebd6c109301b97e006cccdfed`
- 分支：`experiment/dsh-model-benchmark`
- 测试时间：2026-11-01T22:56:56+08:00
- 测试样例 SHA-256：`baf2065dcc1421997fd734c42596b90b84c6320359bca114e9a440c66e19963c`

## 环境

| 项目 | 值 |
| --- | --- |
| OS | Windows-11-10.0.26100-SP0 |
| CPU | AMD Ryzen 7 9700X 8-Core Processor |
| 逻辑处理器 | 16 |
| 系统内存 | 15,477.3 MiB |
| GPU | NVIDIA GeForce RTX 5060 Ti |
| GPU 显存 | 16,311 MiB |
| 驱动 | 621.01 |
| Ollama | 0.40.13 |
| 采样时间 | 2026-11-01T22:56:56+08:00 |
| 时区 | 中国标准时间 |

## 模型与量化

| 模型 | 参数量 | 参数规模 | 量化 | 模型上下文 | 文件 GiB | digest |
| --- | --- | --- | --- | --- | --- | --- |
| qwen3.5:4b | 4,205,751,296 | 4.2B | Q4_K_M | 262,144 | 3.10 | d8b0f5e9760c |
| qwen3.5:9b | 8,953,803,264 | 9.0B | Q4_K_M | 262,144 | 6.10 | 56671c2ab938 |

## 冷启动

| 模型 | 准入 | 加载 ms | 冷启动 TTFT ms | tok/s | 总耗时 ms |
| --- | --- | --- | --- | --- | --- |
| qwen3.5:4b | 允许 | — | — | — | — |
| qwen3.5:9b | 允许 | 29,238.8 | 50,838.8 | 32.3 | 52,909.5 |

## 热启动

| 模型 | 样例 | TTFT p50 ms | TTFT p95 ms | tok/s p50 | tok/s p95 | eval p50 ms |
| --- | --- | --- | --- | --- | --- | --- |
| qwen3.5:9b | long_context_zh | 156.2 | 259.6 | 63.4 | 63.8 | 2,257.3 |
| qwen3.5:9b | medium_reasoning_zh | 132.7 | 482.9 | 62.1 | 63.9 | 2,576.5 |
| qwen3.5:9b | short_factual_zh | 128.3 | 133.5 | 63.5 | 63.5 | 1,055.0 |

## 实测资源占用

> 显存数据来自 `nvidia-smi` 与 Ollama `/api/ps` 的实测采样；
> 不根据 GGUF 量化文件大小推算。整卡显存包含桌面及其他程序，
> 应同时查看峰值增量与 Ollama 报告的 `size_vram`。

| 模型 | GPU 基线 MiB | GPU 峰值 MiB | GPU 峰值增量 MiB | Ollama size_vram MiB | llama 工作集峰值 MiB | 系统已用峰值 MiB | 系统可用最低 MiB |
| --- | --- | --- | --- | --- | --- | --- | --- |
| qwen3.5:4b | — | — | — | — | — | — | — |
| qwen3.5:9b | 2,129.0 | 8,644.0 | 6,515.0 | 5,346.6 | 1,378.0 | 12,977.4 | 2,499.9 |

## 测试条件

- 上下文长度 `num_ctx`：4096
- 全局 `num_predict`：128；每个样例的 `max_tokens` 会覆盖
- 样例 token 上限：short_factual_zh=128, medium_reasoning_zh=160, long_context_zh=192
- 采样：temperature=0.2, top_p=0.9, top_k=40, seed=42, repeat_penalty=1.1
- 思考内容：关闭
- 冷启动次数/模型：1；热启动次数/样例：3
- 测试样例：short_factual_zh, medium_reasoning_zh, long_context_zh

## 限制与未执行项

- 冷启动定义为模型不在 Ollama /api/ps 中；没有清空 Windows 文件系统页缓存。
- 整卡显存包含桌面、浏览器等基线占用，应结合峰值增量和 Ollama size_vram 阅读。
- Windows/WDDM 下 nvidia-smi 的逐进程显存可能为 N/A；为空时不会用文件大小推算。
- 单机、单卡、固定样例的单次工程基线，不足以单独决定生产模型选择。
- 本套件不评价回答质量、人格一致性、自然对话或记忆准确性。
- 27B 实验模型在 16GB 内存机器上只做元数据检查，不执行加载。
- 响应仅保留截断预览和 SHA-256，不保存完整原始输出。

## 结论边界

本报告是单机、单次、固定样例的工程性能基线，不是模型质量排名，
也不代表人格一致性、自然对话或记忆准确性。后三类评测接口已保留，
但尚未实现评分，不能把本报告的吞吐数据当作质量结论。
