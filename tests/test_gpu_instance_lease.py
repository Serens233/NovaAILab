"""以不同 TEMP 启动真实进程，检查同一服务的实例互斥。"""

import os
import subprocess
import sys
import threading
import time
from uuid import uuid4

from nova.gpu.manager import InstanceLease


def test_instance_lease_is_shared_across_temporary_directories(tmp_path):
    first_temp = tmp_path / "first"
    second_temp = tmp_path / "second"
    first_temp.mkdir()
    second_temp.mkdir()
    ready = tmp_path / "ready"
    identity = "synthetic-" + str(uuid4())
    code = """
from pathlib import Path
import sys
from nova.gpu.manager import InstanceLease
lease = InstanceLease(sys.argv[1])
try:
    lease.acquire()
except RuntimeError:
    print('blocked', flush=True)
    raise SystemExit(7)
Path(sys.argv[2]).touch()
sys.stdin.readline()
lease.release()
"""
    first_env = {**os.environ, "TEMP": str(first_temp), "TMP": str(first_temp)}
    second_env = {**os.environ, "TEMP": str(second_temp), "TMP": str(second_temp)}
    first = subprocess.Popen(
        [sys.executable, "-B", "-c", code, identity, str(ready)],
        env=first_env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and first.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), first.stderr.read() if first.poll() is not None else "not ready"
        second = subprocess.run(
            [sys.executable, "-B", "-c", code, identity, str(tmp_path / "second-ready")],
            env=second_env,
            input="\n",
            text=True,
            capture_output=True,
            timeout=15,
        )
        assert second.returncode == 7
        assert second.stdout.strip() == "blocked"
    finally:
        if first.poll() is None:
            first.stdin.write("\n")
            first.stdin.flush()
        first.communicate(timeout=15)
    assert first.returncode == 0
    third = subprocess.run(
        [sys.executable, "-B", "-c", code, identity, str(tmp_path / "third-ready")],
        env=second_env,
        input="\n",
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert third.returncode == 0, third.stderr


def test_lease_released_from_another_thread_allows_new_process():
    identity = "synthetic-thread-" + str(uuid4())
    lease = InstanceLease(identity)
    lease.acquire()
    thread = threading.Thread(target=lease.release)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    code = """
import sys
from nova.gpu.manager import InstanceLease
lease = InstanceLease(sys.argv[1])
lease.acquire()
lease.release()
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code, identity],
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
