"""评估工具默认离线，白名单、固定题集、资源门禁与报告不伪造人工评分。"""

import importlib.util
import io
import json
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "harness" / "evaluate_phase2.py"
SPEC = importlib.util.spec_from_file_location("nova_phase2_eval", SCRIPT)
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


@pytest.fixture(autouse=True)
def synthetic_source_root(tmp_path, monkeypatch):
    # 即使Windows的TEMP回退到仓库内，Mock输出测试也使用独立的合成源码边界。
    # 默认题集路径仍固定为真实只读文件；生产源码输出保护本身不被关闭。
    monkeypatch.setattr(evaluation, "ROOT", tmp_path / "synthetic-source")


class Stream(io.BytesIO):
    status = 200


class MockClient:
    def __init__(self):
        self.calls = []
        self.threads = {}
        self.loaded = []
        self.blocked = False
        self.mode = "AUTO"

    def status(self):
        return {
            "mode": self.mode,
            "local_requests_blocked": self.blocked,
            "active_requests": 0,
            "pending_unloads": 0,
            "training": {"state": "idle"},
            "backend": {"models": self.loaded},
            "fault": None,
            "last_operation": {"load_duration_ns": 12, "tokens_per_second": 3},
        }

    def call(self, path, payload=None):
        self.calls.append((path, payload))
        if path == "/api/gpu/status":
            return 200, self.status()
        if path == "/api/v1/models":
            return 200, {
                "models": [{"id": m, "available": True} for m in evaluation.ALLOWED_MODELS]
            }
        if path == "/api/v1/threads":
            identity = str(uuid4())
            self.threads[identity] = []
            return 201, {"id": identity}
        if path.startswith("/api/v1/threads/"):
            return 200, {"messages": list(self.threads[path.rsplit("/", 1)[1]])}
        if path == "/api/v1/chat" and "canon" in payload:
            return 422, {"error": {"code": "VALIDATION_ERROR"}}
        if path == "/api/v1/chat/cancel":
            return 200, {"cancel_requested": True}
        if path == "/api/gpu/unload":
            self.loaded = []
            self.mode = "GAMING"
            return 200, {"unload_verified": True, "backend": {"models": []}, "mode": self.mode}
        raise AssertionError("unexpected synthetic endpoint")

    @contextmanager
    def stream(self, payload):
        self.calls.append(("/api/v1/chat", payload))
        user = {"id": str(uuid4()), "content": payload["content"], "role": "user"}
        reply = {
            "id": str(uuid4()),
            "content": "合成回复",
            "role": "assistant",
            "status": "complete",
        }
        self.threads[payload["thread_id"]].extend([user, reply])
        self.loaded = [{"name": payload["model"], "size_vram": 1}]
        events = [
            {"type": "meta"},
            {"type": "delta", "text": reply["content"]},
            {"type": "done", "message": reply},
        ]
        raw = "".join("data: " + json.dumps(e, ensure_ascii=False) + "\n\n" for e in events)
        with Stream(raw.encode("utf-8")) as response:
            yield response


def test_frozen_suite_category_counts_and_real_cases():
    suite, digest = evaluation.load_suite()
    counts = {
        key: sum(c["category"] == key for c in suite["cases"])
        for key in evaluation.CATEGORY_MINIMUMS
    }
    assert counts == evaluation.CATEGORY_MINIMUMS
    assert len(suite["cases"]) == 40
    assert sum(c["real_selected"] for c in suite["cases"]) == 12
    assert digest == "ed760d8c4148ef0ca12e6c2031e57f030968b8b8141cbf7a08831d8636b91bdc"


def test_modified_suite_refuses_execution(tmp_path):
    target = tmp_path / "cases.json"
    target.write_bytes(evaluation.DEFAULT_CASES.read_bytes() + b" ")
    target.with_suffix(".sha256").write_bytes(
        evaluation.DEFAULT_CASES.with_suffix(".sha256").read_bytes()
    )
    with pytest.raises(evaluation.EvaluationError):
        evaluation.load_suite(target)


@pytest.mark.parametrize(
    "model",
    [
        "qwen3.8:27b",
        "qwen3.8:27b-q3_k_xl",
        "hf.co/unsloth/Qwen3.8-27B-GGUF:UD-Q3_K_XL",
        "deepseek",
        "other:model",
    ],
)
def test_model_whitelist_excludes_27b_cloud_and_unknown(model):
    with pytest.raises(evaluation.EvaluationError):
        evaluation.validate_models([model])


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:8000/path",
        "http://private:secret@127.0.0.1:8000",
        "http://127.0.0.1:8000?token=private",
        "http://example.com:8000",
    ],
)
def test_api_address_cannot_send_token_to_other_hosts(url):
    with pytest.raises(evaluation.EvaluationError):
        evaluation.validate_base_url(url)


