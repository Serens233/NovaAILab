"""显式V2本机验收：真实180秒空闲，不下载模型、不运行游戏或收费API。"""

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
from uuid import uuid4

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
 gpu_token=os.environ["NOVA_GPU_TOKEN"],api_key="",cloud_enabled=False,
 gpu_model="qwen3.5:9b",gpu_request_timeout=300,chat_enabled=True,
 chat_default_model="qwen3.5:9b",chat_idle_seconds=180,
 gpu_auto_game_enabled=True,gpu_memory_pressure_enabled=True,
 gpu_game_detection_enabled=False)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    token = secrets.token_urlsafe(32)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    opener = build_opener(ProxyHandler({}), NoRedirect())
    report = {
        "started_at_utc": datetime.now(UTC).isoformat(),
        "scope": "v2_backend_real_ollama_stream_history_gpu_idle",
        "idle_seconds": 180,
        "cloud_calls_enabled": False,
        "game_launch_or_termination_executed": False,
        "checks": {},
        "observations": {},
        "streams": {},
        "shutdowns": [],
    }
    checks = report["checks"]
    observations = report["observations"]
    process = None
    server_log = None
    launches = 0
    attempted_inference = False
    samples = (args.output_dir / "samples.jsonl").open("x", encoding="utf-8")

    def request(path, payload=None, *, authenticated=True):
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["Authorization"] = "Bearer " + token
        return Request(
            base + path,
            headers=headers,
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            method="POST" if payload is not None else "GET",
        )

    def call(path, payload=None, *, authenticated=True):
        try:
            with opener.open(
                request(path, payload, authenticated=authenticated), timeout=300
            ) as res:
                return res.status, json.load(res)
        except HTTPError as error:
            return error.code, json.load(error)

    def checked(name, condition):
        checks[name] = bool(condition)
        print(f"{name}: {'PASS' if condition else 'FAIL'}", flush=True)
        if not condition:
            raise RuntimeError("验收未通过：" + name)

    def status(label):
        code, value = call("/api/gpu/status")
        if code != 200:
            raise RuntimeError("无法读取GPU状态")
        sample = {"at_utc": datetime.now(UTC).isoformat(), "label": label, "status": value}
        samples.write(json.dumps(sample, ensure_ascii=False) + "\n")
        samples.flush()
        return value

    def guard(value, *, allow_manual=False):
        detection = value.get("game_detection", {})
        confirmed = detection.get("signals", {}).get("confirmed", [])
        blocked = value.get("local_requests_blocked") and not (
            allow_manual and value.get("manual_gaming")
        )
        if (
            value.get("detected_gaming")
            or confirmed
            or detection.get("ready") is not True
            or detection.get("state") != "monitoring"
            or value.get("memory_pressure", {}).get("active")
            or value.get("fault")
            or blocked
        ):
            report["aborted_for_protection"] = True
            raise RuntimeError("游戏或安全保护生效，停止正向推理；未强行解除自动保护")

    def start():
        nonlocal process, server_log, launches
        launches += 1
        server_log = (args.output_dir / f"server-{launches}.log").open("x", encoding="utf-8")
        # 不继承已有Nova/DeepSeek配置；只为自有验收子进程提供随机令牌。
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.upper().startswith(("NOVA_", "DEEPSEEK_"))
        }
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", SERVER, str(args.output_dir), str(port)],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=server_log,
            stderr=subprocess.STDOUT,
            text=True,
            env={**environment, "NOVA_GPU_TOKEN": token, "PYTHONUTF8": "1"},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        for _ in range(200):
            if process.poll() is not None:
                raise RuntimeError("独立验收后端启动失败")
            try:
                if call("/health")[0] == 200:
                    return
            except URLError, OSError:
                pass
            time.sleep(0.1)
        raise TimeoutError("独立验收后端未就绪")

    def stop():
        nonlocal process, server_log
        if process is None:
            return
        current = process
        if current.poll() is None:
            current.stdin.write("stop\n")
            current.stdin.flush()
            # 上游线程和消息保存都应正常完成；不通过强杀释放资源。
            current.wait(timeout=330)
        report["shutdowns"].append({"launch": launches, "returncode": current.returncode})
        if current.stdin:
            current.stdin.close()
        if server_log:
            server_log.close()
        process, server_log = None, None
        if current.returncode != 0:
            raise RuntimeError("独立验收后端退出码非零")

    def stream(label, payload):
        nonlocal attempted_inference
        guard(status("before_" + label))
        attempted_inference = True
        started = time.monotonic()
        record = {"http_status": None, "events": [], "first_delta_seconds": None}
        report["streams"][label] = record
        try:
            with opener.open(request("/api/v1/chat", payload), timeout=300) as response:
                record["http_status"] = response.status
                record["content_type"] = response.headers.get("Content-Type", "")
                size = 0
                while True:
                    line = response.readline(131073)
                    if not line:
                        break
                    size += len(line)
                    if len(line) > 131072 or size > 4 * 1024 * 1024:
                        raise RuntimeError("验收流超过大小限制")
                    if not line.startswith(b"data:"):
                        continue
                    event = json.loads(line[5:].strip())
                    record["events"].append(event)
                    if event["type"] == "delta" and record["first_delta_seconds"] is None:
                        record["first_delta_seconds"] = round(time.monotonic() - started, 6)
                        print(label + ": first delta received", flush=True)
        except HTTPError as error:
            record["http_status"] = error.code
            record["error"] = json.load(error)
        finally:
            record["elapsed_seconds"] = round(time.monotonic() - started, 6)
        items = record["events"]
        done = [item for item in items if item["type"] == "done"]
        deltas = [item["text"] for item in items if item["type"] == "delta"]
        checked(
            label + "_stream",
            record["http_status"] == 200
            and record.get("content_type", "").startswith("text/event-stream")
            and record["first_delta_seconds"] is not None
            and len(done) == 1
            and items[-1]["type"] == "done"
            and not any(item["type"] == "error" for item in items)
            and done[0]["message"]["status"] == "complete"
            and "".join(deltas) == done[0]["message"]["content"],
        )
        return done[0]["message"]

    def payload(thread_id, model, content):
        return {
            "thread_id": thread_id,
            "model": model,
            "content": content,
            "request_id": str(uuid4()),
            "cloud_consent": False,
        }

    try:
        start()
        checked("healthy", call("/health")[0] == 200)
        checked("authentication_required", call("/api/v1/models", authenticated=False)[0] == 401)
        code, registry = call("/api/v1/models")
        observations["registry"] = registry
        available = {item["id"] for item in registry.get("models", []) if item.get("available")}
        checked(
            "both_local_models_installed", code == 200 and {"qwen3.5:9b", "qwen3.5:4b"} <= available
        )
        initial = status("initial")
        observations["initial"] = initial
        guard(initial)
        checked(
            "ollama_and_gpu_available",
            initial["backend"]["available"] and initial["gpu_memory"]["available"],
        )
        checked(
            "no_foreign_model_to_evict",
            all(
                item["name"] in {"qwen3.5:9b", "qwen3.5:4b"}
                for item in initial["backend"]["models"]
            ),
        )
        code, thread = call("/api/v1/threads", {})
        checked("thread_created", code == 201)
        observations["thread_id"] = thread["id"]
        first = stream(
            "nine_b", payload(thread["id"], "qwen3.5:9b", "请只用中文简短回答：你好，Nova。")
        )
        checked("nine_b_chinese_reply", any("\u4e00" <= c <= "\u9fff" for c in first["content"]))
        loaded = status("nine_b_loaded")
        observations["nine_b_loaded"] = loaded
        checked(
            "nine_b_on_gpu",
            any(
                m["name"] == "qwen3.5:9b" and m["size_vram"] > 0
                for m in loaded["backend"]["models"]
            ),
        )

        stream("four_b", payload(thread["id"], "qwen3.5:4b", "请用中文简短说一句欢迎回来。"))
        switched = status("four_b_loaded")
        observations["four_b_loaded"] = switched
        checked(
            "switch_keeps_one_model_and_auto",
            switched["mode"] == "AUTO"
            and not switched["manual_gaming"]
            and [m["name"] for m in switched["backend"]["models"]] == ["qwen3.5:4b"],
        )
        guard(switched)
        code, gaming = call("/api/gpu/mode", {"mode": "GAMING"})
        observations["gaming"] = gaming
        checked(
            "gaming_unload_verified",
            code == 200 and gaming["unload_verified"] and gaming["backend"]["models"] == [],
        )
        checked(
            "gaming_chat_rejected",
            call("/api/v1/chat", payload(thread["id"], "qwen3.5:9b", "游戏模式合成门禁测试"))[0]
            == 423,
        )
        checked("gaming_wake_rejected", call("/api/gpu/wake", {})[0] == 423)
        blocked = status("gaming_after_rejected_requests")
        checked(
            "gaming_did_not_reload",
            blocked["backend"]["models"] == [] and blocked["local_requests_blocked"],
        )
        checked("health_survives_gaming", call("/health")[0] == 200)
        guard(blocked, allow_manual=True)
        code, restored = call("/api/gpu/mode", {"mode": "AUTO"})
        checked(
            "auto_does_not_preload",
            code == 200 and restored["mode"] == "AUTO" and restored["backend"]["models"] == [],
        )

        replay_payload = payload(
            thread["id"], "qwen3.5:9b", "请用中文简短回答：游戏结束，可以继续。"
        )
        restored_message = stream("nine_b_after_auto", replay_payload)
        idle_start = status("before_real_180_second_idle")
        observations["before_idle"] = idle_start
        chat_at = datetime.fromisoformat(idle_start["last_operation"]["at"])
        deadline = time.monotonic() + 210
        idle_done = None
        print("Waiting for real 180-second idle unload (no accelerated clock).", flush=True)
        while time.monotonic() < deadline:
            current = status("idle_poll")
            guard(current)
            operation = current.get("last_operation") or {}
            if operation.get("reason") == "idle_timeout" and current["unload_verified"]:
                idle_done = current
                break
            time.sleep(5)
        checked("idle_unload_observed", idle_done is not None)
        observations["after_idle"] = idle_done
        idle_duration = (
            datetime.fromisoformat(idle_done["last_unload_at"]) - chat_at
        ).total_seconds()
        observations["idle_operation_interval_seconds"] = idle_duration
        checked("idle_waited_at_least_180_seconds", idle_duration >= 180)
        checked(
            "idle_keeps_auto_without_manual_lock",
            idle_done["mode"] == "AUTO"
            and not idle_done["manual_gaming"]
            and idle_done["backend"]["models"] == [],
        )
        code, history = call("/api/v1/threads/" + thread["id"])
        checked("history_readable", code == 200 and restored_message in history["messages"])
        observations["history_before_restart"] = history
        stop()
        start()
        code, persisted = call("/api/v1/threads/" + thread["id"])
        checked("history_survives_restart", code == 200 and persisted == history)
        before_replay = status("before_replay")
        guard(before_replay)
        checked("restart_did_not_preload", before_replay["backend"]["models"] == [])
        replay = stream("completed_request_replay", replay_payload)
        checked("same_request_reuses_saved_message", replay == restored_message)
        after_replay = status("after_replay")
        checked(
            "replay_did_not_reload",
            after_replay["backend"]["models"] == []
            and after_replay["last_operation"] == before_replay["last_operation"],
        )
        code, unchanged = call("/api/v1/threads/" + thread["id"])
        checked("replay_has_no_duplicate_messages", code == 200 and unchanged == persisted)
        observations["final"] = after_replay
    except Exception as error:
        report["failure_type"] = type(error).__name__
        print("V2 live acceptance stopped; inspect the saved report and server logs.", flush=True)
    finally:
        if process is not None and process.poll() is None and attempted_inference:
            try:
                current = status("before_shutdown_cleanup")
                if current["backend"]["models"]:
                    code, cleaned = call("/api/gpu/unload", {})
                    checks["failure_cleanup_verified"] = (
                        code == 200 and cleaned.get("unload_verified") is True
                    )
            except Exception as error:
                report["cleanup_failure_type"] = type(error).__name__
        try:
            stop()
            checks["graceful_shutdown"] = bool(report["shutdowns"]) and all(
                item["returncode"] == 0 for item in report["shutdowns"]
            )
        except Exception as error:
            report["shutdown_failure_type"] = type(error).__name__
            if process:
                report["remaining_backend_pid"] = process.pid
            checks["graceful_shutdown"] = False
        samples.close()
        report["finished_at_utc"] = datetime.now(UTC).isoformat()
        report["backend_acceptance_passed"] = (
            bool(checks)
            and all(checks.values())
            and not any(
                key in report
                for key in ("failure_type", "cleanup_failure_type", "shutdown_failure_type")
            )
        )
        report["full_v2_acceptance_complete"] = False
        report["not_tested"] = [
            "browser_UI",
            "mobile_browser",
            "real_game_transition",
            "paid_DeepSeek",
        ]
        with (args.output_dir / "report.json").open("x", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write("\n")
        print("Report: " + str(args.output_dir / "report.json"), flush=True)
    return 0 if report["backend_acceptance_passed"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
