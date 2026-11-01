# Nova 本地模型清单与测试环境

本文件只读采集 `ollama list/show` 与硬件信息，不加载模型、不做 GPU 压测。

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
| 采样时间 | 2026-11-01T22:53:37+08:00 |
| 时区 | 中国标准时间 |

## 模型

| 模型 | 参数量 | 参数规模 | 量化 | 模型上下文 | 文件 GiB | digest |
| --- | --- | --- | --- | --- | --- | --- |
| qwen3.5:9b | 8,953,803,264 | 9.0B | Q4_K_M | 262,144 | 6.10 | 56671c2ab938 |
| qwen3.5:4b | 4,205,751,296 | 4.2B | Q4_K_M | 262,144 | 3.10 | d8b0f5e9760c |
| qwen3.8:27b-q3_k_xl | 27,320,697,856 | 27.3B | Q3_K_L | 262,144 | 13.11 | d4a3fff01c21 |

## 固定样例

| 样例 | 类别 | 语言 | prompt 字符数 | max_tokens |
| --- | --- | --- | --- | --- |
| short_factual_zh | factual_explanation | zh-CN | 27 | 128 |
| medium_reasoning_zh | reasoning_math | zh-CN | 85 | 160 |
| long_context_zh | long_context_summary | zh-CN | 751 | 192 |

> `ollama show` 的模板、许可证全文和 modelfile 未写入结果，只保留哈希与
> 必要元数据，避免把大段模型文件内容放进仓库。
