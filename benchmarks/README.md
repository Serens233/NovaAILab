# Nova 本地模型 Benchmark

这是独立于 Nova 生产运行时的实验工具。它不导入 `nova` 的 FastAPI 应用、GPU Manager 或 Router 运行时，只通过回环 Ollama HTTP API 做只读/受控加载，并用实测采样记录资源占用。唯一耦合是读取 Nova 的类型化配置（`nova.config`）：27B 准入阈值、27B 标签族和游戏进程名单必须与 Nova 同源，配置读不到时 benchmark 拒绝真实运行，而不是猜测一个更宽松的规则。

## 为什么独立

- 不修改生产模型服务、GPU Manager、数据库或前端。
- 不把量化文件大小当成显存占用；显存、内存都写入实测值。
- 冷启动前显式卸载模型，热启动复用同一模型；两种状态分开统计。
- 测试样例固定并记录 SHA-256，结果可复核。
- 真实 GPU 压测必须提供短期 `--lease` 租约，默认 dry-run。
- **默认不允许驱逐已驻留模型**：preflight 发现 `/api/ps` 里有模型就失败。只有租约显式列出这些模型、并且传入 `--allow-unload-loaded --execute` 时才会接管；接管后不会自动恢复这些模型。
- 游戏门禁失败关闭：Nova 状态文件读不到、`tasklist` 执行失败、没有游戏名单且 Nova 未启用检测时，preflight 都失败。
- 写盘前脱敏：路径转为仓库相对路径或 `<external>/<basename>`，删除 `python_executable` 与 GPU UUID，租约只保留调度字段。

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

`note` 不会写入结果；只有 `granted_by`、`expires_at`、`models`、`max_seconds` 会保留。

然后：

```powershell
python -m benchmarks.model_benchmark run `
  --models qwen3.5:4b,qwen3.5:9b `
  --execute --lease .\bench-lease.json `
  --baseline-commit 3bb0f6a7ffe868be1dc014772601fe41fa9036a6
```

租约只是调度协调记录，不是鉴权密钥；不要提交真实租约文件。运行结束时会卸载本次模型并记录清理结果。

如果 preflight 发现 Ollama 里还有其他模型：

- 默认直接拒绝，不读写这些模型；
- 只有租约的 `models` 显式列出它们，并且加 `--allow-unload-loaded`（同时必须 `--execute`）才允许卸载；
- 卸载后运行结束不会自动恢复，必须由用户/主会话重新加载。

游戏名单默认取 `NOVA_GPU_GAME_PROCESSES`；没有名单且 Nova 没有启用自动检测时，必须显式传 `--game-process <exe>`。`tasklist` 失败不会当作“没有游戏”。

### 基线与环境变量

- `--baseline-commit` 显式记录统一基准；也可用环境变量 `NOVA_BENCH_BASELINE_COMMIT`，但命令行的值优先。
- `--nova-state` 覆盖 Nova `gpu-mode.json` 路径；默认取 Nova settings 的 `NOVA_DATA_DIR/gpu-mode.json`。
- `--preview` 才会保存回复前 120 字符；默认只存字符数与 SHA-256。
- `--settle` 在冷启动完成后、热启动采样前等待指定秒数（默认 1 秒），用于让模型状态稳定。
- 租约到期或 `--max-seconds` 用尽会中止运行，并把 `status=blocked` 的结果写入磁盘。

## 输出

每次运行写入 `benchmarks/model_benchmark/results/<run-id>/`：

- `results.json`：完整结构化结果
- `results.csv`：一行一次请求
- `summary.md`：简洁汇总报告
- `environment.json`：硬件、OS、Git、Ollama 版本
- `models.json`：模型标识、digest、量化、默认参数、能力
- `cases.snapshot.json`：固定样例和 SHA-256

写盘前会脱敏：`cases_path`/`output_dir`/`nova_state_path` 变为仓库相对路径或 `<external>/<basename>`，`lease_path` 只保留 `<lease>/<basename>`，`environment.os` 不含 `python_executable`，`environment.gpu` 不含 UUID。

`inventory-*` 目录只包含只读元数据，不含性能数据。

## 测试

```powershell
python -m pytest benchmarks/model_benchmark/tests -q
python -m pytest -q   # 默认 testpaths 也包含 benchmark 测试
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
- 16GB 内存机器不加载 27B；27B 只做元数据和准入检查，阈值直接来自 Nova settings（默认总内存 28 GiB、可用内存 18 GiB、可用显存 4 GiB）。
- 默认不保存回复预览；打开 `--preview` 也只保存前 120 字符，完整输出不落盘。
- 默认不驱逐任何已驻留模型；本工具不是生产入口，也不能替代 Nova GPU Manager 的独占协调。
