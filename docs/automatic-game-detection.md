# 无白名单自动游戏检测与显存压力保护

2026-11-01，Asia/Shanghai。保留原 GPU Manager、鉴权、手动 GAMING 和卸载状态机；新增自动信号源，不安装参考项目、驱动、追踪服务或 Python 依赖。代码/合成回归及本机采集/接口验收已通过；CS2实际自动识别、失焦保持、门禁及退出恢复也通过，见[实机记录](cs2-live-acceptance.md)。**实际最小化、游戏触发带载卸载、FPS和《红色沙漠》仍待补充，不标为全部完成。**

## 最小改动与参考研究

先研究 README、许可证、相关代码并报告方案，再独立实现采集/索引/分类，最后接入现有控制器。固定版本与具体借鉴、局限记录在：

| 项目 | 核实许可 | 研究与处理 |
| --- | --- | --- |
| DisplayPal | MIT | GameConfigStore、Steam/Epic、前台及渲染库分层；独立实现，避免只搜顶层EXE及PID复用问题 |
| GameDetect | GPL-3.0；API自动识别NOASSERTION | 研究平台扫描与匹配顺序，不移植其GPL实现，不递归扫描所有EXE |
| windows-vram-monitor | README称MIT，但未找到LICENSE/完整版权声明 | 仅参考每PID显存/JSON思路，不复制源码或执行策略绕过 |
| VGPU-Mon | MIT | 借鉴持久PDH/DXGI结构；按完整LUID修正多GPU归类，不把进程预算当全局预算 |
| PresentMon | MIT | 研究ETW呈帧、权限和成本；不默认安装或运行，仅保留可选增强设计 |

源码与许可证固定链接见 [目录研究](auto-game-catalog-research.md)、[Windows GPU研究](windows-gpu-signals-research.md)、[PresentMon研究](presentmon-research.md)。未复制参考项目的实现源码或引入它们的依赖。

## 检测算法

1. **自动索引**：只读当前用户GameConfigStore、Steam注册安装路径、libraryfolders/appmanifest、Epic安装清单。精确启动路径和安装根分别标记；根内EXE只是候选。清单大小/数量受限，不递归扫描整个磁盘。索引启动时刷新，之后缓存最多5分钟。
2. **Windows信号**：ToolHelp进程快照、创建时间和完整镜像路径；前台PID、可见/最小化及同DPI坐标的窗口/显示器边界；只对前台/库候选查询渲染DLL，不读标题或命令行。无法读取的属性明确未知。
3. **GPU信号**：持久PDH英文counter，按完整LUID、physical node、PID读取3D/Compute/Copy/视频引擎和独显用量；DXGI枚举核显/独显/软件适配器。引擎使用率保守取max，不相加成虚假的整机百分比。首样预热不当作0。
4. **先排除**：Nova自身及子进程、推理/训练解释器、浏览器/播放器、桌面、启动器、壁纸、常见开发与渲染UI、索引里的工具/redistributable。排除名只作负向过滤，目标游戏不使用硬编码EXE前提。
5. **连续确认**：默认3个有效采样。精确路径须有渲染及前台/3D证据；Steam安装根须前台、渲染及自身3D；未知程序严格要求前台可见、非最小化、全屏/无边框、渲染模块及持续3D（且不被视频解码活动主导）。分数是规则等级，不是标定概率。
6. **身份保持**：确认后跟踪PID+创建时间+路径。切屏/最小化不解除；PID确实退出才进入原60秒退出窗口。PID复用重置，身份暂时失读视为未知并保持保护。
7. **降级**：计数器不可用时，未知程序不猜测；精确路径可用前台+渲染证据降级确认。进程快照不可靠沿用原安全锁定。手动GAMING仍可用，自动恢复不解除手动或故障来源。

没有以GPU总体利用率判游戏，没有启动收费API。按5秒采样时，3样本确认通常需要约10–15秒，采集/排空还会增加延迟；不是游戏启动瞬间的操作系统隔离。检测到确认结果后复用原门禁→排空→卸载→复核，退出后恢复AUTO不预载。

## 独立显存压力保护

无需“正在玩游戏”标签也可工作。只在**同一NVIDIA LUID**下组合：物理独显余量不足、其他PID的独显需求、实际引擎活动，并持续两个样本才触发。不会仅凭固定阈值杀进程。

它先阻止新的本地加载；已有受管推理继续完成，下次空闲检查才请求原卸载接口。不会暂停/终止训练或关闭其他GPU应用。卸载事件记录`reason=memory_pressure`与前后状态。

解除需要连续20秒健康余量，且预留刚卸载模型重新加载的空间，减少“刚释放就重载”的循环。计数器失效不能清掉既有压力保护。压力和手动/游戏来源独立；清理压力不会解除手动GAMING。

