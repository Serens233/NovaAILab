"""纯合成WDDM快照与替身reader；不依赖实机负载或管理员权限。"""

import copy

import pytest

from nova.gpu.perf_counters import WindowsGpuCounters, _NativeError, aggregate_counters

NVIDIA = "0x00000000_0x00000011"
AMD = "0x00000000_0x00000022"
SOFTWARE = "0x00000000_0x00000033"


def adapters():
    return [
        {
            "luid": NVIDIA,
            "vendor_id": 4318,
            "name": "Test NVIDIA",
            "software": False,
            "total_dedicated_bytes": 16 * 1024**3,
            "budget_bytes": None,
            "probe_budget_bytes": 15 * 1024**3,
            "probe_current_usage": 0,
            "budget_scope": "current_process",
        },
        {
            "luid": AMD,
            "vendor_id": 4098,
            "name": "Test AMD",
            "software": False,
            "total_dedicated_bytes": 512 * 1024**2,
            "budget_bytes": None,
            "probe_budget_bytes": None,
            "probe_current_usage": None,
            "budget_scope": "unknown",
        },
        {"luid": SOFTWARE, "vendor_id": 5140, "name": "Test software", "software": True},
    ]


def engine(pid, luid, label, *, node=0, index=0):
    return f"pid_{pid}_luid_{luid}_phys_{node}_eng_{index}_engtype_{label}"


def memory(pid, luid, *, node=0):
    return f"pid_{pid}_luid_{luid}_phys_{node}"


def raw(**updates):
    return {"engine": [], "process_memory": [], "adapter_memory": [], **updates}


def test_complete_luid_keeps_nvidia_and_igpu_separate_and_does_not_sum_engines():
    snapshot = aggregate_counters(
        raw(
            engine=[
                (engine(123, NVIDIA, "3D"), 55),
                (engine(123, NVIDIA, "3D", index=1), 60),
                (engine(123, AMD, "3D"), 80),
            ],
            process_memory=[(memory(123, NVIDIA), 100), (memory(123, AMD), 200)],
        ),
        adapters(),
    )
    process = snapshot["processes"][123]
    assert snapshot["available"]
    assert process["engines"]["3D"] == 80
    assert process["per_adapter"][NVIDIA]["engines"]["3D"] == 60
    assert process["per_adapter"][NVIDIA]["dedicated_bytes"] == 100
    assert process["per_adapter"][AMD]["dedicated_bytes"] == 200
    assert process["dedicated_bytes"] == 300
    assert set(process["adapters"]) == {NVIDIA, AMD}


def test_luid_high_half_is_part_of_identity_not_only_low_half():
    other = "0x00000001_0x00000011"
    metadata = adapters()
    metadata.append({**metadata[0], "luid": other})
    snapshot = aggregate_counters(
        raw(process_memory=[(memory(123, NVIDIA), 100), (memory(123, other), 999)]), metadata
    )
    assert snapshot["processes"][123]["per_adapter"][NVIDIA]["dedicated_bytes"] == 100
    assert snapshot["processes"][123]["per_adapter"][other]["dedicated_bytes"] == 999


def test_duplicate_instances_use_max_while_independent_nodes_sum_memory():
    snapshot = aggregate_counters(
        raw(
            engine=[(engine(123, NVIDIA, "3D"), 40), (engine(123, NVIDIA, "3D") + "#1", 70)],
            process_memory=[
                (memory(123, NVIDIA), 100),
                (memory(123, NVIDIA) + "#1", 150),
                (memory(123, NVIDIA, node=1), 25),
            ],
            adapter_memory=[
                (f"luid_{NVIDIA}_phys_0", 1000),
                (f"luid_{NVIDIA}_phys_0#1", 1500),
                (f"luid_{NVIDIA}_phys_1", 200),
            ],
        ),
        adapters(),
    )
    assert snapshot["processes"][123]["dedicated_bytes"] == 175
    assert snapshot["processes"][123]["engines"]["3D"] == 70
    assert snapshot["adapters"][0]["dedicated_used_bytes"] == 1700


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("3D", "3D"),
        ("High Priority Compute", "Compute"),
        ("Compute_1", "Compute"),
        ("VideoDecode", "VideoDecode"),
        ("Video Decode", "VideoDecode"),
        ("JPEG_Decode_0", "VideoDecode"),
        ("VideoEncode", "VideoEncode"),
        ("Copy", "Copy"),
        ("Security", "Other"),
    ],
)
def test_engine_kinds_do_not_confuse_compute_or_video_with_3d(label, expected):
    snapshot = aggregate_counters(raw(engine=[(engine(123, NVIDIA, label), 12)]), adapters())
    assert snapshot["processes"][123]["engines"] == {expected: 12}


def test_unknown_luid_is_unavailable_instead_of_assigned_to_nvidia():
    unknown = "0x00000000_0x00000099"
    snapshot = aggregate_counters(
        raw(
            engine=[(engine(123, unknown, "3D"), 90)], process_memory=[(memory(123, unknown), 1234)]
        ),
        adapters(),
    )
    assert not snapshot["available"]
    assert snapshot["errors"] == ["ADAPTER_MAPPING_UNAVAILABLE"]
    assert 123 not in snapshot["processes"]


