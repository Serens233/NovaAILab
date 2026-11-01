"""组合只读证据判断游戏候选；不加载模型、不控制进程或调用收费服务。"""

import math
import ntpath
import os
import re
from pathlib import PureWindowsPath

# 这些名称只用于排除通用桌面/计算应用，不作为识别某款游戏的前提。
_EXCLUDED = frozenset(
    name.casefold()
    for name in (
        "chrome.exe",
        "msedge.exe",
        "firefox.exe",
        "brave.exe",
        "opera.exe",
        "opera_gx.exe",
        "vivaldi.exe",
        "iexplore.exe",
        "duckduckgo.exe",
        "arc.exe",
        "msedgewebview2.exe",
        "cefclient.exe",
        "QtWebEngineProcess.exe",
        "electron.exe",
        "ChatGPT.exe",
        "Codex.exe",
        "Code.exe",
        "Code - Insiders.exe",
        "Cursor.exe",
        "Windsurf.exe",
        "devenv.exe",
        "blender.exe",
        "obs.exe",
        "obs64.exe",
        "vlc.exe",
        "mpv.exe",
        "potplayer.exe",
        "potplayer64.exe",
        "mpc-hc.exe",
        "mpc-hc64.exe",
        "mpc-be.exe",
        "mpc-be64.exe",
        "wmplayer.exe",
        "Video.UI.exe",
        "Microsoft.Media.Player.exe",
        "dwm.exe",
        "explorer.exe",
        "sihost.exe",
        "ShellExperienceHost.exe",
        "StartMenuExperienceHost.exe",
        "SearchHost.exe",
        "TextInputHost.exe",
        "LockApp.exe",
        "SystemSettings.exe",
        "ApplicationFrameHost.exe",
        "wallpaper.exe",
        "wallpaper32.exe",
        "wallpaper64.exe",
        "wallpaperui.exe",
        "wallpaperui32.exe",
        "webwallpaper32.exe",
        "webwallpaper64.exe",
        "steam.exe",
        "steamwebhelper.exe",
        "EpicGamesLauncher.exe",
        "EpicWebHelper.exe",
        "upc.exe",
        "uplay.exe",
        "UbisoftConnect.exe",
        "GalaxyClient.exe",
        "GameBar.exe",
        "GameBarFTServer.exe",
        "EA.exe",
        "EADesktop.exe",
        "EALauncher.exe",
        "Battle.net.exe",
        "Battle.net Helper.exe",
        "RSI Launcher.exe",
        "node.exe",
        "py.exe",
        "pypy.exe",
        "pypy3.exe",
        "ollama.exe",
        "llama-server.exe",
        "llama-cli.exe",
        "vllm.exe",
        "torchrun.exe",
        "accelerate.exe",
        "deepspeed.exe",
        "jupyter.exe",
        "jupyter-lab.exe",
        "jupyter-notebook.exe",
        "nova.exe",
        "nova-server.exe",
        "nova-gpu-manager.exe",
        "setup.exe",
        "install.exe",
        "uninstall.exe",
        "unins000.exe",
        "dxsetup.exe",
        "crashpad_handler.exe",
        "crashhandler.exe",
        "unitycrashhandler32.exe",
        "unitycrashhandler64.exe",
    )
)
_PYTHON = re.compile(r"python(?:w|\d[\d.]*)?\.exe\Z", re.IGNORECASE)
_EXCLUDED_PARTS = {"wallpaper_engine", "wallpaper engine", "novaailab", "novatraining"}
_MIN_3D_PERCENT = 1.0


def _positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _path(value):
    if not isinstance(value, str) or not value or "\x00" in value:
        return None
    if not PureWindowsPath(value).is_absolute():
        return None
    return ntpath.normcase(ntpath.normpath(value))


