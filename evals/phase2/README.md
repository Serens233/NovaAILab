# Phase 2 固定角色评估集

题集仅使用已批准的最小Nova设定与合成输入，不读取私人聊天或长期记忆。`cases.json`共40例：普通8、多轮8、话题切换6、矛盾6、注入6、隔离/错误/取消6。各类前2例标记为真实代表用例，每模型默认12例；多轮例会产生多次实际请求。

`cases.sha256`固定题集字节摘要，正式运行前提交题集及摘要。不得根据模型结果改题或重新挑选高分问题。新增版本应使用新suite_id和独立报告。

## 执行

先启动本阶段Nova FastAPI，使用独立临时数据库/数据目录、空闲AUTO状态和仅测试进程持有的管理令牌。不要复用正在聊天、训练或运行游戏的实例；不要把令牌写入源码或命令参数。

```powershell
# 默认仅检查固定题集和输出计划，不联网、不读取令牌。
python -B harness/evaluate_phase2.py --output-dir E:/NovaAILab/logs/phase2-eval-new

# NOVA_GPU_TOKEN由启动测试后端的进程安全传入；结果目录必须位于仓库外且不存在。
python -B harness/evaluate_phase2.py --base-url http://127.0.0.1:8000 `
  --models qwen3.5:9b qwen3.5:4b --output-dir E:/NovaAILab/logs/phase2-eval-new --execute
```

工具只允许实际9B/4B，通过现有Nova API创建合成会话和流式请求，不直连Ollama生成、不下载、不使用27B或云端。运行前有驻留模型即拒绝；GAMING、训练、故障或并发活动导致停止。结束只通过受管卸载接口复核自己测试的模型，不触碰未知模型；原卸载接口会保留GAMING保护，不擅自切回AUTO。由启动器正常关闭独立测试实例，不对用户常驻实例做恢复操作。

`--all-cases`显式运行全部40例，包含取消与非法字段探针；默认12例已覆盖每个类别。所有生成数据保存到外部`records.jsonl`和`report.json`，包括实际输入、历史、流事件、输出、错误和时延。原始记录不提交Git。连接异常时尝试用原request_id取消，资源释放未确认即报告失败。

## 结果解释

- 自动记录只判断协议、请求完成/错误、消息身份隔离和时延；不通过关键词给人格或权限安全打分。Canon写入权限等由G1确定性测试验证，HTTP评估工具不能据回答口号证明后台权限安全。
- `rubric.json`规定六维0–2分，满分12。`manual`与`ai_assisted`分别记录，初始全部null。未评/不适用保留null并给理由，不以0补齐，不生成虚假满分。
- `first_delta_ms`为HTTP首可见正文延迟，`elapsed_ms`为整次请求。provider统计缺失保留null，`prompt_eval_count`只有后端实际提供才记录。冷/热条件必须由启动器和驻留状态另行证实，不能从一次延迟自行推断。
- 即使协议全部完成，`human_acceptance`仍为pending；项目负责人决定最终角色质量验收。

本工具不验证真实游戏、浏览器GUI、手机或付费云端；这些不能由Mock、HTTP或手动GAMING结果替代。
