"""多信号分类器行为验证；不读取真实进程、不启动游戏或GPU任务。"""

from copy import deepcopy

import pytest

from nova.gpu.classifier import SignalClassifier


class Catalog:
    def __init__(self, result=None):
        self.result = result

    def match(self, path):
        return self.result


def snapshot(
    name="practice-game.exe",
    pid=123,
    created=100,
    path=None,
    *,
    front=True,
    fullscreen=True,
    borderless=False,
    render=True,
    three_d=20,
    compute=0,
    video=0,
):
    return {
        "available": True,
        "processes": [
            {
                "pid": pid,
                "ppid": 1,
                "created": created,
                "name": name,
                "path": path or rf"C:\Games\Practice\{name}",
                "render_api": render,
            }
        ],
        "foreground": {
            "pid": pid if front else 999,
            "visible": True,
            "minimized": False,
            "fullscreen": fullscreen,
            "borderless": borderless,
        },
        "gpu": {
            "available": True,
            "warming_up": False,
            "processes": {
                pid: {
                    "engines": {"3D": three_d, "Compute": compute, "VideoDecode": video},
                    "dedicated_bytes": 1024,
                }
            },
        },
    }


def repeatedly(classifier, sample, catalog=None, count=3):
    result = None
    for _ in range(count):
        result = classifier.evaluate(deepcopy(sample), catalog or Catalog())
    return result


@pytest.mark.parametrize("source", ["steam", "epic", "gameconfig"])
def test_indexed_game_needs_consecutive_render_evidence(source):
    classifier = SignalClassifier(own_pid=9999)
    catalog = Catalog({"source": source, "exact": False, "category": "game"})
    sample = snapshot(fullscreen=False)
    assert classifier.evaluate(sample, catalog)["confirmed"] == []
    assert classifier.evaluate(sample, catalog)["confirmed"] == []
    result = classifier.evaluate(sample, catalog)
    assert result["matched_processes"] == ["practice-game.exe"]
    assert result["confirmed"][0]["source"] == source
    assert result["confirmed"][0]["sample_count"] == 3


@pytest.mark.parametrize("fullscreen,borderless", [(True, False), (False, True)])
def test_unknown_game_fullscreen_or_borderless_multi_signal(fullscreen, borderless):
    result = repeatedly(
        SignalClassifier(own_pid=9999), snapshot(fullscreen=fullscreen, borderless=borderless)
    )
    assert result["confirmed"][0]["source"] == "heuristic"
    assert result["confirmed"][0]["confidence"] == 0.8


@pytest.mark.parametrize(
    "name",
    [
        "chrome.exe",
        "msedge.exe",
        "firefox.exe",
        "vlc.exe",
        "mpv.exe",
        "dwm.exe",
        "wallpaper64.exe",
        "steamwebhelper.exe",
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
        "python.exe",
        "python3.14.exe",
        "pythonw.exe",
        "llama-server.exe",
        "ollama.exe",
        "vllm.exe",
        "nova.exe",
        "node.exe",
    ],
)
def test_common_non_games_excluded_even_if_indexed_fullscreen_high_3d(name):
    catalog = Catalog({"source": "gameconfig", "exact": True, "category": "game"})
    result = repeatedly(
        SignalClassifier(own_pid=9999), snapshot(name=name, three_d=99, compute=99), catalog
    )
    assert result["confirmed"] == []
    assert result["candidates"] == []


@pytest.mark.parametrize("three_d,compute,video", [(0, 99, 0), (0, 0, 99), (2, 0, 70)])
def test_compute_or_video_activity_does_not_identify_unknown_game(three_d, compute, video):
    result = repeatedly(
        SignalClassifier(own_pid=9999), snapshot(three_d=three_d, compute=compute, video=video)
    )
    assert result["confirmed"] == []


@pytest.mark.parametrize("change", ["render", "foreground", "fullscreen", "3d"])
def test_unknown_game_requires_all_signals(change):
    sample = snapshot()
    if change == "render":
        sample["processes"][0]["render_api"] = False
    elif change == "foreground":
        sample["foreground"]["pid"] = 777
    elif change == "fullscreen":
        sample["foreground"]["fullscreen"] = False
    else:
        sample["gpu"]["processes"][123]["engines"]["3D"] = 0
    assert repeatedly(SignalClassifier(own_pid=9999), sample)["confirmed"] == []