def _percent(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return min(float(value), 100.0)


class SignalClassifier:
    """置信度是规则等级，不是经过标定的概率；默认连续三个有效采样才确认。"""

    def __init__(self, confirmation_samples=3, own_pid=None):
        if not _positive_int(confirmation_samples):
            raise ValueError("确认采样数必须为正整数")
        if own_pid is not None and not _positive_int(own_pid):
            raise ValueError("自身PID必须为正整数")
        self.confirmation_samples = confirmation_samples
        self.own_pid = os.getpid() if own_pid is None else own_pid
        self._streaks = {}
        self._confirmed = {}

    def _result(self, confirmed, candidates, degraded, *, ready, uncertain=()):
        return {
            "matched_processes": sorted({item["name"] for item in confirmed}),
            "confirmed": sorted(confirmed, key=lambda item: item["pid"]),
            "candidates": sorted(candidates, key=lambda item: item["pid"]),
            "degraded": sorted(degraded),
            "state_ready": ready,
            "uncertain_confirmed_pids": sorted(uncertain),
        }

    def evaluate(self, snapshot, catalog):
        degraded = set()
        rows = snapshot.get("processes") if isinstance(snapshot, dict) else None
        if (
            not isinstance(snapshot, dict)
            or snapshot.get("available") is not True
            or not isinstance(rows, list)
        ):
            self._streaks.clear()
            return self._result(
                [], [], {"PROCESS_SNAPSHOT_UNAVAILABLE"}, ready=False, uncertain=self._confirmed
            )
        records = {}
        for item in rows:
            if not isinstance(item, dict) or not _positive_int(item.get("pid")):
                degraded.add("PROCESS_RECORD_INVALID")
                continue
            if item["pid"] in records:
                self._streaks.clear()
                return self._result(
                    [], [], {"PROCESS_SNAPSHOT_AMBIGUOUS"}, ready=False, uncertain=self._confirmed
                )
            records[item["pid"]] = item

        excluded_pids = {self.own_pid}
        changed = True
        while changed:
            changed = False
            for pid, record in records.items():
                if record.get("ppid") in excluded_pids and pid not in excluded_pids:
                    excluded_pids.add(pid)
                    changed = True

        gpu = snapshot.get("gpu") or {}
        gpu_ready = (
            isinstance(gpu, dict)
            and gpu.get("available") is True
            and gpu.get("warming_up") is not True
        )
        if not gpu_ready:
            degraded.add("GPU_COUNTERS_UNAVAILABLE")
        gpu_processes = gpu.get("processes", {}) if isinstance(gpu, dict) else {}
        if not isinstance(gpu_processes, dict):
            gpu_processes = {}
            gpu_ready = False
            degraded.add("GPU_COUNTERS_UNAVAILABLE")
        foreground = snapshot.get("foreground") or {}
        if not isinstance(foreground, dict):
            foreground = {}
            degraded.add("FOREGROUND_UNAVAILABLE")

        confirmed = []
        candidates = []
        next_streaks = {}
        next_confirmed = {}
        uncertain = []
        for pid, record in records.items():
            path = _path(record.get("path"))
            created = record.get("created")
            previous = self._confirmed.get(pid)
            if previous and _positive_int(created) and created != previous[0][1]:
                previous = None  # 创建时间改变已经证明旧进程退出，即使新路径仍不可读。
            raw_name = record.get("name")
            name = raw_name.casefold() if isinstance(raw_name, str) else ""
            if (
                not name
                or PureWindowsPath(name).name != name
                or not name.endswith(".exe")
                or (path and PureWindowsPath(path).name != name)
            ):
                degraded.add("PROCESS_RECORD_INVALID")
                if previous:
                    next_confirmed[pid] = previous
                    uncertain.append(pid)
                    degraded.add("CONFIRMED_IDENTITY_UNAVAILABLE")
                continue
            if (
                pid in excluded_pids
                or name in _EXCLUDED
                or _PYTHON.fullmatch(name)
                or name.startswith("crashreport")
                or (path and _EXCLUDED_PARTS.intersection(PureWindowsPath(path).parts))
            ):
                continue
            known = None
            if path:
                try:
                    known = catalog.match(path)
                except Exception:
                    degraded.add("GAME_CATALOG_UNAVAILABLE")
            if known is not None and (
                not isinstance(known, dict) or known.get("category") != "game"
            ):
                continue
            if known and known.get("source") not in {"gameconfig", "steam", "epic"}:
                known = None
                degraded.add("GAME_CATALOG_RECORD_INVALID")
            identity = (pid, created, path) if _positive_int(created) and path else None
            if previous is not None:
                old_identity, old_record = previous
                if identity is None:
                    # 已确认进程仍存在，但当前身份不可验证；交给调度器保留保护而非伪造退出。
                    next_confirmed[pid] = previous
                    uncertain.append(pid)
                    degraded.add("CONFIRMED_IDENTITY_UNAVAILABLE")
                    continue
                if identity == old_identity:
                    saved = {
                        **old_record,
                        "signals": [*old_record["signals"], "identity_still_running"],
                    }
                    confirmed.append(saved)
                    next_confirmed[pid] = previous
                    continue

            front = (
                foreground.get("pid") == pid
                and foreground.get("visible") is True
                and foreground.get("minimized") is not True
            )
            fullscreen = front and (
                foreground.get("fullscreen") is True or foreground.get("borderless") is True
            )
            render = record.get("render_api") is True
            counters = gpu_processes.get(pid, gpu_processes.get(str(pid), {}))
            engines = counters.get("engines", {}) if isinstance(counters, dict) else {}
            if not isinstance(engines, dict):
                engines = {}
            three_d = _percent(engines.get("3D", 0)) if gpu_ready else None
            video = _percent(engines.get("VideoDecode", 0)) if gpu_ready else None
            busy_3d = three_d is not None and three_d >= _MIN_3D_PERCENT
            signals = []
            source = "heuristic"
            confidence = 0.0
            eligible = False
            if known and known.get("exact") is True:
                source = known["source"]
                eligible = render and (busy_3d or front)
                confidence = 0.95 if gpu_ready else 0.85
                signals = ["exact_game_path", "render_api"]
                if not gpu_ready and eligible:
                    signals.append("counter_unavailable_foreground_fallback")
            elif known:
                source = known["source"]
                eligible = front and render and busy_3d
                confidence = 0.85
                signals = ["game_install_root", "foreground", "render_api", "sustained_3d"]
            else:
                eligible = fullscreen and render and busy_3d and (video is None or three_d > video)
                confidence = 0.8
                signals = ["foreground_fullscreen_or_borderless", "render_api", "sustained_3d"]
            if not eligible:
                continue
            if busy_3d and "sustained_3d" not in signals:
                signals.append("sustained_3d")
            if front and "foreground" not in signals:
                signals.append("foreground")
            count = self._streaks.get(identity, 0) + 1 if identity is not None else 0
            entry = {
                "pid": pid,
                "name": name,
                "confidence": confidence,
                "source": source,
                "signals": signals,
                "sample_count": count,
                "required_samples": self.confirmation_samples,
            }
            if identity is None:
                degraded.add("PROCESS_IDENTITY_INCOMPLETE")
                entry["signals"] = [*signals, "identity_unavailable_not_confirmed"]
                candidates.append(entry)
                continue
            next_streaks[identity] = count
            if count >= self.confirmation_samples:
                confirmed.append(entry)
                next_confirmed[pid] = (identity, entry)
            else:
                candidates.append(entry)
        self._streaks = next_streaks
        self._confirmed = next_confirmed
        return self._result(
            confirmed, candidates, degraded, ready=not uncertain, uncertain=uncertain
        )
