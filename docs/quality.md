# Nova V3.0 质量与阶段验收规范

## 判定与证据

当前规范适用于 V3.0 Phase 1 模型、聊天与资源基础，并保留后续阶段的通用隐私和质量约束。旧后端初始化结果只作历史证据，见 [开发记录](progress.md) 和 [阶段验收](ACCEPTANCE.md)。

每次记录：检查范围、Git 提交、实际命令、退出码、通过/失败/跳过数量、环境与限制。计划、代码存在、pytest 版本命令和历史结果均不表示本次通过；pytest 退出码 5 是没有收集到用例。失败先诊断、修复再复验，不删除安全断言或用 skip/xfail 隐藏关键失败。

区分三种结论：已执行并通过、已执行但失败、未执行/待验收并说明原因。真实游戏、模型、GPU、浏览器、手机和收费云端的证据分别归类，不能由替身测试代替。通过当前测试只能支持已覆盖场景，不承诺未来扩展零风险。

## 锁定环境与自动化检查

从仓库根目录的 PowerShell 逐条执行；命令示例不是执行结果。项目使用独立 `.venv`，精确解释器与包版本以 `.python-version`、`pyproject.toml`、`uv.lock` 为准，不手工混用 pip 或无关升级。

```powershell
uv lock --check
uv sync --locked
uv run --locked python -B -m pytest -p no:cacheprovider -ra
uv run --locked ruff check --no-cache .
uv run --locked ruff format --check --no-cache .
uv run --locked python -B harness/check_project.py
git diff --check
git status --short --branch
```

前端在 `frontend/` 执行，使用已锁定 Node/npm 环境；版本及安装方式记录到本次验收，不更换锁文件来回避失败：

```powershell
npm ci --ignore-scripts --offline
npm run test
npm run typecheck
npm run lint
npm run build
```

离线缓存不全时先记录原因，再说明可信来源、安装范围及空间需求后安装原锁定包。安装脚本默认不运行；确需执行时单独检查。Windows 文本按仓库 `.gitattributes` 使用 UTF-8/LF，不以全局 Git 设置替代项目约定。

## Phase 1 必需行为

| 范围 | 必需验证 |
| --- | --- |
| 生命周期与健康 | 启动失败不就绪；`/health` 正常与故障响应安全；数据库、后台任务和实例锁按真实完成情况释放 |
| 配置、错误与日志 | `.env`/环境覆盖及类型边界；固定错误码、请求标识、超时；客户端和日志不含密钥、堆栈或私人正文 |
| 模型与传输 | 实际标识、安装缺失、未知策略拒绝；上下文、think配置、思考/正文隔离、UTF-8分块、结束确认、慢响应与取消 |
| GPU 调度 | GAMING/检测/压力/故障来源独立；准入在加载前；并发单飞；超时补偿、取消、卸载失败、关机和重启保守恢复；上游线程未结束不能释放实例租约 |
| 训练与云端 | 协作租约互斥、首次写盘失败回滚、游戏不强杀或自动续训；云端默认关闭、逐请求同意且不自动付费回退 |
| SQLite 与历史 | 临时库事务/关闭/幂等/未知结构拒绝；完成请求只读重放、内容模型冲突、部分回复状态、旧数据与非破坏性演进 |
| 前端与代理 | 模型切换、历史与停止交互；SSE和清晰错误；固定代理白名单、同源/Host/体积限制、服务端凭据隔离和断连回收 |

默认 pytest 使用合成 Provider、临时数据库与状态，不读取个人 `.env`、不发真实模型或收费请求。Windows 失败测试使用可重复替身或非法路径，不依赖管理员环境下不可靠的 chmod 行为。

## 真实本机验收

下列脚本会加载和卸载已有模型，必须先确认无游戏/训练/其他受保护任务争用，不接管他人的模型会话，不下载模型。输出目录用仓库外新目录，令牌仅保留在进程内，所有结果均需检查脚本实际报告及正常退出。

```powershell
$acceptanceRoot = Join-Path $env:TEMP ('nova-acceptance-' + [guid]::NewGuid())
# 启用自动检测/压力守护的真实Ollama、鉴权及手动模式回合。
uv run --locked python -B harness/verify_automatic_live.py --output-dir (Join-Path $acceptanceRoot 'automatic')
# 流式聊天、模型切换、历史与真实180秒空闲卸载。
uv run --locked python -B harness/verify_v2_live.py --output-dir (Join-Path $acceptanceRoot 'chat-idle')
```

- 空闲卸载观察真实连续至少180秒无受管请求，再结合 `/api/ps`、GPU 进程及显存记录判定；不缩短配置冒充三分钟实测，不以单一显存数值判断释放。
- 真实游戏进程切换必须由用户自行启动并配合最小化/失焦/退出；验证检测保持、退出延迟和按需恢复，不自动启动或结束游戏。用户本轮选择不启动，保留待验收。
- 每个待用模型记录实际标识、量化、RAM/VRAM、加载/首可见Token延迟与生成速度；无数据用未知，不能以4B/9B代替27B性能。27B未通过安全资源门槛时禁止强行加载，记录准入拒绝与待验收。
- 浏览器视觉、手机真机及收费云端另行授权和记录；本地合成HTTP上游只能证明代理与协议行为，不能证明真实模型质量。

## 打包、数据兼容与精简

在仓库外构建并检查 wheel/sdist 文件清单及隔离环境导入/启动：

```powershell
$packageDir = Join-Path $env:TEMP ('nova-build-' + [guid]::NewGuid())
uv build --out-dir $packageDir
```

包内不得含 `.env`、凭据、私人聊天、SQLite、日志、模型、训练输出或虚拟环境；不以“构建成功”替代内容检查。

精简前检查全仓引用、公开接口及兼容用途；记录删除/合并清单与理由，比较前后相关用例和行为。保留许可证、历史验收证据、安全回归及仍有用途的兼容入口。数据库迁移不得混入清理，用户数据不作为冗余删除。

`.gitignore` 和工程自检只是辅助：提交前审阅暂存差异、实际 Git 文件及打包清单，确认示例密钥留空，浏览器产物不含服务端凭据。自检不扫描所有任意秘密，泄密后需撤销并轮换，不能仅增加忽略规则。

## 后续阶段与退出

新增人格、记忆、成长、工具或渠道时按 [路线图](roadmap.md) 增加授权、来源、隔离、版本冲突、注入、撤销、失败恢复和回滚测试；只为实际需求增加依赖并记录 [技术决策](decisions.md)。不提前编写完整V3应用。

交付需同步 CURRENT_STATE、验收和构建记录，说明完成范围、测试、差异、已知限制及下一阶段入口。阻断项未解决时不标完成；未执行的实机项明确保留。阶段交付后停止，不由路线图自动开始下一阶段或发布/合并。
