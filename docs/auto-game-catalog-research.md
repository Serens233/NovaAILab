# 自动游戏目录：研究与独立实现记录

日期：2026-11-01（Asia/Shanghai）。本文仅涵盖此次分工研究的 DisplayPal 和 GameDetect；其他三个参考项目由总体方案另行记录。仅阅读源文件和安装元数据，没有安装参考项目、运行其安装器或执行其构建脚本。

## 参考版本与许可证

| 项目 | 本次读取的固定 commit | 许可证 |
| --- | --- | --- |
| DisplayPal | `5f2402e3c63a45a4fdb551a11143df3fee2eea78` | LICENSE 明确为 MIT，版权行是 2026 Ben Kirtland（MorlachAU） |
| GameDetect | `4f66952a51cb0e576c249b9da7bc0abcdcaa1e83` | README 声明 GPL-3.0，LICENSE 包含 GNU GPL v3 全文；GitHub API 的自动识别结果是 NOASSERTION，不能把这个结果当作没有许可证 |

Nova 本次独立编写有限元数据解析与路径索引，**未复制这两个项目的实现源码，也未引入其依赖**。没有将 GPL 实现直接改写成 Nova 模块。若后续实际复制 MIT 项目的实质源码，应保留其版权和许可文本；若要采用 GPL 实现，应先另行评估分发和许可要求，不能根据当前研究记录自动作出授权。

固定来源：

