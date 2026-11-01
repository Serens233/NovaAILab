# Nova 本地模型 Benchmark

这是独立于 Nova 生产运行时的实验工具。它不导入 `nova` 的 Model Router、GPU Manager 或 FastAPI 应用，只通过回环 Ollama HTTP API 做只读/受控加载，并用实测采样记录资源占用。

## 为什么独立

- 不修改生产模型服务、GPU Manager、数据库或前端。
- 不把量化文件大小当成显存占用；显存、内存都写入实测值。
- 冷启动前显式卸载模型，热启动复用同一模型；两种状态分开统计。
- 测试样例固定并记录 SHA-256，结果可复核。
- 真实 GPU 压测必须提供短期 `--lease` 租约，默认 dry-run。

## 快速开始

在仓库根目录执行（使用项目自己的 Python）：

```powershell
python -m benchmarks.model_benchmark suites
python -m benchmarks.model_benchmark inventory --models qwen3.5:9b,qwen3.5:4b
python -m benchmarks.model_benchmark preflight --models qwen3.5:9b,qwen3.5:4b
python -m benchmarks.model_benchmark run --models qwen3.5:9b,qwen3.5:4b
```

最后一条默认是 dry-run，只打印计划和预检，不加载模型。

### 真实执行

先由主会话/用户给出独占 GPU 时间窗口，再写一个短期租约：

```json
{
  "granted_by": "main session (user approved 2026-11-01T23:00+08:00)",
  "expires_at": "2026-11-01T23:30:00+08:00",
  "models": ["qwen3.5:9b", "qwen3.5:4b"],
  "max_seconds": 900,
  "note": "exclusive GPU window; no games or other GPU tests"
}
```

然后：

```powershell
python -m benchmarks.model_benchmark run `
  --models qwen3.5:4b,qwen3.5:9b `
  --execute --lease .\bench-lease.json `
  --baseline-commit 3bb0f6a7ffe868be1dc014772601fe41fa9036a6
```

租约只是调度协调记录，不是鉴权密钥；不要提交真实租约文件。运行结束后工具会卸载模型并记录清理结果。

## 输出

每次运行写入 `benchmarks/model_benchmark/results/<run-id>/`：

- `results.json`：完整结构化结果
- `results.csv`：一行一次请求
- `summary.md`：简洁汇总报告
- `environment.json`：硬件、OS、Git、Ollama 版本
- `models.json`：模型标识、digest、量化、默认参数、能力
- `cases.snapshot.json`：固定样例和 SHA-256

`inventory-*` 目录只包含只读元数据，不含性能数据。

## 测试

```powershell
python -m pytest benchmarks/model_benchmark/tests -q
```

测试使用进程内 fake Ollama，不加载真实模型、不访问 GPU。

## 目录

```text
benchmarks/model_benchmark/
├── cli.py                 # suites / inventory / preflight / run
├── runner.py              # 冷启动、热启动、资源采样编排
├── ollama_client.py       # 回环 Ollama 客户端
├── monitor.py             # nvidia-smi + 进程 + 系统内存采样
├── safety.py              # 游戏/租约/锁/27B 准入检查
├── report.py              # JSON/CSV/Markdown
├── cases/                 # 固定合成样例
├── suites/                # 性能套件与预留接口
├── docs/                  # 方法、schema、隐私、未来套件
└── results/               # 已提交的脱敏结果
```

## 边界

- 不评价回答质量。人格一致性、自然对话、记忆准确性的接口已预留，但当前 `reserved`，不会伪造评分。
- 单机、单卡、单次工程基线，不是模型排行榜。
- 冷启动只表示模型不在 Ollama 显存/内存中，没有清空 Windows 文件系统页缓存。
- 16GB 内存机器不加载 27B；27B 只做元数据和准入检查。
