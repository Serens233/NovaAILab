# 已提交结果

本目录保存脱敏后的结构化结果。原始 NDJSON、完整模型输出、临时租约和日志不提交。

## 当前内容

- `inventory-20261101/`：2026-11-01 22:52（Asia/Shanghai）只读采集的 Ollama 模型清单、量化、模型元数据、CPU/GPU/内存/Git 环境。没有加载模型，没有性能数据。
- `20261101T230010+0800-b39b/`：**主结果**。4B 与 9B 均完成 1 次冷启动 + 9 次热启动；状态 `completed`。
- `20261101T225657+0800-625f/`：首次尝试，4B 冷启动 HTTP 500、9B 完成；保留为失败/重试证据，见其中的 `RETRY_NOTE.md`。4B 最终数据不取自此目录。

简短结论见 [run-20261101.md](../docs/run-20261101.md)。

## 运行产物

`run` 会创建 `<run-id>/`：

```text
<run-id>/
├── results.json
├── results.csv
├── summary.md
├── environment.json
├── models.json
└── cases.snapshot.json
```

运行 ID 由本地时间 + 随机后缀组成，例如 `20261101T231500+0800-a1b2`。

## 复现

```powershell
python -m benchmarks.model_benchmark inventory --models qwen3.5:9b,qwen3.5:4b
python -m benchmarks.model_benchmark run --models qwen3.5:4b,qwen3.5:9b --execute --lease <lease.json>
```

复现时确认：

- 统一基准 commit 与 `baseline.baseline_commit` 一致；
- 模型 digest 与 `models[].digest` 一致；
- 量化以 `details.quantization_level` 为准；
- 环境、驱动、Ollama 版本、固定样例哈希与报告一致；
- 结果目录没有用户私人内容。

## 结果解释

`summary.md` 是给人看的简表；`results.json` 是权威结构化数据；CSV 便于二次分析。不要只用某一个数字下结论，至少同时看：

- 冷启动 `load_duration_ms` 与 `ttft_ms`；
- 热启动 `tokens_per_second` 的 p50/p95；
- `gpu_total_delta_peak_mib` 与 Ollama `model_size_vram_mib`；
- 失败样本和 `limitations`。
