# 固定测试样例

`performance_zh.json` 是当前唯一可执行的性能样例。所有文本均为本项目编写的合成内容，不包含用户私人对话、真实姓名、账号、路径或数据库内容。

## 性能样例字段

| 字段 | 说明 |
| --- | --- |
| `id` | 稳定标识，只允许小写字母、数字、`_`、`-` |
| `category` | 样例类别，仅用于报告分组 |
| `language` | 语言标识，当前为 `zh-CN` |
| `system` | 固定系统提示词 |
| `prompt` | 固定用户输入，最长 6000 字符 |
| `max_tokens` | 该样例的生成上限；缺省时使用全局 `num_predict` |
| `notes` | 给人看的说明，不进入模型请求 |

## 为什么固定样例

- 冷启动和热启动使用同一组 prompt，避免用不同问题比较吞吐。
- 样例哈希写入 `results.json` 和 `cases.snapshot.json`，可核对是否被改动。
- 样例不包含真实日志；模型输出只保留截断预览与 SHA-256，不保存完整原文。

## 预留评测样例

`persona_consistency.example.json`、`natural_dialogue.example.json`、`memory_accuracy.example.json` 只是接口示例，均为 `enabled: false`。当前 runner 不会读取或执行它们；对应的 suite 在 `benchmarks/model_benchmark/suites/` 中返回 `reserved`，不会伪造评分。
