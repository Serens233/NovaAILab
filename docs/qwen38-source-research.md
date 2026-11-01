# Qwen3.8-27B 来源与兼容性核实

2026-11-01，Asia/Shanghai。此记录仅涉及公开文档及小型元数据请求，不表示模型权重已经下载或真实推理已通过；下载和验收状态以主任务报告为准。

## 指定模型与文件

[Hugging Face模型API](https://huggingface.co/api/models/unsloth/Qwen3.8-27B-GGUF)实际返回HTTP 200，模型公开、非gated。核实revision为`4ca720788d1e01f1bff70c033e0d0028fd02e502`，[该版本README](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/raw/4ca720788d1e01f1bff70c033e0d0028fd02e502/README.md)也为HTTP 200。

- 用户指定推理标识：`hf.co/unsloth/Qwen3.8-27B-GGUF:UD-Q3_K_XL`，没有替换为其他模型。
- [文件清单API](https://huggingface.co/api/models/unsloth/Qwen3.8-27B-GGUF/tree/main?recursive=false&expand=false)实际HTTP 200；目标`Qwen3.8-27B-UD-Q3_K_XL.gguf`为 **13,146,393,504字节，约12.24GiB**。
- 该文件LFS SHA-256：`8c2a45ff85e7674ca185ec8eb6cdeab0e617ed9d8018caed0b64380eb2a67a5e`。此处为发布方元数据，不冒充本地文件哈希验收。
- Model card声明Apache-2.0；base model为`Qwen/Qwen3.8-27B`。`config.json`记录`Qwen3_5ForConditionalGeneration`、`qwen3_5`，GGUF元数据架构为`qwen35`，不是根据名称推测。

## Ollama兼容依据

[Ollama v0.32.12发布记录](https://github.com/ollama/ollama/releases/tag/v0.32.12)明确加入Qwen3.8-27B；[v0.32.13](https://github.com/ollama/ollama/releases/tag/v0.32.13)补充developer instructions。系列支持并不等于本机指定UD量化及嵌入聊天模板已通过实测；应先检查实际Ollama版本，再验证下载后`/api/show`和生成行为，不盲目升级全部环境。

[HF官方Ollama文档](https://huggingface.co/docs/hub/en/ollama)支持`hf.co/namespace/repository:quantization`，量化标签大小写不敏感。拉取后仍应核对本机tags返回的规范名称，避免旧客户端严格名称比较误拒绝。所有Nova请求继续使用既有Ollama客户端与GPU Manager。

## 资源边界

[Unsloth官方指南](https://unsloth.ai/docs/models/qwen3.8)将3-bit参考内存列为12–14GB，口径是RAM+VRAM或统一内存，不是对16GB Windows主机RAM的稳定运行保证。模型文件大小不含全部系统、映射、KV缓存与运行时开销。

当前阶段可先下载、登记和模拟验证。正式加载应结合实际可用RAM/VRAM和系统余量；资源不足则推理、首Token延迟、加载时间和tokens/s均保持未测，待硬件升级后再验收。初始上下文4096、单请求、不启用MTP或长上下文，不强制完整GPU加载，不修改权重、不将GGUF当BF16微调权重。

[Ollama FAQ](https://docs.ollama.com/faq)说明`num_ctx`上下文配置、`keep_alive=0`卸载和`ollama ps`的CPU/GPU分配显示；它们是后续测量依据，不是本记录中的实测结果。
