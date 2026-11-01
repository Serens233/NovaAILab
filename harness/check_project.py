"""Phase 1 工程自检：只读、标准库实现，不调用模型或修改工作区。"""

import re
import subprocess
import sys
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "AGENTS.md",
    "README.md",
    "GOALS.md",
    "PLANS.md",
    ".gitignore",
    ".env.example",
    ".editorconfig",
    ".gitattributes",
    ".python-version",
    "pyproject.toml",
    "uv.lock",
    "docs/architecture.md",
    "docs/decisions.md",
    "docs/roadmap.md",
    "docs/quality.md",
    "docs/windows-development.md",
    "docs/progress.md",
    "harness/README.md",
    "harness/check_project.py",
    "docs/ACCEPTANCE.md",
    "harness/context/CURRENT_STATE.md",
    "build-log.md",
    "src/nova/__init__.py",
    "src/nova/__main__.py",
    "src/nova/main.py",
    "src/nova/config.py",
    "src/nova/logging_config.py",
    "src/nova/errors.py",
    "src/nova/api/__init__.py",
    "src/nova/api/router.py",
    "src/nova/api/health.py",
    "src/nova/api/middleware.py",
    "src/nova/storage/__init__.py",
    "src/nova/storage/database.py",
    "tests/conftest.py",
    "tests/test_app.py",
    "tests/test_health.py",
    "tests/test_config.py",
    "tests/test_errors.py",
    "tests/test_logging.py",
    "tests/test_database.py",
    "tests/test_cli.py",
)
IGNORED_PATHS = (
    ".env",
    ".env.local",
    ".env.production",
    "nested/.env",
    "nested/.env.local",
    ".venv/pyvenv.cfg",
    "__pycache__/module.pyc",
    ".pytest_cache/example",
    ".ruff_cache/example",
    "data/chat.json",
    "logs/server.txt",
    "backups/snapshot.zip",
    "exports/memories.json",
    "nova.db",
    "nova.db-wal",
    "nova.db-shm",
    "nova.sqlite3",
    "nova.sqlite3-wal",
    "nova.sqlite3-shm",
    "nova.sqlite",
    "nova.sqlite-wal",
    "server.log",
    "private.pem",
    "private.key",
    "credentials.json",
    "secrets.json",
    "web/node_modules/example/index.js",
)
KEPT_PATHS = (*REQUIRED, "nested/.env.example", "src/nova/config.py", "tests/test_example.py")
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?im)^\s*(?:DEEPSEEK_API_KEY|OPENAI_API_KEY)\s*=\s*[\"']?[^\s\"'#]+"),
)


def git(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    """只向 Git 传递固定参数或路径，不经过 shell。"""
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )


def check_files() -> list[str]:
    return [f"缺少文件：{name}" for name in REQUIRED if not (ROOT / name).is_file()]


def check_config() -> list[str]:
    errors = []
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    project = config["project"]
    if project.get("requires-python") != ">=3.14,<3.15":
        errors.append("Python 版本约束偏离 Phase 0 决策")
    pinned = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"3\.14\.\d+", pinned) or sys.version.split()[0] != pinned:
        errors.append("当前解释器与 .python-version 的精确版本不一致")
    expected_runtime = {
        "fastapi>=0.115,<1",
        "uvicorn>=0.32,<1",
        "pydantic>=2.10,<3",
        "pydantic-settings>=2.6,<3",
        "starlette>=0.40,<2",
    }
    if set(project.get("dependencies", [])) != expected_runtime:
        errors.append("运行时依赖偏离已确认的 Phase 1 基础设施范围")
    dev = config.get("dependency-groups", {}).get("dev", [])
    if set(dev) != {"pytest>=9,<10", "ruff>=0.14,<1", "httpx2>=2,<3"}:
        errors.append("开发依赖偏离已说明的 pytest / Ruff / HTTPX2 范围")
    if config["tool"]["uv"].get("package") is False:
        errors.append("Phase 1 应按可编辑应用包安装")
    build = config.get("build-system", {})
    if build.get("build-backend") != "uv_build" or build.get("requires") != [
        "uv_build>=0.13.6,<0.14"
    ]:
        errors.append("构建后端偏离已确认的 uv_build 配置")
    if config["tool"]["uv"].get("build-backend", {}).get("module-name") != "nova":
        errors.append("应用模块名必须为 nova")
    if config["tool"]["ruff"].get("target-version") != "py314":
        errors.append("Ruff 目标 Python 版本不一致")
    if config["tool"]["pytest"]["ini_options"].get("testpaths") != ["tests"]:
        errors.append("pytest 测试目录配置不一致")
    package_versions = {
        package["name"]: package.get("version") for package in lock.get("package", [])
    }
    packages = set(package_versions)
    if (
        not {
            project["name"],
            "pytest",
            "ruff",
            "httpx2",
            "fastapi",
            "uvicorn",
            "pydantic",
            "pydantic-settings",
            "starlette",
        }
        <= packages
    ):
        errors.append("uv.lock 缺少项目或开发工具记录；还需运行 uv lock --check")
    if package_versions.get("pytest") != "9.1.1" or package_versions.get("ruff") != "0.17.3":
        errors.append("Phase 0 已锁定的 pytest / Ruff 不应被无关升级")
    return errors


