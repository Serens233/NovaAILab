# Nova V2：本机 Web 聊天

2026-11-01，Asia/Shanghai。本轮在原项目上增加独立前端与最小聊天模块，不替换 FastAPI、SQLite、GPU Manager 或训练环境。人格、长期记忆、AI_FOCUS 和训练实验页面仍属后续范围。

## 开工审计与备份

- Windows 11 专业版，10.0.26100，64位；Ryzen 7 9700X。
- 实际 RAM 仍为单条16GB DDR5-6000，系统可用容量口径约15.11GiB，尚未升级32GB。
- RTX 5060 Ti 16GB，NVIDIA驱动621.01，总显存16311MiB。
- 原项目没有frontend，也没有聊天表或历史API；已有SQLite基础版本1及GPU管理/诊断推理。
- Git main尚无提交，工程文件未跟踪。变更前完整复制83个非忽略工程文件，并记录SHA-256：`E:\NovaAILab\backups\nova-v2-20261101-134933`。没有自动提交、推送或删除原文件。
- 初始仅有`qwen3.5:4b`。只补拉9B，下载前查询tags，未重复下载4B。实际9B本地元数据大小6550825550字节，4B为3324173934字节；它们是推理量化权重，不是BF16训练权重。

## 启动与停止

本机已安装依赖并构建后，在PowerShell执行：

```powershell
Set-Location 'C:\Users\Administrator\Documents\Codex\nova'
.\.venv\Scripts\python.exe -B harness\run_web.py
```

打开 `http://127.0.0.1:3000`。脚本启动本机8000的FastAPI与3000的Next，生成只在进程内共享的随机令牌；不写`.env`，不向浏览器返回令牌。默认启用自动游戏检测、独立显存压力保护和180秒空闲卸载，SQLite使用`data/nova.sqlite3`。原`.env`如存在，仍由后端按已有类型配置读取；不要将密钥写入源码。

运行窗口输入回车或按Ctrl+C，脚本通过标准输入请求两个自有服务正常关闭，不强制杀进程。已有推理可能需要完成和保存后再结束。Ollama服务保留；前端关闭后模型的Ollama keep_alive仍有180秒上限。日志在忽略的`logs/web-时间/`下。端口已占用时脚本拒绝启动，可显式选择`--backend-port`和`--frontend-port`。

开发前端可加`--dev`。`--data-dir`仅供明确选择不同实例/验收目录，默认继续使用原data目录，不自动迁移其他数据库。

新检出或重建依赖：

```powershell
# 后端沿用原uv锁定环境，详见README原Windows安装说明。
$env:Path = "$env:LOCALAPPDATA\Nova\tools\uv-0.13.6;$env:Path"
uv sync --locked

# 便携Node不修改系统PATH，只在当前终端使用。
$env:Path = 'E:\NovaAILab\runtime\node-v24.22.2-win-x64;' + $env:Path
Set-Location .\frontend
npm ci --no-audit --no-fund
npm run build
Set-Location ..
.\.venv\Scripts\python.exe -B harness\run_web.py
```

本项目只默认监听回环地址。手机尺寸响应式测试不等于实体手机经局域网访问；开放局域网/公网前应另行设计认证、TLS和访问范围，不能简单暴露当前私有代理。

## 数据流与接口

`浏览器 → Next同源代理 → FastAPI → GPU Manager → Ollama`。Next不是另一个AI后端，不能直接访问Ollama或调用云端模型；GPU管理令牌和DeepSeek密钥均不出服务端。代理只允许固定路径/方法，检查Host和Origin，限制请求体，不转发浏览器认证头或Origin。

| FastAPI接口 | 用途 |
| --- | --- |
| GET `/api/v1/models` | 只读注册表与安装/云端可用状态，不加载或下载 |
| GET/POST `/api/v1/threads` | 会话列表与创建 |
| GET/PATCH/DELETE `/api/v1/threads/{id}` | 历史/改名/显式删除；活动会话拒绝删除或改名 |
| POST `/api/v1/chat` | 服务端权威历史上下文，幂等请求，SSE流 |
| 原`/api/gpu/*`、`/health` | 保留原控制器、CLI约束与健康接口 |