def test_library_root_alone_and_tool_or_helper_cannot_become_game():
    for catalog, sample in (
        (Catalog({"source": "steam", "exact": False, "category": "game"}), snapshot(render=False)),
        (Catalog({"source": "steam", "exact": False, "category": "tool"}), snapshot()),
        (
            Catalog({"source": "steam", "exact": False, "category": "game"}),
            snapshot(name="setup.exe"),
        ),
    ):
        assert repeatedly(SignalClassifier(own_pid=9999), sample, catalog)["confirmed"] == []


def test_nova_process_and_descendants_excluded_without_reading_cmdline():
    sample = snapshot(pid=777)
    sample["processes"].extend(
        [
            {"pid": 500, "ppid": 1, "name": "host.exe", "path": r"C:\Nova\host.exe", "created": 1},
            {
                "pid": 600,
                "ppid": 500,
                "name": "child.exe",
                "path": r"C:\Nova\child.exe",
                "created": 2,
            },
        ]
    )
    sample["processes"][0]["ppid"] = 600
    result = repeatedly(SignalClassifier(own_pid=500), sample)
    assert result["confirmed"] == []


def test_wallpaper_and_ai_workspace_paths_are_excluded():
    for path in (
        r"C:\Steam\steamapps\common\wallpaper_engine\renamed.exe",
        r"E:\NovaAILab\runtime\renamed.exe",
    ):
        result = repeatedly(SignalClassifier(own_pid=9999), snapshot(name="renamed.exe", path=path))
        assert result["confirmed"] == []


def test_confirmed_game_survives_alt_tab_minimize_and_counter_loss_until_exit():
    classifier = SignalClassifier(own_pid=9999)
    sample = snapshot()
    assert repeatedly(classifier, sample)["confirmed"]
    sample["foreground"].update(pid=999, minimized=True, fullscreen=False)
    sample["gpu"] = {"available": False}
    result = classifier.evaluate(sample, Catalog())
    assert result["matched_processes"] == ["practice-game.exe"]
    assert "identity_still_running" in result["confirmed"][0]["signals"]
    sample["processes"] = []
    assert classifier.evaluate(sample, Catalog())["confirmed"] == []


@pytest.mark.parametrize("field,value", [("created", 101), ("path", r"C:\Other\practice-game.exe")])
def test_pid_reuse_or_image_change_does_not_inherit_confirmation(field, value):
    classifier = SignalClassifier(own_pid=9999)
    sample = snapshot()
    repeatedly(classifier, sample)
    sample["processes"][0][field] = value
    result = classifier.evaluate(sample, Catalog())
    assert result["confirmed"] == []
    assert result["candidates"][0]["sample_count"] == 1


def test_missing_identity_never_accumulates_new_unknown_game():
    for field in ("path", "created"):
        sample = snapshot()
        sample["processes"][0][field] = None
        result = repeatedly(SignalClassifier(own_pid=9999), sample, count=10)
        assert result["confirmed"] == []
        assert "PROCESS_IDENTITY_INCOMPLETE" in result["degraded"]


def test_previously_confirmed_identity_unreadable_is_unknown_not_exit():
    classifier = SignalClassifier(own_pid=9999)
    sample = snapshot()
    repeatedly(classifier, sample)
    missing = deepcopy(sample)
    missing["processes"][0]["created"] = None
    result = classifier.evaluate(missing, Catalog())
    assert not result["state_ready"]
    assert result["uncertain_confirmed_pids"] == [123]
    assert result["confirmed"] == []
    assert classifier.evaluate(sample, Catalog())["matched_processes"] == ["practice-game.exe"]


def test_catalog_tool_exclusion_precedes_already_confirmed_latch():
    classifier = SignalClassifier(own_pid=9999)
    sample = snapshot()
    catalog = Catalog({"source": "steam", "category": "game", "exact": False})
    assert repeatedly(classifier, sample, catalog)["confirmed"]
    catalog.result["category"] = "tool"
    assert classifier.evaluate(sample, catalog)["confirmed"] == []