def check_env() -> list[str]:
    errors = []
    values = {}
    for number, line in enumerate(
        (ROOT / ".env.example").read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([A-Z][A-Z0-9_]*)=(.*)", line)
        if not match:
            errors.append(f".env.example:{number} 变量格式错误")
            continue
        name, value = match.groups()
        if name in values:
            errors.append(f".env.example:{number} 变量重复：{name}")
        values[name] = value
        if any(part in name for part in ("KEY", "TOKEN", "PASSWORD", "SECRET")) and value:
            errors.append(f".env.example:{number} 敏感配置必须留空：{name}")
    if values.get("DEEPSEEK_API_KEY") != "":
        errors.append("示例必须包含留空的 DEEPSEEK_API_KEY")
    if values.get("NOVA_HOST") != "127.0.0.1":
        errors.append("开发监听地址必须默认为本机")
    return errors


def check_ignore() -> list[str]:
    paths = (*IGNORED_PATHS, *KEPT_PATHS)
    # NUL 分隔避免 Windows 文本管道把 LF 转成 CRLF 并将 CR 混入文件名。
    result = git("check-ignore", "--no-index", "-z", "--stdin", input_text="\0".join(paths) + "\0")
    if result.returncode not in (0, 1):
        return ["git check-ignore 执行失败；请先安装 Git 并在项目根目录初始化仓库"]
    ignored = set(result.stdout.split("\0")) - {""}
    errors = [f"路径未被忽略：{path}" for path in IGNORED_PATHS if path not in ignored]
    errors.extend(f"项目文件被错误忽略：{path}" for path in KEPT_PATHS if path in ignored)
    tracked = git("ls-files", "-z")
    if tracked.returncode != 0:
        return [*errors, "无法检查已跟踪文件"]
    tracked_paths = [path for path in tracked.stdout.split("\0") if path]
    if tracked_paths:
        check = git(
            "check-ignore",
            "--no-index",
            "-z",
            "--stdin",
            input_text="\0".join(tracked_paths) + "\0",
        )
        if check.returncode not in (0, 1):
            errors.append("已跟踪文件的忽略规则检查失败")
        else:
            errors.extend(
                f"已跟踪应忽略的文件：{path}" for path in check.stdout.split("\0") if path
            )
    return errors


def check_text() -> list[str]:
    result = git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    if result.returncode != 0:
        return ["无法列出项目文件；请检查 Git 初始化状态"]
    errors = []
    for name in sorted(set(result.stdout.split("\0")) - {""}):
        path = ROOT / name
        if (
            not path.is_file()
            or path.suffix not in {".py", ".md", ".toml", ".lock"}
            and name not in REQUIRED
        ):
            continue
        data = path.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"文件不是 UTF-8：{name}")
            continue
        if data.startswith(b"\xef\xbb\xbf") or b"\r" in data or not data.endswith(b"\n"):
            errors.append(f"编码/换行不符合 UTF-8 无 BOM、LF、末尾换行约定：{name}")
        for number, line in enumerate(text.splitlines(), 1):
            if line != line.rstrip():
                errors.append(f"存在行尾空格：{name}:{number}")
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            errors.append(f"发现疑似密钥模式，请人工审查（不输出内容）：{name}")
        if path.suffix == ".md":
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
                url = urlsplit(target.strip("<>"))
                if url.scheme or url.netloc or not url.path:
                    continue
                resolved = (path.parent / unquote(url.path)).resolve()
                if not resolved.is_relative_to(ROOT) or not resolved.exists():
                    errors.append(f"本地链接不存在或越出项目：{name} -> {target}")
    return errors


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    failures = 0
    checks = (
        ("必需文件", check_files),
        ("Python / TOML / 锁文件配置", check_config),
        ("环境示例", check_env),
        ("Git 忽略与跟踪边界", check_ignore),
        ("文本、链接与常见密钥模式", check_text),
    )
    for label, check in checks:
        try:
            errors = check()
        except OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError:
            errors = [f"{label} 无法完成；请检查必需文件、配置和工具，不输出潜在敏感内容"]
        failures += len(errors)
        print(f"{'FAIL' if errors else 'PASS'} {label}")
        for error in errors:
            print(f"  {error}")
    print(f"Phase 1 工程自检：{failures} 个问题；这不替代 pytest 或完整密钥审计。")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