def test_kernel_pid_zero_is_ignored_instead_of_assigned_to_a_user_process():
    snapshot = aggregate_counters(raw(engine=[(engine(0, NVIDIA, "3D"), 90)]), adapters())
    assert snapshot["available"]
    assert snapshot["processes"] == {}


def test_software_adapter_is_known_but_excluded_without_invalidating_hardware():
    snapshot = aggregate_counters(
        raw(
            engine=[(engine(123, SOFTWARE, "3D"), 90)],
            process_memory=[(memory(123, SOFTWARE), 1234)],
            adapter_memory=[(f"luid_{SOFTWARE}_phys_0", 1234)],
        ),
        adapters(),
    )
    assert snapshot["available"]
    assert snapshot["errors"] == []
    assert snapshot["processes"] == {}
    assert len(snapshot["adapters"]) == 2


def test_probe_process_budget_is_never_reported_as_global_budget_or_global_usage():
    snapshot = aggregate_counters(raw(adapter_memory=[(f"luid_{NVIDIA}_phys_0", 1000)]), adapters())
    nvidia = snapshot["adapters"][0]
    assert nvidia["budget_bytes"] is None
    assert nvidia["probe_budget_bytes"] == 15 * 1024**3
    assert nvidia["budget_scope"] == "current_process"
    assert nvidia["probe_current_usage"] == 0
    assert nvidia["dedicated_used_bytes"] == 1000


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True, "private-invalid"])
def test_invalid_values_are_unknown_and_never_become_zero_or_privacy_leak(value):
    snapshot = aggregate_counters(raw(engine=[(engine(123, NVIDIA, "3D"), value)]), adapters())
    assert not snapshot["available"]
    assert snapshot["errors"] == ["PDH_INSTANCE_INVALID"]
    assert snapshot["processes"] == {}
    assert "private-invalid" not in str(snapshot)


def test_counter_failure_and_missing_memory_are_not_reported_as_zero():
    snapshot = aggregate_counters(
        raw(engine=[(engine(123, NVIDIA, "3D"), 90)], process_memory=None),
        adapters(),
        errors=["PDH_PROCESS_MEMORY_UNAVAILABLE"],
    )
    assert not snapshot["available"]
    assert snapshot["processes"][123]["dedicated_bytes"] is None
    assert snapshot["adapters"][0]["dedicated_used_bytes"] is None


def test_format_over_100_is_clamped_not_added_to_other_engines():
    snapshot = aggregate_counters(raw(engine=[(engine(123, NVIDIA, "3D"), 120)]), adapters())
    assert snapshot["processes"][123]["engines"]["3D"] == 100


class FakeReader:
    def __init__(self):
        self.reads = 0
        self.closed = False
        self.failure = False

    def read(self):
        self.reads += 1
        if self.failure:
            raise _NativeError("not public private failure detail")
        return raw(), self.reads == 1, []

    def close(self):
        self.closed = True


def test_collector_reuses_query_has_warmup_and_refreshes_adapter_metadata_without_sleep(
    monkeypatch,
):
    reader = FakeReader()
    opened = []
    metadata_reads = []

    def factory():
        opened.append(True)
        return reader

    def metadata():
        metadata_reads.append(True)
        return copy.deepcopy(adapters())

    def forbidden_sleep(*_):
        pytest.fail("持续query不应每个sample阻塞sleep")

    monkeypatch.setattr("time.sleep", forbidden_sleep)
    collector = WindowsGpuCounters(reader_factory=factory, adapter_provider=metadata)
    assert not opened
    first, second = collector.sample(), collector.sample()
    assert first["available"] and first["warming_up"]
    assert second["available"] and not second["warming_up"]
    assert len(opened) == 1 and len(metadata_reads) == 2
    collector.close()
    collector.close()
    assert reader.closed
    assert collector.sample()["errors"] == ["COLLECTOR_CLOSED"]


def test_native_failure_is_fixed_code_and_query_is_closed_before_retry():
    reader = FakeReader()
    reader.failure = True
    collector = WindowsGpuCounters(reader_factory=lambda: reader, adapter_provider=adapters)
    snapshot = collector.sample()
    assert not snapshot["available"]
    assert snapshot["errors"] == ["WINDOWS_GPU_COUNTERS_UNAVAILABLE"]
    assert snapshot["processes"] == {}
    assert reader.closed
    assert "private" not in str(snapshot)
    collector.close()


def test_dxgi_failure_is_unknown_and_never_guesses_adapter():
    def unavailable():
        raise OSError("private OS detail")

    collector = WindowsGpuCounters(reader_factory=FakeReader, adapter_provider=unavailable)
    snapshot = collector.sample()
    assert not snapshot["available"]
    assert snapshot["adapters"] == []
    assert "private OS detail" not in str(snapshot)
    collector.close()
