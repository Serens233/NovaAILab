"""显式运行的本机 GPU 验收，不属于默认 pytest；不下载模型、不访问收费 API。"""

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
SERVER = r"""
import os,sys,threading
from pathlib import Path
import uvicorn
from nova.config import Settings
from nova.main import create_app
folder=Path(sys.argv[1]); port=int(sys.argv[2])
settings=Settings(_env_file=None,environment="test",host="127.0.0.1",port=port,
    data_dir=folder,database_path=folder/"acceptance.sqlite3",gpu_enabled=True,
    gpu_token=os.environ["NOVA_GPU_TOKEN"],api_key="",gpu_request_timeout=120)
server=uvicorn.Server(uvicorn.Config(create_app(settings),host="127.0.0.1",port=port,
    access_log=False,log_config=None))
def stop():
    sys.stdin.readline()
    server.should_exit=True
threading.Thread(target=stop,daemon=True).start()
server.run()
"""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    token = secrets.token_urlsafe(32)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    opener = build_opener(ProxyHandler({}), NoRedirect())
    report = {"tested_at_utc": datetime.now(UTC).isoformat(), "checks": {}, "observations": {}}
    checks = report["checks"]
    observations = report["observations"]
    process = None
    server_log = None
    launches = 0

    def call(path, payload=None, *, authenticated=True):
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["Authorization"] = "Bearer " + token
        request = Request(
            base + path,
            headers=headers,
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            method="POST" if payload is not None else "GET",
        )
        try:
            with opener.open(request, timeout=180) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def start():
        nonlocal process, server_log, launches
        launches += 1
        server_log = (args.output_dir / f"server-{launches}.log").open("x", encoding="utf-8")
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", SERVER, str(args.output_dir), str(port)],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=server_log,
            stderr=subprocess.STDOUT,
            text=True,
            env={**os.environ, "NOVA_GPU_TOKEN": token, "PYTHONUTF8": "1"},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("验收后端未能启动，请查看独立服务器日志")
            try:
                if call("/health")[0] == 200:
                    return
            except URLError, OSError:
                pass
            time.sleep(0.1)
        raise RuntimeError("验收后端就绪超时")

    def stop():
        nonlocal process
        if process is not None:
            if process.poll() is None:
                process.stdin.write("stop\n")
                process.stdin.flush()
                process.wait(timeout=45)
            if process.stdin:
                process.stdin.close()
            server_log.close()
            process = None

    def checked(name, condition):
        checks[name] = bool(condition)
        print(f"{name}: {'PASS' if condition else 'FAIL'}", flush=True)
        if not condition:
            raise RuntimeError("真实验收未通过：" + name)

    try:
        start()
        checked("authentication_required", call("/api/gpu/status", authenticated=False)[0] == 401)
        cli = subprocess.run(
            [sys.executable, "-B", str(ROOT / "harness" / "gpu_cli.py"), "status", "--url", base],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            env={**os.environ, "NOVA_GPU_TOKEN": token, "PYTHONUTF8": "1"},
        )
        checked("cli_status_request", cli.returncode == 0 and "mode" in json.loads(cli.stdout))
        code, status = call("/api/gpu/status")
        observations["initial"] = status
        checked(
            "ollama_and_gpu_available",
            code == 200 and status["backend"]["available"] and status["gpu_memory"]["available"],
        )
        code, status = call("/api/gpu/mode", {"mode": "GAMING"})
        checked("initial_unload", code == 200 and status["unload_verified"])
        code, status = call("/api/gpu/mode", {"mode": "AUTO"})
        checked("auto_does_not_preload", code == 200 and status["backend"]["models"] == [])
        code, reply = call("/api/gpu/generate", {"prompt": "请仅用中文说一句你好。"})
        checked(
            "local_chinese_generation",
            code == 200
            and reply.get("done") is True
            and any("\u4e00" <= c <= "\u9fff" for c in reply.get("response", "")),
        )
        _, status = call("/api/gpu/status")
        observations["loaded"] = status
        checked(
            "model_resident_on_gpu", any(m["size_vram"] > 0 for m in status["backend"]["models"])
        )
        code, status = call("/api/gpu/mode", {"mode": "GAMING"})
        observations["gaming"] = status
        checked(
            "gaming_unload_verified",
            code == 200
            and status["mode"] == "GAMING"
            and status["unload_verified"]
            and status["backend"]["models"] == [],
        )
        checked(
            "gaming_generate_blocked", call("/api/gpu/generate", {"prompt": "合成验收"})[0] == 423
        )
        checked("gaming_wake_blocked", call("/api/gpu/wake", {})[0] == 423)
        code, status = call("/api/gpu/mode", {"mode": "GAMING"})
        checked("gaming_idempotent_no_reload", code == 200 and status["backend"]["models"] == [])
        checked("backend_database_stay_healthy", call("/health")[0] == 200)
        stop()
        start()
        _, status = call("/api/gpu/status")
        checked(
            "gaming_survives_backend_restart",
            status["mode"] == "GAMING"
            and call("/api/gpu/generate", {"prompt": "合成验收"})[0] == 423,
        )
        code, status = call("/api/gpu/mode", {"mode": "AUTO"})
        checked("restore_auto_without_preload", code == 200 and status["backend"]["models"] == [])
        code, result = call("/api/gpu/wake", {})
        checked("explicit_wake_after_auto", code == 200 and result.get("done") is True)
        code, reply = call("/api/gpu/generate", {"prompt": "请用中文回答：一加一等于几？"})
        checked("generation_reloads_after_auto", code == 200 and reply.get("done") is True)
        code, result = call("/api/gpu/unload", {})
        observations["final"] = result
        checked("final_explicit_unload", code == 200 and result["unload_verified"])
        report["passed"] = all(checks.values())
    except Exception as error:
        report["passed"] = False
        report["failure_type"] = type(error).__name__
        print("验收失败，保留实际报告与服务器日志。", file=sys.stderr)
    finally:
        stop()
        report["finished_at_utc"] = datetime.now(UTC).isoformat()
        with (args.output_dir / "report.json").open("x", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write("\n")
        print("Report: " + str(args.output_dir / "report.json"), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