浏览器使用`/api/nova/*`同源路径。SSE为`data: JSON\n\n`，type依次为meta、delta、done，出错为error。它不是AI SDK数据流协议。官方LocalRuntime的ChatModelAdapter负责UTF-8分块、SSE分帧和累积文本，RemoteThreadListAdapter管理会话，ThreadHistoryAdapter读取历史；append不重复写入后端已保存消息。

SQLite在同一文件中新增`nova_chat_schema/nova_threads/nova_messages/nova_chat_runs`，基础user_version仍为1，聊天扩展版本单独为1。首次新增表或恢复pending前使用SQLite backup API备份，失败则不改数据；未知或冲突结构拒绝覆盖。没有迁移到云数据库。未完成回复保留error/interrupted状态，界面不将其标为完成；重试使用稳定请求ID防重复。

聊天历史完整保留；送给模型的是有界最近上下文（最多20条历史并限制文字量），不是全部历史无限送入4096上下文。初版不提供分支、编辑后重生成、工具调用或附件。

## 模型与GPU策略

注册9B主力、4B轻量和DeepSeek三个选项。显存预检初始要求分别为9500MiB/5500MiB余量，包含保守预留；已驻留目标至少保留512MiB。它们是准入估计，不是模型显存占用承诺。遥测未知/显存不足明确拒绝，不通过合并多卡容量伪装够用。

本地流式请求仍由原GpuManager登记active、持久化安全状态；模型切换串行执行原卸载/`/api/ps`复核，不遗留人工GAMING。预检期间游戏保护到达会阻止加载。浏览器停止显示/断开不等于停止GPU，后台安全完成并保存后才释放active，游戏切换会等待真实结束，超时继续锁定。

Ollama使用4096上下文、think=false、小规模回复上限和keep_alive=180。独立空闲监测在最后受管请求结束180秒后经原卸载路径复核，保留AUTO，不因释放显存立即重载。卸载失败保留保护。外部直接请求11434仍可绕过Nova，此边界未被前端消除；训练不受自动强杀或暂停。

DeepSeek默认不可用。需要用户在本机配置`NOVA_CLOUD_ENABLED=true`和`DEEPSEEK_API_KEY`，并在界面明确同意可能计费后自行选择；没有自动收费回退。真实付费请求不在本轮验收中，传输与授权由模拟测试覆盖。

## 官方来源、许可证与依赖

先研究[assistant-ui仓库](https://github.com/assistant-ui/assistant-ui/tree/ff654d8156680b839774296ef644b57ab1078425)的README、MIT许可证及实现，核对[LocalRuntime](https://www.assistant-ui.com/docs/runtimes/custom/local-runtime)和[线程/历史](https://www.assistant-ui.com/docs/runtimes/concepts/threads)。保留`frontend/LICENSE.assistant-ui`与`THIRD_PARTY_NOTICES.md`。

实际先执行`npx --yes assistant-ui@0.0.127 create frontend --template minimal --use-npm --skip-install --no-skills`，CLI在GitHub模板下载阶段超时。保留失败日志，再按官方最小模板创建所需结构并替换默认OpenAI路由；没有冒称CLI成功，也未留下默认云模型后端。

Node24.22.2/npm11.19.1便携安装，官方ZIP SHA256为`ca1bff5d8ead13c9f2ea404a176db46bd430704d5b5113734049fa365e7799e2`。前端锁定Next16.4.3、React19.3.1、TypeScript7.0.2、assistant-ui/react0.15.32、react-markdown0.14.24；其他包以package-lock为准。Python3.14.10/uv锁文件未换栈，无新增Python依赖，不修改PyTorch/Unsloth环境。

## 验收与证据

实际执行命令、实机结果和限制见 [阶段验收](ACCEPTANCE.md) 及 [构建记录](../build-log.md)。证据根目录：`E:\NovaAILab\logs\nova-v2-20261101-135100`，包含CLI失败日志、9B下载进度、pytest输出及独立实机报告。验收期间不写用户聊天、不启动游戏、不调用付费云端。