@pytest.mark.parametrize("change", ["missing_name", "path_name_mismatch"])
def test_bad_identity_of_existing_pid_keeps_unknown_instead_of_reporting_exit(change):
    classifier = SignalClassifier(own_pid=9999)
    sample = snapshot()
    repeatedly(classifier, sample)
    broken = deepcopy(sample)
    if change == "missing_name":
        broken["processes"][0]["name"] = None
    else:
        broken["processes"][0]["path"] = r"C:\Different\different.exe"
    result = classifier.evaluate(broken, Catalog())
    assert result["state_ready"] is False
    assert result["uncertain_confirmed_pids"] == [123]
    assert classifier.evaluate(sample, Catalog())["confirmed"]


def test_pid_reuse_with_unreadable_new_path_is_not_old_confirmed_process():
    classifier = SignalClassifier(own_pid=9999)
    sample = snapshot()
    repeatedly(classifier, sample)
    sample["processes"][0].update(created=999, path=None)
    result = classifier.evaluate(sample, Catalog())
    assert result["state_ready"] is True
    assert result["confirmed"] == []
    assert result["uncertain_confirmed_pids"] == []


def test_counter_unavailable_exact_path_foreground_render_can_degrade_but_unknown_cannot():
    sample = snapshot(fullscreen=False)
    sample["gpu"] = {"available": False}
    exact = Catalog({"source": "epic", "exact": True, "category": "game"})
    root = Catalog({"source": "steam", "exact": False, "category": "game"})
    result = repeatedly(SignalClassifier(own_pid=9999), sample, exact)
    assert result["confirmed"]
    assert "GPU_COUNTERS_UNAVAILABLE" in result["degraded"]
    assert "counter_unavailable_foreground_fallback" in result["confirmed"][0]["signals"]
    assert repeatedly(SignalClassifier(own_pid=9999), sample, root)["confirmed"] == []
    assert (
        repeatedly(SignalClassifier(own_pid=9999), snapshot_with_no_gpu(), Catalog())["confirmed"]
        == []
    )


def snapshot_with_no_gpu():
    result = snapshot()
    result["gpu"] = {"available": False}
    return result


def test_broken_snapshot_preserves_pending_known_identity_without_reporting_it_alive():
    classifier = SignalClassifier(own_pid=9999)
    sample = snapshot()
    repeatedly(classifier, sample)
    result = classifier.evaluate({"available": False}, Catalog())
    assert not result["state_ready"]
    assert result["uncertain_confirmed_pids"] == [123]
    assert result["confirmed"] == []
    assert classifier.evaluate(sample, Catalog())["confirmed"]


def test_nonconsecutive_evidence_resets_streak():
    classifier = SignalClassifier(own_pid=9999)
    sample = snapshot()
    classifier.evaluate(sample, Catalog())
    classifier.evaluate(sample, Catalog())
    not_full = deepcopy(sample)
    not_full["foreground"]["fullscreen"] = False
    classifier.evaluate(not_full, Catalog())
    assert classifier.evaluate(sample, Catalog())["candidates"][0]["sample_count"] == 1


def test_json_pid_keys_warming_up_and_nonfinite_values_are_conservative():
    sample = snapshot()
    sample["gpu"]["processes"] = {"123": sample["gpu"]["processes"][123]}
    assert repeatedly(SignalClassifier(own_pid=9999), sample)["confirmed"]
    sample["gpu"]["warming_up"] = True
    assert repeatedly(SignalClassifier(own_pid=9999), sample)["confirmed"] == []
    sample["gpu"]["warming_up"] = False
    sample["gpu"]["processes"]["123"]["engines"]["3D"] = float("nan")
    assert repeatedly(SignalClassifier(own_pid=9999), sample)["confirmed"] == []


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "3"])
def test_confirmation_count_must_be_positive_integer(value):
    with pytest.raises(ValueError):
        SignalClassifier(confirmation_samples=value)