DXGI的QueryVideoMemoryInfo是**采样进程自身**预算，不是Ollama或游戏预算；状态仅将其列为`current_process`诊断元数据，全局预算为未知。压力决策采用相同适配器口径的DXGI容量和PDH独显使用量，不跨口径减nvidia-smi数据，不伪造全局Windows预算。

## 启用：不需要EXE名单

默认保持兼容，不自动覆盖用户`.env`或注册自启。启用时在Nova项目的同一PowerShell设置：

```powershell
$env:NOVA_GPU_ENABLED = 'true'
$env:NOVA_GPU_TOKEN = & '.\.venv\Scripts\python.exe' -c 'import secrets; print(secrets.token_urlsafe(32))'
$env:NOVA_GPU_AUTO_GAME_ENABLED = 'true'
$env:NOVA_GPU_GAME_DETECTION_ENABLED = 'false'
$env:NOVA_GPU_MEMORY_PRESSURE_ENABLED = 'true'
$env:NOVA_GPU_GAME_POLL_SECONDS = '5'
```

随后按 [原管理说明](gpu-resource-manager.md) 从此环境启动单worker后端并使用CLI。旧白名单模式仍可选，但不能与自动模式同时开启；自动模式不要求`NOVA_GPU_GAME_PROCESSES`有值。只想用压力保护时，可关闭自动游戏开关、保留压力开关。

GET `/api/gpu/status`新增自动策略、索引聚合、候选/确认的置信与信号、降级原因、采样耗时和`memory_pressure`状态。不会暴露所有进程路径、窗口标题或启动参数。原模式接口/令牌和本机限制保留。

## 验收与实际结果

- 全量`pytest -ra`：411通过，0失败、0跳过，5.77秒。覆盖Steam/Epic/注册索引、未知全屏、浏览器/视频/壁纸、AI推理/训练计算、最小化/失焦/PID复用、退出恢复、counter降级、压力组合与空闲卸载等。
- 新模式开启后的真实Ollama/CLI/HTTP回合：17项通过；实际模型加载、手动GAMING卸载、拦截、重启保持和按需恢复通过。该回合没有冒充真实游戏运行或真实显存耗尽测试。
- 本机发现GameConfigStore精确路径3个、Steam清单8个（含目标两游戏）、工具2个；未发现Epic清单，Epic解析由合成清单测试覆盖。
- 6次只读实机采集：冷采样281.677ms，热采样中位数66.463ms；25.812秒内CPU0.75秒（单核口径2.906%），工作集约26.48→35.43MiB。这是采集进程测量，不是FPS测试或整个Nova后端性能保证。
- Ruff静态/格式和工程自检、锁定依赖检查通过；没有新增依赖或数据库schema改动。

证据根目录：`E:\NovaAILab\logs\gpu-auto-detection-20261101-043203-813`，包含`native-probe.json`、`live-automatic/report.json`、`pytest-final.txt`。显式只读采样命令：

```powershell
.\.venv\Scripts\python.exe -B harness\probe_auto_games.py --samples 6 --interval 5 --output 'E:\NovaAILab\logs\一个尚不存在的采样.json'
```

真实GPU管理回合用`harness/verify_automatic_live.py --output-dir <新目录>`。它使用独立端口/数据库和进程内随机令牌，结束时卸载模型并正常关闭验收后端。不会写入用户配置或把测试服务注册为自启。

## 尚未完成的实机验收

用户自行启动CS2后的连续54样本会话已完成：38次确认、生成/wake423、健康200、切回桌面持续保护；退出缺席61.215秒恢复AUTO且不预载，后续按需回复和清理成功。原报告和独立复核见[CS2实机验收](cs2-live-acceptance.md)。验收服务已关闭，不等于启用了常驻生产保护。

CS2实际最小化、原有模型驻留时游戏触发的卸载、FPS对照尚未实证；《红色沙漠》仍待用户自行启动。不自动启动、下载或登录游戏，不在游戏中为验收强制加载模型。

本机没有人为填满显存；压力触发使用合成遥测回归。真实训练没有被启动或中断，不把合成Compute测试称作训练框架的实机验收。

## 已知误判、漏判及下一步

- 未收录的全屏CAD/自定义3D壳仍可能满足未知规则而误判；常见应用已排除，但规则不可能给出普遍的“游戏证明”。
- 反作弊/权限保护、渲染DLL无法枚举、远程桌面或counter失效可能漏判新游戏；已有确认保护保守保留，手动模式备用。
- 启动器与主进程切换、极低3D负载、窗口化未知游戏、非常规/网络库路径、GOG/EA/Xbox专属索引尚不完整；缓存刷新存在延迟。
- 外部直接访问11434仍能绕过Nova门禁；外部训练未被接管。本轮没有安全暂停训练、恢复训练、vLLM Sleep或全系统GPU隔离。
- 优先完成两款真实游戏校准与FPS/CPU测量；再考虑可选误报反馈、其他平台索引、针对单PID限时PresentMon增强。Present事件也不等于游戏，权限不足时不自动提升权限，不默认开ETW服务。
