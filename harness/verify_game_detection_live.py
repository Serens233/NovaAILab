"""显式验收 Windows 白名单检测，使用自有模拟进程和真实60秒退出窗口。"""

import argparse
import json
import os
import secrets
import shutil
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
folder=Path(sys.argv[1]);port=int(sys.argv[2]);image=sys.argv[3]
settings=Settings(_env_file=None,environment="test",host="127.0.0.1",port=port,
    data_dir=folder,database_path=folder/"acceptance.sqlite3",gpu_enabled=True,
    gpu_token=os.environ["NOVA_GPU_TOKEN"],api_key="",gpu_request_timeout=120,
    gpu_game_detection_enabled=True,gpu_game_processes=[image],gpu_game_poll_seconds=5)
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
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("此验收只用于本机Windows")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    image = "nova-game-simulator-" + secrets.token_hex(4) + ".exe"
    # 只复制系统可信cmd，使用内置set /p等待输入；正常写入一行即可退出。
    # 不启动真正游戏，不终止任何已有进程，不需要下载编译器或管理员权限。
    source = Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"
    target = args.output_dir / image
    shutil.copyfile(source, target)
    token = secrets.token_urlsafe(32)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    opener = build_opener(ProxyHandler({}), NoRedirect())
    report = {
        "tested_at_utc": datetime.now(UTC).isoformat(),
        "simulated_exe": image,
        "checks": {},
        "observations": {},
        "poll_samples": [],
    }
    checks = report["checks"]
    observations = report["observations"]
    process = None
    simulation = None
    server_log = None

    def call(path, payload=None):
        request = Request(
            base + path,
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            method="POST" if payload is not None else "GET",
        )
        try:
            with opener.open(request, timeout=180) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def checked(name, condition):
        checks[name] = bool(condition)
        print(f"{name}: {'PASS' if condition else 'FAIL'}", flush=True)
        if not condition:
            raise RuntimeError("验收未通过：" + name)

    def wait_for(predicate, timeout):
        deadline = time.monotonic() + timeout
        last_print = 0.0
        while True:
            code, status = call("/api/gpu/status")
            detector = status.get("game_detection", {})
            report["poll_samples"].append(
                {
                    "monotonic": time.monotonic(),
                    "state": detector.get("state"),
                    "mode": status.get("mode"),
                    "clear_for_seconds": detector.get("clear_for_seconds"),
                }
            )
            if code == 200 and predicate(status):
                return status
            if time.monotonic() >= deadline:
                raise TimeoutError("等待检测状态超时")
            if time.monotonic() - last_print >= 10:
                print(
                    f"Waiting: {detector.get('state')}, absent={detector.get('clear_for_seconds')}s",
                    flush=True,
                )
                last_print = time.monotonic()
            time.sleep(1)

    def finish_simulation():
        if simulation is not None and simulation.poll() is None:
            simulation.stdin.write("finished\n")
            simulation.stdin.flush()
            simulation.wait(timeout=10)
            simulation.stdin.close()

    try:
        server_log = (args.output_dir / "server.log").open("x", encoding="utf-8")
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", SERVER, str(args.output_dir), str(port), image],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=server_log,
            stderr=subprocess.STDOUT,
            text=True,
            env={**os.environ, "NOVA_GPU_TOKEN": token, "PYTHONUTF8": "1"},
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        for _ in range(150):
            if process.poll() is not None:
                raise RuntimeError("验收后端启动失败")
            try:
                if call("/health")[0] == 200:
                    break
            except URLError, OSError:
                pass
            time.sleep(0.1)
        else:
            raise TimeoutError("验收后端启动超时")
        status = wait_for(lambda s: s["game_detection"]["state"] == "monitoring", 15)
        checked(
            "initial_scan_auto", status["mode"] == "AUTO" and not status["local_requests_blocked"]
        )
        code, reply = call("/api/gpu/generate", {"prompt": "只回答中文你好。"})
        checked("initial_gpu_generation", code == 200 and reply.get("done") is True)
        observations["loaded"] = call("/api/gpu/status")[1]
        simulation = subprocess.Popen(
            [str(target), "/d", "/q", "/c", "set /p NOVA_SIM_WAIT="],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        time.sleep(0.2)
        checked("simulation_running", simulation.poll() is None)
        gaming = wait_for(lambda s: s["mode"] == "GAMING" and s["unload_verified"], 30)
        observations["gaming"] = gaming
        checked("whitelist_detected", gaming["game_detection"]["matched_processes"] == [image])
        checked(
            "detected_source_no_manual_latch",
            gaming["detected_gaming"] and not gaming["manual_gaming"],
        )
        checked("game_unload_verified", gaming["backend"]["models"] == [])
        checked(
            "gaming_generate_blocked", call("/api/gpu/generate", {"prompt": "合成测试"})[0] == 423
        )
        checked("gaming_wake_blocked", call("/api/gpu/wake", {})[0] == 423)
        checked("manual_auto_cannot_bypass", call("/api/gpu/mode", {"mode": "AUTO"})[0] == 409)
        checked("health_stays_available", call("/health")[0] == 200)
        finish_simulation()
        cooldown = wait_for(lambda s: s["game_detection"]["state"] == "exit_grace", 15)
        checked(
            "exit_grace_blocks", cooldown["mode"] == "GAMING" and cooldown["local_requests_blocked"]
        )
        started = time.monotonic()
        restored = wait_for(lambda s: s["mode"] == "AUTO", 90)
        elapsed = time.monotonic() - started
        observations["restored"] = restored
        observations["measured_grace_wait_seconds"] = round(elapsed, 3)
        checked("full_60_seconds_confirmed", restored["game_detection"]["clear_for_seconds"] >= 60)
        checked(
            "auto_restore_no_preload",
            restored["backend"]["models"] == [] and not restored["local_requests_blocked"],
        )
        code, reply = call("/api/gpu/generate", {"prompt": "请只回答数字二。"})
        checked("generation_after_game_exit", code == 200 and reply.get("done") is True)
        code, final = call("/api/gpu/unload", {})
        checked(
            "final_manual_unload",
            code == 200 and final["unload_verified"] and final["manual_gaming"],
        )
        observations["final"] = final
        report["passed"] = all(checks.values())
    except Exception as error:
        report["passed"] = False
        report["failure_type"] = type(error).__name__
        print("验收未完成，保留实际报告；不将失败标记为通过。", file=sys.stderr)
    finally:
        finish_simulation()
        if process is not None:
            if process.poll() is None:
                process.stdin.write("stop\n")
                process.stdin.flush()
                process.wait(timeout=45)
            process.stdin.close()
        if server_log:
            server_log.close()
        report["finished_at_utc"] = datetime.now(UTC).isoformat()
        with (args.output_dir / "report.json").open("x", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write("\n")
        print("Report: " + str(args.output_dir / "report.json"), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
