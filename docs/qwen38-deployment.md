# Nova V3：Qwen3.8-27B 实验接入

2026-11-01，Asia/Shanghai。**代码与安全验收通过；用户选择后台继续下载。27B权重尚未下载校验完成，也没有执行真实27B推理。Git提交/推送/PR缺少远程与提交身份。** 不将本阶段标为完整部署成功。

## 实际环境与审计

当前项目已移到`AI-Workspace/nova`，不是历史文档中的旧根目录。复用`chat/registry.py`模型注册表、`chat/service.py`角色提示与历史、`api/chat.py`接口、`gpu/manager.py`及`gpu/backend.py`，保留FastAPI、原SQLite和Next前端。没有新建Nova、聊天库、记忆或Strata模块。

实际Windows11、Ryzen9700X、RTX5060Ti16GB、驱动621.01、总显存16311MiB。系统物理内存15477MiB（单条16GB）；真实安全验收时可用2058MiB，后续快照2333MiB。全机GPU快照使用2870MiB，**这是桌面及其他程序占用，不能当作27B性能**。

Ollama0.40.13便携版已存在，只是服务未启动、用户PATH未包含其目录。已启动原服务、追加用户PATH并保存原值；新终端生效，已有VSCode终端需刷新环境。API `http://127.0.0.1:11434`正常，模型仍在原E盘`NovaAILab/models`，剩余约1.3TB，没有迁移或删除权重。9B/4B原模型ID和文件保留。

项目搬迁使Python可编辑安装仍指旧目录，已实际运行`uv sync --locked`，仅重建当前项目包，依赖锁和Python版本未升级。修改前备份123个工程文件到`E:\NovaAILab\backups\nova-v3-20261101-183353`，分支`feat/qwen38-27b`，不修改main。

## 模型来源与下载状态

目标为`hf.co/unsloth/Qwen3.8-27B-GGUF:UD-Q3_K_XL`。已核实公开HF仓库HTTP200、Apache-2.0、qwen35架构和该量化文件存在；发布文件大小13,146,393,504字节。发布方SHA与版本证据见 [来源研究](qwen38-source-research.md)。发布方哈希不等于本地已校验。

已实际执行：

```powershell
ollama pull 'hf.co/unsloth/Qwen3.8-27B-GGUF:UD-Q3_K_XL'
```

首次进度捕获器因Windows默认GBK不能输出Unicode进度字符而中断；仅停止本次遗留的拉取客户端，保留服务与缓存。诊断HF元数据及权重HEAD均200；官方API重试读超时的失败记录也保留。改为客户端直接写UTF-8日志后续传正常，未换安装版本或下载来源。

18:52快照：CLI显示12%、约1.6GB/13GB、2.3MB/s、剩余约1小时22分；此为当时估算。用户明确选择后台续传并先交付代码。预分配13GB的partial文件不是下载完成证明，缓存元数据的Completed也可能滞后；以客户端成功退出、摘要校验和tags/show为准。

日志根：`E:\NovaAILab\logs\nova-v3-20261101`。

- `pull-retry-utf8.log`持续记录原生CLI进度。
- `pull-retry-result.json`在客户端结束后写入实际退出码；文件未出现不代表成功。
- `download-progress-latest.json`和`deployment-status.json`为带时间的快照。
- `ollama-pull.raw.log`、`pull-api-result.json`保留前述失败，不能删掉或当作通过。

查看状态：

```powershell
ollama list
Get-Content 'E:\NovaAILab\logs\nova-v3-20261101\pull-retry-utf8.log' -Tail 3
```

下载完成后先执行`ollama show '<目标标识>'`核实架构、量化和实际名称；不要直接`ollama run`绕过Nova的RAM/GPU门禁。本轮未完成canonical名称/模板实际加载验证。

## 配置与启动

9B仍是默认模型，4B与DeepSeek配置保留。实验项默认关闭，模型列表额外显示其实验状态及不可用原因；开启后仍须通过真实RAM、显存和游戏门禁。

