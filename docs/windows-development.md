# Windows 本地开发说明

## 当前可用环境

项目路径为 `C:\Users\Administrator\Documents\Codex\nova`。本阶段已安装用户目录中的 uv 0.13.6，并通过 uv 下载 CPython 3.14.10；项目 `.venv` 使用该解释器。开发工具精确版本由 `uv.lock` 管理。

系统的 `python` 命令仍可能指向 Microsoft Store 占位程序。使用 `uv run --locked python`，不依赖系统 Python、`py` 启动器或虚拟环境激活脚本。没有修改全局 PATH 或 PowerShell 永久执行策略。

Git 在当前 Codex 终端中可用。另开独立 PowerShell 时，先确认 `git --version`；如果找不到 Git，从 [Git 官方 Windows 下载页](https://git-scm.com/downloads/win) 安装后重新打开终端。无需更改全局 Git 用户身份即可完成本阶段检查。

## 本机开始工作

```powershell
Set-Location 'C:\Users\Administrator\Documents\Codex\nova'
$env:Path = "$env:LOCALAPPDATA\Nova\tools\uv-0.13.6;$env:Path"
git --version
uv --version
uv sync --locked
uv run --locked pytest -ra
uv run --locked python --version
```

PATH 设置只在当前会话生效。也可直接调用：

```powershell
& "$env:LOCALAPPDATA\Nova\tools\uv-0.13.6\uv.exe" --version
```

`uv sync --locked` 在项目内建立或同步 `.venv`。它会移除该环境中未声明的包，因此不要把其他项目的环境放在此目录。不得通过 pip 手工向该环境添加依赖。

## 新机器安装 uv 和 Python

参照 [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/)。本项目使用独立用户工具目录，下面命令与本机安装布局一致。目标目录若已存在，请使用已有工具，不覆盖安装。

```powershell
$novaUvDirectory = Join-Path $env:LOCALAPPDATA 'Nova\tools\uv-0.13.6'
if (Test-Path -LiteralPath $novaUvDirectory) {
    throw '工具目录已存在，请先检查并使用已有 uv。'
}
$novaInstallerPath = Join-Path ([System.IO.Path]::GetTempPath()) ('nova-uv-' + [guid]::NewGuid().ToString('N') + '.ps1')
Invoke-WebRequest -Uri 'https://astral.sh/uv/0.13.6/install.ps1' -OutFile $novaInstallerPath
Get-Content -LiteralPath $novaInstallerPath
```

检查来源和脚本后，在同一 PowerShell 中执行：

```powershell
$env:UV_UNMANAGED_INSTALL = $novaUvDirectory
$env:UV_NO_MODIFY_PATH = '1'
powershell -NoProfile -ExecutionPolicy Bypass -File $novaInstallerPath
if ($LASTEXITCODE -ne 0) { throw 'uv 安装失败。' }
$env:Path = "$novaUvDirectory;$env:Path"
uv --version
```

`ExecutionPolicy Bypass` 只作用于该安装子进程，不修改永久执行策略。安装目录不会自动加入全局 PATH，也不会接管其他 uv 安装；后续终端需重新加入本会话 PATH。

进入项目后运行：

```powershell
uv sync --locked
if ($LASTEXITCODE -ne 0) { throw '依赖同步失败，请按下文排障。' }
uv run --locked python --version
```

uv 会按 `.python-version` 查找或下载 CPython 3.14.10。uv 管理的 Python 来自 Astral 的 `python-build-standalone`，详见 [官方 Python 管理说明](https://docs.astral.sh/uv/guides/install-python/)。不需要先安装系统 Python。

本仓库已初始化 Git，不要重复初始化。本阶段没有提交；因此还不能从远程克隆。以后迁移已提交的仓库时再使用正常的克隆流程。

## 执行工程检查

以下每条命令成功时退出码应为 0；PowerShell 中可立即用 `$LASTEXITCODE` 查看。运行失败时停止验收并处理原因。

```powershell
uv lock --check
uv sync --locked
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked python harness/check_project.py
git diff --check
git status --short --branch
```

自动串行执行时应在每条外部命令后检查 `$LASTEXITCODE`，不能仅凭最后一条命令成功判断前面均通过。`git status` 显示未跟踪的工程文件是本阶段未提交状态的正常表现；`.venv` 不应出现在待提交文件中。

运行实际后端基础测试：

```powershell
uv run --locked pytest -ra
```

测试隔离环境变量和工作目录，使用临时数据库，不需要 DeepSeek API Key，也不调用模型。应报告实际通过、失败与跳过数量；空测试集不是成功。检查含义与退出标准见 [质量规范](quality.md)。

## 修改和更新依赖

先说明依赖用途、运行时影响及验证方式，再修改声明并生成锁文件。普通复现使用 `--locked`，避免运行命令时悄悄更新锁文件。

Phase 1 新增 FastAPI、Uvicorn、Pydantic、Pydantic Settings 和直接使用的 Starlette；开发组新增当前 TestClient 所需 HTTPX2。pytest 和 Ruff 保持 Phase 0 锁定版本。没有安装 LLM SDK、数据库 ORM、Node.js、Flutter 或 VPS 部署工具。

未来确有升级需求时，使用 uv 管理并重新运行相关检查。不要盲目执行全量升级或移除版本上限。

## 配置、启动与数据

默认配置即可启动；本地 `.env` 可选且不提交。仅当 `.env` 不存在时，才可复制 `.env.example`，避免覆盖已有配置。进程环境变量优先于 `.env`，最后采用默认值。未知 `.env` 字段会给出安全校验错误；不宣称会校验所有系统环境变量的拼写。

| 配置 | 默认值 / 作用 |
| --- | --- |
| NOVA_ENV | development；环境标签，不自动开启公网或生产能力 |
| NOVA_HOST | 127.0.0.1；支持 IP 或 localhost，实际传入服务启动器 |
| NOVA_PORT | 8000；合法端口范围 1–65535，实际传入服务启动器 |
| NOVA_LOG_LEVEL | INFO；标准日志等级，非法值拒绝启动 |
| NOVA_DATA_DIR | ./data；数据目录设置，独立于数据库路径 |
| NOVA_DATABASE_PATH | ./data/nova.sqlite3；SQLite 文件位置，可独立覆盖 |
| NOVA_DB_TIMEOUT | 5 秒；有界连接等待超时，以配置校验为准 |
| NOVA_LLM_PROVIDER / DEEPSEEK_MODEL / DEEPSEEK_API_KEY | 未来占位，本阶段不使用、不要求填写密钥 |

路径按启动时当前工作目录解析，因此先 `Set-Location` 到项目根目录。修改 `NOVA_DATA_DIR` 不会自动改写独立的 `NOVA_DATABASE_PATH`，需要时同时配置两项。

启动服务：

```powershell
uv run --locked python -m nova
```

另一 PowerShell 查询健康：

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health'
```

正常为 HTTP 200 与 `{"status":"ok","database":"ok"}`；数据库不可用返回统一 503 错误，不包含本地路径。Ctrl+C 停止服务，生命周期执行退出清理。没有聊天或前端页面。

仅初始化数据库而不启动服务器：

```powershell
uv run --locked python -m nova --init-db
```

初始化和启动使用同一逻辑，重复执行不会清空数据。元数据为 Nova application_id `0x4E4F5641` 和 user_version `1`，不创建业务表；未知、损坏或未来版本的数据库会拒绝，不能通过删除已有文件绕过错误。

服务启动器关闭 Uvicorn 原始访问日志，统一 JSON 控制台日志；不建议绕过启动器启用会记录原始查询参数的访问日志。没有公网鉴权和部署配置，当前验收为本地开发。

不要提交 `.env`、数据库、WAL/SHM、对话导出、记忆、日志或备份。若未来需要样本，使用脱敏合成数据并放在明确允许提交的位置。忽略规则不会自动取消已跟踪文件。

## 常见问题

### python 打开 Microsoft Store

使用 `uv run --locked python`。这会选择项目环境，不要求关闭系统执行别名或修改系统 Python。

### uv Python 安装出现目录链接错误

本机首次 `uv python install 3.14` 下载完整解释器后，创建或检查 Windows minor-version 目录链接时报错，退出码 2。精确版本 `3.14.10` 的解释器可直接执行；随后 `uv lock` 和 `uv sync --locked` 成功建立项目环境。

若遇到相同问题，先检查精确解释器是否存在且可运行；不要删除用户目录、强制重装或覆盖已有 Python：

```powershell
$novaPython = Join-Path $env:APPDATA 'uv\python\cpython-3.14.10-windows-x86_64-none\python.exe'
if (-not (Test-Path -LiteralPath $novaPython)) { throw '精确版本解释器不存在。' }
& $novaPython --version
if ($LASTEXITCODE -ne 0) { throw '解释器不可用。' }
uv sync --locked --python $novaPython
if ($LASTEXITCODE -ne 0) { throw '同步失败，不能继续验收。' }
uv run --locked python --version
```

本项目固定补丁版本以避免依赖该 minor-version 链接。该方案已经在本机验证；它不声称修复 uv 的目录链接机制。备用官方 MSI 安装尝试曾以路径错误失败，未被采用，也不是当前项目运行的前提，见 [进度记录](progress.md)。

### 下载受网络或代理影响

确认可以访问官方安装源、PyPI 和 Python 下载源。按本地网络要求配置代理；不要把代理凭据写入仓库，不关闭 TLS 校验，也不要改用来源不明的安装包。失败命令需保留真实退出码。

### 依赖锁文件过期或 Python 版本不符

先检查 `.python-version`、`pyproject.toml` 与 `uv.lock`，不要通过移除 `--locked` 隐藏差异。版本调整属于技术决策变更，需要说明并重新验证。

