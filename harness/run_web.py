"""Windows本机启动Nova V2；随机令牌仅传给子进程，不写.env、不打印。"""

import argparse
import os
import secrets
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
BACKEND = r"""
import os,sys,threading
from pathlib import Path
import uvicorn
from nova.config import Settings
from nova.main import create_app
folder=Path(sys.argv[1]);port=int(sys.argv[2])
settings=Settings(_env_file=Path.cwd()/".env",host="127.0.0.1",port=port,
 data_dir=folder,database_path=folder/"nova.sqlite3",gpu_enabled=True,chat_enabled=True,
 gpu_token=os.environ["NOVA_GPU_TOKEN"],gpu_auto_game_enabled=True,
 gpu_game_detection_enabled=False,gpu_memory_pressure_enabled=True)
server=uvicorn.Server(uvicorn.Config(create_app(settings),host="127.0.0.1",port=port,
 log_config=None,access_log=False))
def stop():
 sys.stdin.readline();server.should_exit=True
threading.Thread(target=stop,daemon=True).start()
server.run()
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", action="store_true", help="使用Next开发模式，默认运行已有生产构建")
    parser.add_argument("--backend-port", type=int, default=8000)
    parser.add_argument("--frontend-port", type=int, default=3000)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument(
        "--node-dir", type=Path, default=Path(r"E:\NovaAILab\runtime\node-v24.22.2-win-x64")
    )
    args = parser.parse_args()
    if args.backend_port == args.frontend_port:
        parser.error("前后端端口必须不同")
    for port in (args.backend_port, args.frontend_port):
        if not 1 <= port <= 65535:
            parser.error("端口范围必须为1至65535")
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                parser.error("指定端口已被使用；请先确认现有服务或选择其他端口")
    node = args.node_dir / "node.exe"
    frontend = ROOT / "frontend"
    if not node.is_file() or not (frontend / "node_modules/next/package.json").is_file():
        parser.error("缺少Node或前端依赖，请按README安装")
    if not args.dev and not (frontend / ".next/BUILD_ID").is_file():
        parser.error("缺少生产构建，请先npm run build，或使用--dev")
    logs = ROOT / "logs" / ("web-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    logs.mkdir(parents=True, exist_ok=False)
    token = secrets.token_urlsafe(32)
    environment = {
        **os.environ,
        "PYTHONUTF8": "1",
        "NEXT_TELEMETRY_DISABLED": "1",
        "NOVA_GPU_TOKEN": token,
        "NOVA_BACKEND_TOKEN": token,
        "NOVA_BACKEND_URL": f"http://127.0.0.1:{args.backend_port}",
        "PATH": str(args.node_dir) + os.pathsep + os.environ.get("PATH", ""),
    }
    opener = build_opener(ProxyHandler({}))
    children, outputs = [], []
    try:
        commands = [
            [
                sys.executable,
                "-B",
                "-c",
                BACKEND,
                str(args.data_dir.resolve()),
                str(args.backend_port),
            ],
            [
                str(node),
                str(ROOT / "harness/web-server.mjs"),
                str(frontend),
                str(args.frontend_port),
                "dev" if args.dev else "start",
            ],
        ]
        for index, command in enumerate(commands):
            output = (logs / ("backend.log" if index == 0 else "frontend.log")).open(
                "x", encoding="utf-8"
            )
            outputs.append(output)
            children.append(
                subprocess.Popen(
                    command,
                    cwd=ROOT,
                    env=environment,
                    stdin=subprocess.PIPE,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            )
            url = (
                f"http://127.0.0.1:{args.backend_port}/health"
                if index == 0
                else f"http://127.0.0.1:{args.frontend_port}"
            )
            for _ in range(180):
                if children[-1].poll() is not None:
                    raise RuntimeError("服务启动失败，请查看本次日志")
                try:
                    with opener.open(url, timeout=2) as response:
                        if response.status == 200:
                            break
                except URLError, OSError:
                    pass
                time.sleep(0.5)
            else:
                raise TimeoutError("服务尚未就绪，请查看本次日志")
        print(f"Nova已启动：http://127.0.0.1:{args.frontend_port}", flush=True)
        print(f"仅本机访问；日志：{logs}；按Ctrl+C或输入回车正常结束。", flush=True)
        input()
    except KeyboardInterrupt, EOFError:
        pass
    except (OSError, RuntimeError, TimeoutError) as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        for child in reversed(children):
            if child.poll() is None:
                try:
                    child.stdin.write("stop\n")
                    child.stdin.flush()
                    child.wait(timeout=330)
                except BrokenPipeError, subprocess.TimeoutExpired:
                    print(
                        f"进程{child.pid}尚未正常结束，请查看日志；没有强制终止。", file=sys.stderr
                    )
            child.stdin.close()
        for output in outputs:
            output.close()
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
