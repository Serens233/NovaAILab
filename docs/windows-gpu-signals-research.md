# Windows GPU 信号研究与最小采样器

日期：2026-11-01（Asia/Shanghai）。本记录只覆盖两个显存/引擎监控参考项目，以及本轮独立编写的 PDH/DXGI 采样器。没有安装或运行参考项目的程序、安装脚本或依赖。

## windows-vram-monitor

- 仓库：[kasidid/windows-vram-monitor](https://github.com/kasidid/windows-vram-monitor)。核对提交：`4dba5d836433dc7afe4c4a88e40a44c3aa0bf43a`。
- 已研究该提交的 README 和 `vram_monitor.py`。README 宣称 MIT，但仓库树没有独立 LICENSE 或完整版权许可声明，GitHub API 也未识别许可证。因此只参考设计，不复制其源码；不能把“页面写 MIT”扩张为已核实完整许可文本。
- 设计启发：Windows 消费显卡的每进程显存不能依赖 nvidia-smi 的 WDDM N/A；可用 GPU Process Memory/Adapter Memory 性能计数器，再按 PID 和适配器标识生成 JSON。
- 局限：该实现通过 PowerShell 每次查询，使用英文 Get-Counter 路径、LUID 低32位和友好名称合并；本轮不采用其执行策略绕过、低位 LUID 合并和每次创建 PowerShell 进程的方式。

原始代码：[固定提交的 vram_monitor.py](https://github.com/kasidid/windows-vram-monitor/blob/4dba5d836433dc7afe4c4a88e40a44c3aa0bf43a/vram_monitor.py)。

## VGPU-Mon

- 仓库：[xptea/VGPU-Mon](https://github.com/xptea/VGPU-Mon)。核对提交：`5d51d3956c19b07ad92f29452729ff4f43e2f472`。
- 已研究 README、独立 [MIT 许可证](https://github.com/xptea/VGPU-Mon/blob/5d51d3956c19b07ad92f29452729ff4f43e2f472/LICENSE) 和 `pdh_gpu.c`、`dxgi_gpu.c`、`d3dkmt_gpu.c`。许可证包含2026年贡献者版权声明；若以后复制实质代码，须保留对应许可及版权声明。本轮没有复制实现。
- 设计启发：长期保留 PDH query，先采一帧，再用后续巡检计算引擎利用率；3D、Compute、Copy、VideoDecode、VideoEncode 分开。DXGI 提供适配器厂商、独立显存和完整 LUID；直接进程查询失败应显式未知。
- 局限：其 PDH 代码按 `phys_N` 筛选 GPU，未同时比较完整 LUID；双显卡都可能有 `phys_0`，不能照搬这种分组。其 DXGI 回退中使用 QueryVideoMemoryInfo 的值时还必须尊重进程作用域，不能直接解释为整卡总使用或全局可用预算。

源码：[PDH](https://github.com/xptea/VGPU-Mon/blob/5d51d3956c19b07ad92f29452729ff4f43e2f472/src/pdh_gpu.c)、[DXGI](https://github.com/xptea/VGPU-Mon/blob/5d51d3956c19b07ad92f29452729ff4f43e2f472/src/dxgi_gpu.c)、[D3DKMT](https://github.com/xptea/VGPU-Mon/blob/5d51d3956c19b07ad92f29452729ff4f43e2f472/src/d3dkmt_gpu.c)。

## 本轮实现

`src/nova/gpu/perf_counters.py` 使用标准库 ctypes 独立实现官方 ABI，不依赖两个参考项目。模块导入不打开 query；`WindowsGpuCounters.sample()` 是同步接口，供外层 `asyncio.to_thread` 调用；`close()` 幂等释放句柄。

- DLL 只从 System32 加载。
- 采用 `PdhAddEnglishCounterW`，避免中文 Windows 的本地化计数器路径问题。
- 一个长期 query 同时采集 GPU Engine Utilization Percentage、GPU Process Memory Dedicated Usage、GPU Adapter Memory Dedicated Usage。
- 首次样本为 `warming_up=true`，不把缺少速率基线当作0%活动；没有在每次 sample 内部 sleep。
- 完整 LUID 的高低32位及物理节点用于合并；重复实例取最大值，独立物理节点显存求和。引擎活动按同类最大值表达，不把多个引擎和多个GPU百分比累加到100%以上。
- 每进程既有汇总字段，也有 `per_adapter[LUID]`，防止把核显显存需求误算到 NVIDIA。软件适配器明确识别并过滤，未知 LUID 则报告映射不可用。
- Counter 不可用、坏数据或无法映射时返回固定错误码，不回显系统原文，不伪造0；采样结果不含进程命令行、窗口标题或私密路径。

### 数据接口

返回 `available`、`warming_up`、`sample_duration_ms`、`errors`，以及：

- `processes[pid]`：`engines`、`dedicated_bytes`、适配器 LUID 列表和 `per_adapter`。
- `adapters[]`：完整 LUID、厂商ID、名称、DXGI独立显存总量、PDH独立显存使用量。
- `budget_bytes` 始终为 `null`，表示本实现未取得全局显存预算。
- `probe_budget_bytes` 和 `probe_current_usage` 来自 QueryVideoMemoryInfo，`budget_scope=current_process`，仅描述采样进程自己；**不代表游戏、Ollama或整卡预算**。查询失败时为 `null`/`unknown`。

独立压力策略应使用同一 NVIDIA LUID 下的实际余量、其他进程显存及活动、连续采样来判断。DXGI总量与 nvidia-smi 的总量口径可能不同，不应混合相减，也不能仅凭采样进程的预算判断整卡压力。

## 实机只读探查结果

本机 PDH 与 DXGI 均可用，识别到 NVIDIA 独显和 AMD 核显。一次探查中 GPU Engine 有516个有效实例，进程显存45个实例；它们不是516个进程，也不等于游戏进程数量。

`GPU Adapter Memory\Dedicated Limit` 在本机不存在，故没有猜测“预算计数器”。DXGI能够读取当前采样进程的预算，而该进程使用量为0，不可据此宣称其他应用未占显存。

初次模块实测：首样约254ms、预热样本标志为true；间隔1秒后的第二次采样约3.4ms、预热标志为false；两个硬件适配器、28个有引擎/显存信息的PID，错误列表为空。这只是一次采样成本记录，不是整个检测服务或真实游戏的性能保证；单元测试与最终综合验收另行记录。

收尾时用最终代码另外采5个样本（外部探查程序间隔0.2秒，模块内部无sleep）：首样218.294ms；后续为4.255、2.865、2.891、2.948ms，全部available=true，只有首样warming_up=true，错误均为空。该模块26项离线测试通过、0失败、0跳过，Ruff静态和格式检查均通过。

本次探查只输出匿名聚合及适配器指标，没有打印全部进程路径、启动命令或游戏账号信息。未更改驱动、启用ETW服务、启动游戏或终止进程。

## 官方 ABI 依据

- [PdhAddEnglishCounterW](https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhaddenglishcounterw)：语言中立路径。
- [PdhGetFormattedCounterArrayW](https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhgetformattedcounterarrayw)：通配实例数组、缓冲区大小与格式化状态。
- [IDXGIFactory1::EnumAdapters1](https://learn.microsoft.com/en-us/windows/win32/api/dxgi/nf-dxgi-idxgifactory1-enumadapters1)：适配器枚举。
- [IDXGIAdapter3::QueryVideoMemoryInfo](https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_4/nf-dxgi1_4-idxgiadapter3-queryvideomemoryinfo) 与 [DXGI_QUERY_VIDEO_MEMORY_INFO](https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_4/ns-dxgi1_4-dxgi_query_video_memory_info)：明确预算和用量属于调用进程。

本模块不提供帧呈现事件，不把3D活动等同于游戏；识别策略必须另结合库索引、前台窗口、渲染证据和排除规则。