def test_dry_run_needs_no_token_or_network(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        evaluation, "NovaClient", lambda *a: pytest.fail("dry run must not access API")
    )
    result = evaluation.main(["--output-dir", str(tmp_path / "not-created")])
    assert result == 0
    assert not (tmp_path / "not-created").exists()
    plan = json.loads(capsys.readouterr().out)
    assert plan["model_calls"] == 0
    assert plan["cases_per_model"] == 12


def test_gaming_stops_before_thread_or_model_request(tmp_path):
    suite, digest = evaluation.load_suite()
    client = MockClient()
    client.blocked, client.mode = True, "GAMING"
    with pytest.raises(evaluation.EvaluationError):
        evaluation.execute(client, list(evaluation.ALLOWED_MODELS), suite, digest, tmp_path / "out")
    assert client.calls == [("/api/gpu/status", None)]
    assert not (tmp_path / "out").exists()


def test_existing_model_is_not_taken_over_or_unloaded(tmp_path):
    suite, digest = evaluation.load_suite()
    client = MockClient()
    client.loaded = [{"name": "qwen3.8:27b-q3_k_xl"}]
    with pytest.raises(evaluation.EvaluationError):
        evaluation.execute(client, ["qwen3.5:4b"], suite, digest, tmp_path / "out")
    assert not any(path.endswith(("chat", "unload")) for path, _ in client.calls)


def test_mock_run_records_all_fixed_real_cases_and_leaves_scoring_pending(tmp_path):
    suite, digest = evaluation.load_suite()
    client = MockClient()
    report = evaluation.execute(
        client, list(evaluation.ALLOWED_MODELS), suite, digest, tmp_path / "out"
    )
    assert report["status"] == "completed"
    assert len(report["records"]) == 24
    assert report["cleanup"]["verified"]
    assert client.loaded == []
    assert client.mode == "GAMING"
    assert report["human_acceptance"] == "pending"
    assert all(
        value is None
        for r in report["records"]
        for value in r["scoring"]["manual"]["scores"].values()
    )
    assert all(
        value is None
        for r in report["records"]
        for value in r["scoring"]["ai_assisted"]["scores"].values()
    )
    assert all(r["turns"][0]["request"]["cloud_consent"] is False for r in report["records"])
    assert all(
        r["turns"][0]["request"]["model"] in evaluation.ALLOWED_MODELS for r in report["records"]
    )
    probes = [r for r in report["records"] if "isolated_probe" in r]
    assert len(probes) == 2
    assert all(all(r["automatic_checks"].values()) for r in probes)
    assert len((tmp_path / "out" / "records.jsonl").read_text(encoding="utf-8").splitlines()) == 24


def test_raw_results_cannot_be_written_inside_source_tree():
    suite, digest = evaluation.load_suite()
    with pytest.raises(evaluation.EvaluationError):
        evaluation.execute(
            MockClient(), ["qwen3.5:4b"], suite, digest, evaluation.ROOT / "unsafe-results"
        )


def test_score_objects_do_not_share_mutable_values():
    first, second = evaluation.scores_pending(), evaluation.scores_pending()
    first["manual"]["scores"]["identity_consistency"] = 0
    assert second["manual"]["scores"]["identity_consistency"] is None


def test_unknown_upstream_error_is_not_marked_complete(tmp_path):
    class BrokenClient(MockClient):
        @contextmanager
        def stream(self, payload):
            yield Stream(b'data: {"type":"error","code":"GPU_UPSTREAM_FAILED"}\n\n')

    suite, digest = evaluation.load_suite()
    report = evaluation.execute(BrokenClient(), ["qwen3.5:4b"], suite, digest, tmp_path / "out")
    assert report["status"] == "failed"
    assert len(report["records"]) == 1
    assert report["records"][0]["status"] == "failed"
    assert report["records"][0]["turns"][0]["terminal_error"] == "GPU_UPSTREAM_FAILED"


def test_final_unload_failure_is_not_success(tmp_path):
    class FailedUnload(MockClient):
        def call(self, path, payload=None):
            if path == "/api/gpu/unload":
                return 503, {"unload_verified": False}
            return super().call(path, payload)

    suite, digest = evaluation.load_suite()
    report = evaluation.execute(FailedUnload(), ["qwen3.5:4b"], suite, digest, tmp_path / "out")
    assert report["status"] == "failed"
    assert not report["cleanup"]["verified"]
