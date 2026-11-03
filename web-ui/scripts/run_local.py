"""启动独立 UI-01 与现有 FastAPI。只在子进程环境传递临时管理令牌。"""

import argparse
import os
import secrets
import socket
import subprocess
import sys
import time
import webbrowser
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

WEB = Path(__file__).resolve().parents[1]
ROOT = WEB.parent
BACKEND = """
import os, sys, threading
from pathlib import Path
import uvicorn
from nova.config import Settings
from nova.main import create_app

data = Path(sys.argv[1])
port = int(sys.argv[2])
settings = Settings(_env_file=None, environment='development', host='127.0.0.1', port=port,
    data_dir=data, database_path=data / 'nova.sqlite3', gpu_enabled=True, chat_enabled=True,
    gpu_token=os.environ['NOVA_GPU_TOKEN'], gpu_ollama_url='http://127.0.0.1:11434',
    gpu_auto_game_enabled=True, gpu_memory_pressure_enabled=True,
    gpu_game_detection_enabled=False, gpu_request_timeout=180,
    cloud_enabled=False, api_key='', qwen38_enabled=False)
server = uvicorn.Server(uvicorn.Config(create_app(settings), host='127.0.0.1', port=port,
    access_log=False, log_config=None))
def stop():
    sys.stdin.readline()
    server.should_exit = True
threading.Thread(target=stop, daemon=True).start()
server.run()
"""


def ready(opener, url):
    try:
        with opener.open(url, timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def free(port):
    with socket.socket() as sock:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", port))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", type=Path)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--frontend-port", type=int, default=5173)
    parser.add_argument("--backend-port", type=int, default=8000)
    parser.add_argument("--open", action="store_true", help="就绪后打开浏览器")
    parser.add_argument("--stop", action="store_true", help="请求此数据目录对应的启动器退出")
    args = parser.parse_args()
    data = args.data_dir.resolve()
    signal = data / "ui01.stop"
    if args.stop:
        if data.is_dir():
            signal.touch()
        return 0
    if args.node is None or not args.node.is_file() or not (WEB / "dist/index.html").is_file():
        parser.error("需要现有 Node 可执行文件和 npm run build 生成的 dist")
    ports = (args.backend_port, args.frontend_port)
    if len(set(ports)) != 2 or any(not 1 <= port <= 65535 for port in ports):
        parser.error("请输入两个不同的有效本机端口")
    opener = build_opener(ProxyHandler({}))
    for _ in range(20):
        if ready(opener, "http://127.0.0.1:11434/api/version"):
            break
        time.sleep(0.5)
    else:
        parser.error("Ollama 尚未就绪，请使用 Nova AI Lab 的 Ollama 启动器")
    for port in ports:
        try:
            free(port)
        except OSError:
            parser.error(f"端口 {port} 已被占用，请检查现有 Nova 服务")
    data.mkdir(parents=True, exist_ok=True)
    signal.unlink(missing_ok=True)
    logs = data / "logs" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    logs.mkdir(parents=True, exist_ok=False)
    token = secrets.token_urlsafe(32)
    children = []
    url = f"http://127.0.0.1:{args.frontend_port}"
    commands = (
        (
            [sys.executable, "-B", "-c", BACKEND, str(data), str(args.backend_port)],
            {"NOVA_GPU_TOKEN": token, "PYTHONPATH": str(ROOT / "src")},
            f"http://127.0.0.1:{args.backend_port}/health",
            "backend",
        ),
        (
            [
                str(args.node.resolve()),
                "scripts/serve-local.mjs",
                str(args.frontend_port),
                str(args.backend_port),
            ],
            {"NOVA_UI_BACKEND_TOKEN": token},
            url,
            "web",
        ),
    )
    with ExitStack() as stack:
        try:
            for command, env, health, name in commands:
                output = stack.enter_context((logs / (name + ".log")).open("x", encoding="utf-8"))
                child = subprocess.Popen(
                    command,
                    cwd=WEB,
                    env={**os.environ, **env, "PYTHONUTF8": "1"},
                    stdin=subprocess.PIPE,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                children.append(child)
                for _ in range(120):
                    if child.poll() is not None:
                        raise RuntimeError(f"{name} 启动失败，查看 {logs}")
                    if ready(opener, health):
                        break
                    time.sleep(0.25)
                else:
                    raise RuntimeError(f"{name} 尚未就绪，查看 {logs}")
            print(f"Nova 已启动 {url}", flush=True)
            if args.open:
                webbrowser.open(url)
            while not signal.exists():
                if any(child.poll() is not None for child in children):
                    raise RuntimeError(f"服务提前退出，查看 {logs}")
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        except (OSError, RuntimeError) as error:
            print(str(error), file=sys.stderr)
            return 1
        finally:
            for child in reversed(children):
                try:
                    if child.poll() is None:
                        child.stdin.write(b"stop\n")
                        child.stdin.close()
                        child.wait(timeout=210)
                except OSError, subprocess.TimeoutExpired:
                    print(f"进程 {child.pid} 正在退出，请检查 {logs}", file=sys.stderr)
                finally:
                    child.stdin.close()
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