| 环境变量 | 默认值/用途 |
| --- | --- |
| `NOVA_QWEN38_ENABLED` | false，明确开启实验项 |
| `NOVA_QWEN38_MODEL` | 上述公开HF标识；最长128字符，与API/存储一致 |
| `NOVA_GPU_OLLAMA_URL` | 原回环HTTP地址，可配置端口，不能改成外部任意URL |
| `NOVA_QWEN38_CONTEXT` | 4096，配置范围512–8192 |
| `NOVA_QWEN38_NUM_PREDICT` | 256，初次正式测试可降低到64–128 |
| `NOVA_QWEN38_KEEP_ALIVE` | 180秒，可设0–180；空闲监测仍使用原控制器 |
| `NOVA_QWEN38_NUM_GPU` | 空，Ollama自动选择层数；不强制全GPU |
| `NOVA_QWEN38_TEMPERATURE` | 0.7 |
| `NOVA_QWEN38_MIN_TOTAL_RAM_MIB` | 28672，最低允许24576 |
| `NOVA_QWEN38_MIN_AVAILABLE_RAM_MIB` | 18432，最低允许12288 |
| `NOVA_QWEN38_MIN_FREE_VRAM_MIB` | 4096，供部分GPU/CPU加载的保守下限，不是全模型显存需求 |

这些是Nova的保守准入策略，不是发布方宣称的硬性RAM需求。当前16GB阶段不要降低门槛硬试。升级32GB后，也应先确认实际可用RAM至少18GiB且没有游戏/训练负载；不够就释放其他负载或继续9B/4B。初版没有全系统训练协调器，外部训练仍显示unmanaged，不能宣称已隔离任意GPU程序。

从当前仓库根启动稳定版：

```powershell
.\.venv\Scripts\python.exe -B harness\run_web.py
# 打开 http://127.0.0.1:3000
```

下载校验、内存升级与资源检查通过后，在启动前设置`$env:NOVA_QWEN38_ENABLED='true'`，再在原模型选择器选择实验项。默认模型不变。当前16GB即使手工开启也会被拒绝。

## API、卸载与回退

沿用`POST /api/v1/chat`和原请求字段。默认`stream=true`保持原SSE的meta/delta/done/error，不改变前端协议；新增可选`stream=false`返回`{"thread_id":"…","message":{…}}`。后端依然使用原Nova系统提示词、原会话历史和幂等ID；未发现长期记忆或Character Bible接口，不新建此类业务。

实验模型的流式、旧generate和wake均先做开关/RAM检查；RAM不足在查询标签、驱逐9B/4B或加载之前拒绝。模型名大小写不能绕过策略。所有加载继续经过原GpuManager，游戏/安全锁优先，预检与发送前重查资源，消费端取消不提前释放active。

卸载沿用`POST /api/gpu/unload`及Ollama keep_alive=0复核，GAMING禁止新加载。界面“进入游戏模式”也会锁定并卸载；确认可用后恢复AUTO，等待下一条请求，恢复不会预载。原独立空闲卸载、自动游戏检测和显存压力路径保留。

回退直接在原选择器选择9B或4B；本地模型不会在同一次已输出一部分的流中偷偷换模型。失败导致安全锁时，先显式卸载复核，再恢复AUTO。DeepSeek仍需原明确启用、密钥和计费同意，没有自动收费回退。若不再使用实验项，关闭`NOVA_QWEN38_ENABLED`并重启；卸载入口不受RAM不足或实验关闭阻碍。

## 实际验收与尚未执行

| 检查 | 实际结果 |
| --- | --- |
| 全量Python回归 | `python -B -m pytest -ra`，658通过、0失败、0跳过，11.88秒 |
| 前端适配回归 | `npm test`，17通过、0失败、0跳过，217.7676ms；前端代码未修改，不重复生产构建 |
| Ruff | 静态通过；98个Python文件格式通过，旧启动器仅补一处异常捕获格式，无语义变更 |
| 工程自检 | 五组PASS、0问题；不替代完整秘密审计 |
| 真实安全检查 | Windows/NVIDIA/Ollama真实采集+ASGI接口14项通过；流式/JSON实验请求409，GAMING请求/wake423，健康200，模型始终未加载，数据库正常关闭 |
| 模拟27B集成 | 配置、参数透传、原提示词、多轮、SSE/JSON、低RAM/遥测未知、游戏/取消竞态、失败锁定及9B/4B回退通过 |

真实安全检查不是27B推理。中文生成、实际首Token延迟、tokens/s、加载时间、CPU/GPU分配比例及27B带载卸载均**待下载校验完成并升级内存后测试**，性能数据保持null，不用9B数据代替。后端已保留eval_duration和tokens/s统计能力。没有训练、Strata安装、权重修改、驱动修改或付费API调用。

## Git交付阻塞

当前Git仍无历史提交和remote，提交姓名/邮箱均未配置；GitHub连接返回可用仓库为空。已向用户询问目标仓库URL及提交身份，尚未获得，不能代填身份、创建未知远程或虚构Commit/PR链接。已在功能分支完成本地改动、备份和测试，main未产生提交。补充信息/仓库授权后再审查暂存、提交、推送功能分支并创建PR，合并继续由用户确认。
