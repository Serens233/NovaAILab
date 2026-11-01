# 预留评测接口

性能套件只回答“加载多快、首 token 多快、每秒多少 token、占多少显存/内存”。它**不**回答“人格是否一致、对话是否自然、记忆是否准确”。这三类评测在 `suites/` 中以 `reserved` 形式预留，当前不会调用模型，也不会伪造分数。

## 接口形状

所有套件实现 `suites/base.py` 的 `Suite`：

```python
class Suite(ABC):
    id: str
    description: str
    status: str
    required_inputs: tuple[str, ...]

    @abstractmethod
    def run(self, context: SuiteContext) -> SuiteResult: ...
```

- `available`：可以执行（当前只有 `performance`）。
- `reserved`：接口存在，但缺少输入或评分器；`run()` 返回 `status="reserved"` 和原因，不调用模型。
- `SuiteResult` 包含 `runs`、`status`、`reason`、`metadata`，便于未来接入真正的评分结果。

## Persona Consistency

需要：

- Immutable Canon（不可自动修改的核心身份）；
- Evolvable Persona（可控发展的兴趣/习惯）；
- 固定压力场景；
- 已审查的评分 rubric。

样例 schema 见 `cases/persona_consistency.example.json`，套件类为 `suites/persona_consistency.py`。评分至少应包含 `quote`、`trait_match`、`drift_flags`，并保留人工复核样本；不能只用另一个模型一句“通过”。

## Natural Dialogue

需要：

- 多轮脚本或用户模拟器；
- 对话连贯性、回应性、非机械安慰的 rubric；
- 至少一部分人工复核。

样例 schema 见 `cases/natural_dialogue.example.json`，套件类为 `suites/natural_dialogue.py`。性能套件的单轮 prompt 不能直接当作自然对话评测。

## Memory Accuracy

需要：

- Phase 3 的记忆 API / 隔离存储；
- 合成事实、干扰轮次、探针问题；
- 溯源要求（回答是否引用了正确记忆）；
- 评分 rubric。

样例 schema 见 `cases/memory_accuracy.example.json`，套件类为 `suites/memory_accuracy.py`。**禁止**使用用户真实记忆或私人对话作为测试夹具；测试必须在临时数据库/临时线程中完成，结束后清理。

## 接入方式

1. 实现套件类，把 `status` 从 `reserved` 改为 `available`；
2. 在 `suites/__init__.py` 注册；
3. 为 `run()` 增加隔离的 fixture、评分器和失败处理；
4. 增加 fake/mock 测试，确保没有真实模型时也能验证协议；
5. 更新本文件和 `results-schema.md`；
6. 真实模型评分需要单独的 GPU/模型调度许可，不能与性能跑批混在一起。

在评分器完成并经过审查前，`reserved` 就是正确状态；不要为了“看起来完整”而返回空分数或固定分数。
