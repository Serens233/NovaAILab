# 结果 schema

`results.json` 的 `schema_version` 当前为 `1.0`。所有时间均为本地时区 ISO-8601；所有 MiB 为 1024² 字节。

## 顶层

| 字段 | 说明 |
| --- | --- |
| `schema_version` | 结果 schema 版本 |
| `run_id` | 时间戳 + 随机后缀 |
| `generated_at` | 环境采集时间 |
| `status` | `completed` / `partial` / `failed` / `blocked` |
| `baseline` | 基准 commit、分支、是否 dirty、显式基准 SHA |
| `config` | 本次模型、样例、上下文、采样、超时、租约路径；路径已脱敏为相对路径或 `<external>/<basename>` |
| `environment` | OS/CPU/内存/GPU/Git/Ollama；不含 `python_executable`、GPU UUID |
| `models` | 模型标识、digest、量化、默认参数、能力、准入结果 |
| `cases` | 固定样例 SHA-256 与完整合成样例 |
| `runs` | 一次请求一行 |
| `suite_results` | 已执行套件的状态 |
| `summary` | 按模型聚合的冷/热/内存统计 |
| `limitations` | 本次真实限制 |
| `takeover` | `{previously_loaded, unloaded, restored}`；默认空，只有显式接管时才有内容 |
| `preflight` | 预检 checks 和 warning；租约只保留调度字段 |
| `error` | 整体错误（如适用）；超时为 `blocked` 并在此记录原因 |

## `models[]`

| 字段 | 说明 |
| --- | --- |
| `id` | Ollama 模型标识（含 tag） |
| `installed` | `/api/tags` 是否发现 |
| `digest` | Ollama 模型 digest |
| `size_bytes` / `size_gib` | 模型 blob 大小，仅用于标识，不用于推断显存 |
| `details.parameter_size` | 参数规模 |
| `details.quantization_level` | 实际量化（以 `show` 为准，可能与 tag 不一致） |
| `details.family` / `runner` | 架构与 runner |
| `model_info.context_length` | 模型自身声明的最大上下文 |
| `model_info.parameter_count` | 参数数量 |
| `model_info.license` | 许可证标识 |
| `default_parameters` | Ollama show 的默认参数 |
| `capabilities` | completion/vision/tools/thinking 等 |
| `template_sha256` / `modelfile_sha256` / `license_sha256` | 大段文本的哈希，不保存全文 |
| `admission` | `{allowed, reason}`；27B 或未安装模型会在这里被拒绝 |

## `runs[]`

| 字段 | 说明 |
| --- | --- |
| `run_index` | 全局请求序号 |
| `model` / `case_id` | 模型与固定样例 |
| `phase` | `cold` / `warm` / `warmup` |
| `repetition` | 同阶段第几次 |
| `success` | 是否收到完整 `done` 且非空可见回复 |
| `started_at` | 请求开始时间 |
| `timings` | 见下表 |
| `memory` | 见下表 |
| `response` | 字符数、SHA-256、thinking 字符数、chunk 数、done_reason；预览默认 `null`，加 `--preview` 才写前 120 字符 |
| `request` | 采样参数、`num_ctx`、`num_predict`、`think`、messages 哈希 |
| `error` | 失败原因；成功为 `null` |

### `timings`

`ttft_ms`、`wall_time_ms`、`load_duration_ms`、`prompt_eval_duration_ms`、`eval_duration_ms`、`total_duration_ms`、`prompt_eval_count`、`eval_count`、`tokens_per_second`、`prompt_tokens_per_second`、`end_to_end_tokens_per_second`、`provider_timings`。

### `memory`

`gpu_total_baseline_mib`、`gpu_total_peak_mib`、`gpu_total_delta_peak_mib`、`gpu_util_peak_percent`、`gpu_temperature_peak_c`、`system_total_mib`、`system_used_baseline_mib`、`system_used_peak_mib`、`system_available_min_mib`、`ollama_working_set_peak_mib`、`llama_working_set_peak_mib`、`ollama_private_peak_mib`、`llama_private_peak_mib`、`model_size_vram_mib`、`model_size_mib`、`process_source`、`compute_apps_start`、`compute_apps_end`。

`model_size_vram_mib` / `model_size_mib` 来自 Ollama `/api/ps`；`compute_apps` 只保留 Ollama/llama 进程，其他进程只计数不记名。

## `summary`

```json
{
  "qwen3.5:4b": {
    "cold": {"runs": 1, "success": 1, "ttft_ms": {"p50": 0.0, "p95": 0.0}},
    "warm": {"runs": 9, "success": 9, "tokens_per_second": {"p50": 0.0}},
    "warm_by_case": {},
    "memory": {},
    "failures": 0
  }
}
```

每个指标块都是 `count/mean/median/p50/p95/min/max/stdev`；没有样本时该块为 0 或 `null`。

## `results.csv`

固定列，一行一次请求；列名与 `runs` 的主要字段对应，便于 Excel/ pandas 直接读取。失败行也保留，`success=false`、`error` 有值。

## 版本与兼容

- 新字段只增不改；删除/重命名必须提升 `schema_version`。
- 旧结果不重新解释；报告会写明生成时的 schema 和工具版本。
