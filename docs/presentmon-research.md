# PresentMon 可选增强研究

研究日期：2026-11-01，Asia/Shanghai。仅阅读公开源码和文档，没有下载发行二进制、运行ETW、安装服务/驱动、修改用户组或提升权限。

## 来源与许可证

仓库为 [GameTechDev/PresentMon](https://github.com/GameTechDev/PresentMon)。本次固定阅读的main提交为 `69dafa12b9804b1ea0a5d7595cba8eaf3c127e68`（2026-10-28）。[LICENSE.txt](https://github.com/GameTechDev/PresentMon/blob/69dafa12b9804b1ea0a5d7595cba8eaf3c127e68/LICENSE.txt) 为MIT，版权声明为Intel 2017–2024；部分实现文件另列NVIDIA版权，仍声明MIT。如以后分发其代码或实质片段，必须保留对应版权和许可。本轮只借鉴分层和证据设计，未复制该项目实现。

实际阅读了 [README](https://github.com/GameTechDev/PresentMon/blob/69dafa12b9804b1ea0a5d7595cba8eaf3c127e68/README.md)、[Console说明](https://github.com/GameTechDev/PresentMon/blob/69dafa12b9804b1ea0a5d7595cba8eaf3c127e68/README-ConsoleApplication.md)、[Service说明](https://github.com/GameTechDev/PresentMon/blob/69dafa12b9804b1ea0a5d7595cba8eaf3c127e68/README-Service.md)、[TraceConsumer接口](https://github.com/GameTechDev/PresentMon/blob/69dafa12b9804b1ea0a5d7595cba8eaf3c127e68/PresentData/PresentMonTraceConsumer.hpp) 和 [TraceSession实现](https://github.com/GameTechDev/PresentMon/blob/69dafa12b9804b1ea0a5d7595cba8eaf3c127e68/PresentData/PresentMonTraceSession.cpp)。

## 能提供什么证据

PresentData在实时ETW会话中消费DXGI、D3D9、DxgKrnl、DWM等事件，关联进程和交换链的帧呈现。Console可以按PID选择对象，限时运行，输出CSV到标准输出；指标可区分渲染与视频引擎。Service则组合帧事件、厂商硬件遥测和SDK，是额外Windows服务，不能当作轻量Python依赖顺手安装。

这些信号可以补足“已加载渲染DLL”只说明能力、不说明持续呈帧的局限。但浏览器、桌面和播放器也会呈帧，因此Present事件仍不能单独证明是游戏；必须保留非游戏排除、前台/全屏、游戏库及持续活动等组合规则。

## 权限和开销

官方README说明实时追踪需要相应ETW权限，包括Performance Log Users成员资格；非管理员对其他用户或短命进程可能拿不到完整信息。加入用户组需要管理员操作及重新登录，本轮没有执行。也不使用自动管理员重启参数。

跟踪会启用provider并处理逐帧事件，存在CPU、内存、缓冲和可选CSV输出开销；限PID不应被宣传为没有全局采集成本。源码特别避开部分DxgKrnl关键字，说明不恰当的追踪配置可能影响某些Windows版本。HAGS和OpenGL/Vulkan还会降低部分GPU时间或延迟指标的准确度。本轮未运行PresentMon，不能虚构CPU百分比、帧率影响或目标游戏开销数据。

## 可选接入方式

默认不安装、不启动PresentMon或重型追踪服务。以后若轻量采集无法区分候选，可在用户明确授权并核对签名/版本后，针对单个候选PID运行有界、限时的Console捕获，并把Present持续性作为附加证据。使用独立会话名，正常结束自己的会话，不终止其他工具的追踪，不修改驱动/用户组，不公开管理端点，不记录窗口标题、完整进程命令行或聊天正文。

权限不足、计数器或追踪不可用时，应报告降级并保留手动GAMING，不把“采集失败”当成“游戏已退出”。本轮分类器已经组合自动索引、前台窗口、渲染模块及持续3D证据；PresentMon仅为后续可选增强，不是当前默认检测前提。