- [DisplayPal README](https://github.com/MorlachAU/DisplayPal/blob/5f2402e3c63a45a4fdb551a11143df3fee2eea78/README.md)、[LICENSE](https://github.com/MorlachAU/DisplayPal/blob/5f2402e3c63a45a4fdb551a11143df3fee2eea78/LICENSE)、[appdetect.py](https://github.com/MorlachAU/DisplayPal/blob/5f2402e3c63a45a4fdb551a11143df3fee2eea78/appdetect.py)。
- [GameDetect README](https://github.com/opkelde/GameDetect/blob/4f66952a51cb0e576c249b9da7bc0abcdcaa1e83/README.md)、[LICENSE](https://github.com/opkelde/GameDetect/blob/4f66952a51cb0e576c249b9da7bc0abcdcaa1e83/LICENSE)、[LauncherScanner.cs](https://github.com/opkelde/GameDetect/blob/4f66952a51cb0e576c249b9da7bc0abcdcaa1e83/src/GameDetect/Detection/LauncherScanner.cs)、[ProcessMonitor.cs](https://github.com/opkelde/GameDetect/blob/4f66952a51cb0e576c249b9da7bc0abcdcaa1e83/src/GameDetect/Detection/ProcessMonitor.cs)、[GameMatcher.cs](https://github.com/opkelde/GameDetect/blob/4f66952a51cb0e576c249b9da7bc0abcdcaa1e83/src/GameDetect/Detection/GameMatcher.cs)、[FullscreenDetector.cs](https://github.com/opkelde/GameDetect/blob/4f66952a51cb0e576c249b9da7bc0abcdcaa1e83/src/GameDetect/Detection/FullscreenDetector.cs)。

## DisplayPal 的有用设计和限制

README 将游戏识别组织为游戏注册信息、游戏平台目录、前台全屏及渲染库等层次。`appdetect.py` 从当前用户的 `System\GameConfigStore\Children` 读取 `MatchedExeFullPath`；Steam 从注册安装路径出发解析 `libraryfolders.vdf` 和安装 manifest；Epic 读取 `.item` 中的安装位置和启动程序。它还跟踪已识别进程，使切换窗口不立即退出游戏状态。

不能直接把这些检查当作可靠的最终游戏判定：其 Steam 实现仅收集安装目录顶层 EXE，可能漏掉嵌套程序并收入助手或卸载程序；渲染 DLL 和无边框全屏也不是游戏独有的能力。原实现的 PID 保持没有单独解决 PID 复用问题。

Nova 借鉴多来源索引和多层判定的组织方式，采用完整路径及安装边界证据；不会因为某程序位于 Steam 目录，或加载了 DirectX DLL，就直接宣布正在游戏。

## GameDetect 的有用设计和限制

GameDetect 是 .NET Windows 桌面项目，通过 GameFinder 的 Steam、GOG、Epic、EA 和 Xbox 处理器寻找安装目录。其扫描器可搜索至四层目录、加载忽略规则和自定义映射，并选择名称相似或文件较大的主程序，同时仍将全部过滤后的 EXE 名称加入映射。

进程监控主要保留带主窗口的候选；匹配多个程序时优先前台窗口，否则选择较晚启动的候选。全屏检测使用 Windows 通知状态，另有窗口覆盖屏幕尺寸的回退。它没有提供足以区分 CUDA、视频解码和游戏渲染的完整 GPU 引擎判定。

Nova 不采用 basename 全局映射、最大 EXE 或广泛递归文件扫描作为游戏证据，避免不同安装目录的同名程序、启动器、崩溃上报器及 Steam 工具被误认。也不安装该项目的 MQTT、Windows 服务、WPF 或 GameFinder 依赖。

## 本轮 GameCatalog 的职责

文件是 `src/nova/gpu/catalog.py`：

- `GameCatalog().refresh()` 读取少量已知注册位置和 manifest，更新内存索引；`.summary` 仅输出来源计数、工具计数及固定错误代码，不包含完整安装路径、账号或启动命令。
- GameConfigStore 仅纳入当前确实存在的本地完整 EXE 路径。
- Steam 自动展开库配置和 `appmanifest_*.acf`，索引安装根，不遍历全部 EXE。安装根匹配返回 `exact=false`，后续分类器仍须要求渲染/窗口/活动证据。
- Epic 仅将安装根内的 `LaunchExecutable` 作为精确候选，不索引同目录所有程序，也不使用或输出 `LaunchCommand`。
- `match(path)` 返回来源、ID、显示名称、精确性与 `game/tool` 类别。工具/服务器/壁纸/运行库元数据和常见辅助目录提供否定证据，不能被另一条精确记录重新覆盖为游戏。
- 拒绝 UNC、设备路径、父级跳转和安装根以外的启动路径；限制单文件 1MiB、manifest 512 份、库根 32 个、GameConfigStore 条目 2048 个及 VDF 嵌套深度。
- 不读进程命令行、不扫描整个磁盘、不启动游戏、不访问云端或自动下载。

注入参数用于离线夹具：`registry_roots` 接收已发现的 `MatchedExeFullPath` 样本；`steam_paths` 接收 Steam 或库根；`epic_manifest_dirs` 接收 `.item` 目录。各参数为 `None` 时才执行对应系统发现，传空列表可禁用该真实来源。

## 实际检查结果

本机只读发现三个 Steam 库和八份安装 manifest，包含本次指定的两个游戏案例；同时发现 Wallpaper Engine 和 Steamworks 运行库元数据，应作为工具排除。GameConfigStore 有三个当前存在的完整 EXE 路径。未发现 Epic 的对应注册位置或默认 manifest 目录。

实际 `GameCatalog().refresh()` 返回：GameConfigStore 3 条、Steam 8 条、Epic 0 条；精确项 3 条，安装根 8 个，工具项 2 个，错误列表为空。单次刷新采样为 **12.689ms**，只代表本机这一次元数据刷新，不是检测循环或所有游戏库规模的性能保证。

实际执行：`.venv\Scripts\python.exe -B -m pytest tests\test_gpu_catalog.py -q` 为 **36 passed、0 failed、0 skipped**；Ruff 静态和格式检查通过。测试使用合成安装数据，覆盖精确/嵌套路径、目录边界、工具优先、非法路径、坏格式、文件大小与条数/根目录上限及索引刷新，不调用真实游戏。

发现游戏安装和注册信息不等于验证该游戏的运行兼容性。本模块尚不负责前台窗口、GPU 计数器、置信度或卸载动作；这些由上层信号采集、分类器和既有 GPU 管理器完成。

## 已知范围

本轮只实现 GameConfigStore、Steam 和 Epic。GOG、EA、Xbox、其他启动器及完全独立游戏需要其他索引源或上层未知程序判定；不把未扫描的平台说成已经支持。Steam 的 ACF 并非可靠的“这是游戏而不是工具”类型数据库，因此工具类别和多信号判定仍然必要。损坏或无权限读取的条目会记录匿名错误并跳过，不扩大扫描范围来补猜。
