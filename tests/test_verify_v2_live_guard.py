"""验收脚本只允许等待空闲排空，不得绕过任何实际保护。"""

import copy

import pytest

from harness.verify_v2_live import check_protection, idle_unload_complete


@pytest.fixture
def healthy():
    return {
        "mode": "AUTO",
        "mode_source": "auto",
        "manual_gaming": False,
        "detected_gaming": False,
        "local_requests_blocked": False,
        "game_detection": {"ready": True, "state": "monitoring", "signals": {"confirmed": []}},
        "memory_pressure": {"active": False},
        "training": {"state": "idle", "lease": None},
        "fault": None,
        "last_operation": {"reason": "idle_timeout"},
        "unload_verified": True,
    }


def test_idle_block_is_only_waited_out(healthy):
    healthy["local_requests_blocked"] = True
    assert idle_unload_complete(healthy) is False
    with pytest.raises(RuntimeError):
        check_protection(healthy)
    healthy["local_requests_blocked"] = False
    assert idle_unload_complete(healthy) is True


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("manual_gaming",), True),
        (("detected_gaming",), True),
        (("mode",), "GAMING"),
        (("mode_source",), "memory_pressure"),
        (("game_detection", "signals", "confirmed"), ["game"]),
        (("game_detection", "ready"), False),
        (("game_detection", "state"), "failed"),
        (("memory_pressure", "active"), True),
        (("fault",), "OLLAMA_UNAVAILABLE"),
        (("training", "state"), "reserved"),
        (("training", "lease"), {"id": "training"}),
    ],
)
@pytest.mark.parametrize("blocked", [False, True])
def test_idle_observation_never_ignores_real_protection(healthy, path, value, blocked):
    sample = copy.deepcopy(healthy)
    sample["local_requests_blocked"] = blocked
    target = sample
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(RuntimeError):
        idle_unload_complete(sample)


def test_manual_exception_does_not_hide_other_protection(healthy):
    healthy.update(mode="GAMING", mode_source="manual", manual_gaming=True)
    healthy["local_requests_blocked"] = True
    assert check_protection(healthy, allow_manual=True)
    healthy["training"]["state"] = "reserved"
    with pytest.raises(RuntimeError):
        check_protection(healthy, allow_manual=True)
