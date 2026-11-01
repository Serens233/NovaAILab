"""用户自行启动游戏后的实机验收；不预加载模型，不启动或结束游戏。"""

import argparse
import ctypes
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from ctypes import wintypes
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
folder=Path(sys.argv[1]);port=int(sys.argv[2])
settings=Settings(_env_file=None,environment="test",host="127.0.0.1",port=port,
 data_dir=folder,database_path=folder/"acceptance.sqlite3",gpu_enabled=True,
 gpu_token=os.environ["NOVA_GPU_TOKEN"],api_key="",gpu_auto_game_enabled=True,
 gpu_memory_pressure_enabled=True,gpu_game_detection_enabled=False)
server=uvicorn.Server(uvicorn.Config(create_app(settings),host="127.0.0.1",port=port,
 access_log=False,log_config=None))
def stop():
 sys.stdin.readline();server.should_exit=True
threading.Thread(target=stop,daemon=True).start()
server.run()
"""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def game_window_state(pids):
    """只读取目标PID窗口标志，不读标题、不控制窗口。"""
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.EnumWindows.restype = wintypes.BOOL
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetForegroundWindow.restype = wintypes.HWND
    for name in ("IsWindowVisible", "IsIconic"):
        getattr(user, name).argtypes = [wintypes.HWND]
        getattr(user, name).restype = wintypes.BOOL
    foreground = user.GetForegroundWindow()
    result = []

    @callback_type
    def callback(hwnd, unused):
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            result.append(
                {
                    "pid": pid.value,
                    "visible": bool(user.IsWindowVisible(hwnd)),
                    "minimized": bool(user.IsIconic(hwnd)),
                    "foreground": hwnd == foreground,
                }
            )
        return True

    user.EnumWindows(callback, 0)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--verify-reload-after-exit", action="store_true")
    args = parser.parse_args()
    if not 30 <= args.seconds <= 900:
        parser.error("seconds范围30–900")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    token = secrets.token_urlsafe(32)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    opener = build_opener(ProxyHandler({}), NoRedirect())
    report = {
        "started_at_utc": datetime.now(UTC).isoformat(),
        "scope": "user_started_real_game",
        "preload_executed": False,
        "game_launch_or_termination_executed": False,
        "checks": {},
        "samples": [],
    }
    checks = report["checks"]
    process = None
    log = None

    def call(path, payload=None):
        request = Request(
            base + path,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            method="POST" if payload is not None else "GET",
        )
        try:
            with opener.open(request, timeout=60) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    try:
        log = (args.output_dir / "server.log").open("x", encoding="utf-8")
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", SERVER, str(args.output_dir), str(port)],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            env={**os.environ, "NOVA_GPU_TOKEN": token, "PYTHONUTF8": "1"},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
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
            raise TimeoutError("验收后端未就绪")
        deadline = time.monotonic() + args.seconds
        with (args.output_dir / "samples.jsonl").open("x", encoding="utf-8") as samples:
            while time.monotonic() < deadline:
                code, status = call("/api/gpu/status")
                detection = status.get("game_detection", {})
                signals = detection.get("signals", {})
                confirmed = signals.get("confirmed", [])
                windows = game_window_state({item["pid"] for item in confirmed})
                sample = {
                    "at_utc": datetime.now(UTC).isoformat(),
                    "status": status,
                    "game_windows": windows,
                }
                report["samples"].append(sample)
                samples.write(json.dumps(sample, ensure_ascii=False) + "\n")
                samples.flush()
                print(
                    json.dumps(
                        {
                            "mode": status.get("mode"),
                            "detector_state": status.get("game_detection", {}).get("state"),
                            "confirmed": [
                                {"pid": x["pid"], "name": x["name"], "source": x["source"]}
                                for x in confirmed
                            ],
                            "candidate_count": len(signals.get("candidates", [])),
                            "game_windows": windows,
                            "unload_verified": status.get("unload_verified"),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                if (
                    confirmed
                    and status.get("mode") == "GAMING"
                    and status.get("mode_source") == "game_detection"
                    and not status.get("manual_gaming")
                    and status.get("detected_gaming") is True
                    and status.get("fault") is None
                    and detection.get("ready") is True
                    and detection.get("state") == "game_detected"
                    and status.get("unload_verified")
                    and not checks.get("gate_tested")
                ):
                    checks["game_detected"] = True
                    checks["game_mode_active"] = bool(status["local_requests_blocked"])
                    checks["model_absence_verified"] = (
                        status["unload_verified"] and status["backend"]["models"] == []
                    )
                    # 仅在确认门禁后检查生成/wake拒绝；不主动预载模型。
                    checks["generation_rejected"] = (
                        call("/api/gpu/generate", {"prompt": "合成门禁验收"})[0] == 423
                    )
                    checks["health_available"] = call("/health")[0] == 200
                    checks["wake_rejected"] = call("/api/gpu/wake", {})[0] == 423
                    checks["gate_tested"] = True
                    print("CONFIRMED_GAME_AND_GATE_CHECKED", flush=True)
                if checks.get("gate_tested") and status.get("mode") == "GAMING":
                    if any(
                        any(w["pid"] == pid and w["visible"] for w in windows)
                        and not any(w["pid"] == pid and w["foreground"] for w in windows)
                        for pid in {item["pid"] for item in confirmed}
                    ):
                        checks["unfocused_game_stays_gaming"] = True
                    if any(w["minimized"] for w in windows):
                        checks["minimized_game_stays_gaming"] = True
                if checks.get("game_detected") and status.get("mode") == "AUTO":
                    checks["exit_auto_observed"] = True
                    checks["exit_no_preload"] = status["backend"]["models"] == []
                    checks["exit_grace_at_least_60"] = (
                        status["game_detection"]["clear_for_seconds"] >= 60
                    )
                    safe_recovery = (
                        checks["exit_no_preload"]
                        and checks["exit_grace_at_least_60"]
                        and detection.get("ready") is True
                        and detection.get("state") == "monitoring"
                        and not confirmed
                        and not status["local_requests_blocked"]
                        and not status.get("manual_gaming")
                        and not status.get("detected_gaming")
                        and status.get("fault") is None
                    )
                    if args.verify_reload_after_exit and safe_recovery:
                        code, reply = call("/api/gpu/generate", {"prompt": "请用中文回答你好。"})
                        checks["requested_generation_after_exit"] = (
                            code == 200 and reply.get("done") is True
                        )
                        code, final = call("/api/gpu/unload", {})
                        checks["post_exit_cleanup"] = (
                            code == 200 and final.get("unload_verified") is True
                        )
                    break
                if (args.output_dir / "stop.request").exists():
                    report["stopped_by_operator"] = True
                    break
                time.sleep(5)
    except Exception as error:
        report["failure_type"] = type(error).__name__
        print("实机采集遇到异常，保留日志。", file=sys.stderr)
    finally:
        if process is not None:
            if process.poll() is None:
                process.stdin.write("stop\n")
                process.stdin.flush()
                process.wait(timeout=45)
            process.stdin.close()
        if log:
            log.close()
        report["finished_at_utc"] = datetime.now(UTC).isoformat()
        report["game_gate_checks_passed"] = bool(checks.get("gate_tested")) and all(
            checks.get(key) is True
            for key in [
                "game_detected",
                "game_mode_active",
                "model_absence_verified",
                "generation_rejected",
                "wake_rejected",
                "health_available",
            ]
        )
        report["full_game_acceptance_complete"] = False
        report["not_tested"] = ["causal_unload_of_previously_resident_model", "FPS_difference"]
        if not checks.get("minimized_game_stays_gaming"):
            report["not_tested"].append("actual_minimized_window")
        if not checks.get("exit_auto_observed"):
            report["not_tested"].append("user_game_exit_and_60_second_recovery")
        with (args.output_dir / "report.json").open("x", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write("\n")
        print("Report: " + str(args.output_dir / "report.json"), flush=True)
    return 0 if report["game_gate_checks_passed"] and "failure_type" not in report else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
