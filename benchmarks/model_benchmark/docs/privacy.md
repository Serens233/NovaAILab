# 隐私与提交边界

## 不读取、不提交

- 用户私人聊天、SQLite 数据库、记忆、人格状态、`.env`、API Key、令牌。
- 模型权重、GGUF、Ollama blob、完整原始输出日志。
- 其他桌面进程的名称/PID 列表（只保留 Ollama/llama 进程；其他只计数）。
- 真实 GPU 租约文件（它只是调度记录，可能包含用户名/时间安排）。

## 测试样例

- `cases/performance_zh.json` 是项目编写的合成中文样例。
- 三个样例均不含真实姓名、账号、地址、私人对话或数据库内容。
- 样例 SHA-256 写入结果，便于确认没有被替换。
- 预留的 persona/dialogue/memory 示例也全部是合成场景。

## 模型输出

默认只保存：

- 可见回复字符数；
- 回复 SHA-256；
- thinking 字符数。

不保存完整回复、完整思考内容或原始 NDJSON 流。只有显式加 `--preview` 才额外保存前 120 个字符的截断预览；即使如此也不保存完整输出。

## 进程与硬件

- `nvidia-smi` 整卡显存、利用率、温度、驱动版本：必要硬件事实。
- Ollama/llama 进程工作集与私有字节：只针对模型服务。
- 其他 GPU compute 进程：只记录数量，不记录名称/PID。
- 写盘前统一脱敏：配置路径转为仓库相对路径或 `<external>/<basename>`，`python_executable` 和 GPU UUID 不写入结果，租约只保留 `granted_by`/`expires_at`/`models`/`max_seconds` 等调度字段。

## 提交前审查

```powershell
git diff --cached
git status --short
```

确认没有：

- `.env`、`*.key`、`*.pem`、`credentials.json`；
- `*.db` / `*.sqlite` / `logs/` / `data/`；
- 含真实对话的输出；
- 真实租约、令牌或 API Key；
- 大模型文件或缓存。

`.gitignore` 只是防误提交措施，不能替代逐项审查。
