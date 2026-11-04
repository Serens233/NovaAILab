"""固定合成题集的Phase 2候选评估；默认dry-run，只经Nova API调用9B/4B。"""

import argparse
import hashlib
import json
import os
import re
import time
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "evals" / "phase2" / "cases.json"
ALLOWED_MODELS = ("qwen3.5:9b", "qwen3.5:4b")
CATEGORY_MINIMUMS = {
    "ordinary": 8,
    "multiturn": 8,
    "topic_switch": 6,
    "contradiction": 6,
    "injection": 6,
    "isolation_errors_cancel": 6,
}
DIMENSIONS = (
    "identity_consistency",
    "natural_expression",
    "multiturn_continuity",
    "independent_judgment",
    "uncertainty_expression",
    "emotional_appropriateness",
)


class EvaluationError(Exception):
    """固定诊断信息，不携带认证头、环境变量或任意上游正文。"""

    def __init__(self, message, *, record=None):
        super().__init__(message)
        self.record = record


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_base_url(value):
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port is None
            or not 1 <= parsed.port <= 65535
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or any(char.isspace() for char in value)
            or "\\" in value
        ):
            raise ValueError
    except ValueError, TypeError, AttributeError:
        raise EvaluationError("评估地址必须是带端口的127.0.0.1 HTTP根地址") from None
    return f"http://127.0.0.1:{parsed.port}"


def validate_models(models):
    if (
        not models
        or len(set(models)) != len(models)
        or any(m not in ALLOWED_MODELS for m in models)
    ):
        raise EvaluationError("仅允许明确的qwen3.5:9b和qwen3.5:4b；禁止27B及云端")
    return list(models)


def load_suite(path=DEFAULT_CASES):
    path = Path(path)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    expected = path.with_suffix(".sha256").read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or digest != expected:
        raise EvaluationError("题集摘要不匹配，正式运行前必须固定题集")
    suite = json.loads(raw)
    if not isinstance(suite, dict) or suite.get("schema_version") != 1:
        raise EvaluationError("题集版本无效")
    cases = suite.get("cases")
    if not isinstance(cases, list) or not 40 <= len(cases) <= 200:
        raise EvaluationError("题集数量无效")
    identifiers, counts = set(), Counter()
    for case in cases:
        if not isinstance(case, dict):
            raise EvaluationError("题集用例格式无效")
        identifier = case.get("id")
        turns = case.get("turns")
        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{1,70}", identifier)
            or identifier in identifiers
            or case.get("category") not in CATEGORY_MINIMUMS
            or type(case.get("real_selected")) is not bool
            or not isinstance(turns, list)
            or not 1 <= len(turns) <= 6
            or any(not isinstance(t, str) or not t.strip() or len(t) > 4000 for t in turns)
        ):
            raise EvaluationError("题集用例无效或重复")
        if "isolated_probe" in case and (
            not isinstance(case["isolated_probe"], str)
            or not 1 <= len(case["isolated_probe"]) <= 4000
        ):
            raise EvaluationError("隔离探针无效")
        for key in ("cancel_after_first_delta", "invalid_payload_probe"):
            if key in case and type(case[key]) is not bool:
                raise EvaluationError("题集探针标记必须为布尔值")
        identifiers.add(identifier)
        counts[case["category"]] += 1
    if any(counts[key] < minimum for key, minimum in CATEGORY_MINIMUMS.items()):
        raise EvaluationError("题集分类覆盖不足")
    selected = [case for case in cases if case["real_selected"]]
    if len(selected) < 12 or {c["category"] for c in selected} != set(CATEGORY_MINIMUMS):
        raise EvaluationError("真实评估必须固定至少12例并覆盖所有分类")
    return suite, digest


def scores_pending():
    return {
        "automatic": {"scope": "protocol/state observations only", "character_score": None},
        "ai_assisted": {"reviewer": None, "scores": dict.fromkeys(DIMENSIONS), "reasons": {}},
        "manual": {"reviewer": None, "scores": dict.fromkeys(DIMENSIONS), "reasons": {}},
    }


class NovaClient:
    def __init__(self, base_url, token):
        self.base_url = validate_base_url(base_url)
        if not isinstance(token, str) or len(token) < 32:
            raise EvaluationError("执行模式需要独立的有效Nova管理令牌")
        self._token = token
        self._opener = build_opener(ProxyHandler({}), NoRedirect())

    def _request(self, path, payload=None):
        if not path.startswith(("/api/v1/", "/api/gpu/")) or "?" in path or ".." in path:
            raise EvaluationError("评估路径不合法")
        return Request(
            self.base_url + path,
            data=None
            if payload is None
            else json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="GET" if payload is None else "POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self._token,
            },
        )

    def call(self, path, payload=None):
        try:
            with self._opener.open(self._request(path, payload), timeout=300) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise EvaluationError("API响应过大")
                return response.status, json.loads(raw)
        except HTTPError as error:
            return error.code, {"error": {"code": "HTTP_REJECTED"}}
        except URLError, OSError, ValueError:
            raise EvaluationError("Nova API不可用或响应无效") from None

    @contextmanager
    def stream(self, payload):
        try:
            with self._opener.open(self._request("/api/v1/chat", payload), timeout=300) as response:
                yield response
        except HTTPError as error:
            raise EvaluationError("聊天请求被拒绝，HTTP " + str(error.code)) from None
        except URLError, OSError:
            raise EvaluationError("聊天连接中断") from None


def status_snapshot(client):
    code, status = client.call("/api/gpu/status")
    if code != 200 or not isinstance(status, dict):
        raise EvaluationError("无法确认资源状态")
    return status


def guard(status):
    training = status.get("training") or {}
    if (
        status.get("mode") != "AUTO"
        or status.get("local_requests_blocked") is not False
        or status.get("fault")
        or status.get("active_requests") != 0
        or status.get("pending_unloads", 0) != 0
        or training.get("state") != "idle"
    ):
        raise EvaluationError("游戏/训练/故障/活动请求保护生效，停止正向评估")


def new_thread(client, model, case_id):
    code, thread = client.call("/api/v1/threads", {"model": model, "title": "合成评估 " + case_id})
    if code != 201:
        raise EvaluationError("无法创建合成会话")
    try:
        return str(UUID(thread["id"]))
    except ValueError, KeyError, TypeError, AttributeError:
        raise EvaluationError("会话标识无效") from None


def history(client, thread_id):
    code, value = client.call("/api/v1/threads/" + thread_id)
    if code != 200 or not isinstance(value.get("messages"), list):
        raise EvaluationError("无法读取合成会话上下文")
    return value["messages"]


def run_turn(client, thread_id, model, content, *, cancel=False):
    validate_models([model])
    guard(status_snapshot(client))
    before = history(client, thread_id)
    payload = {
        "thread_id": thread_id,
        "model": model,
        "content": content,
        "request_id": str(uuid4()),
        "cloud_consent": False,
        "think": False,
    }
    record = {"request": payload, "history_before": before, "events": [], "first_delta_ms": None}
    started, confirmed = time.monotonic(), False
    try:
        with client.stream(payload) as response:
            if response.status != 200:
                raise EvaluationError("聊天流HTTP状态异常")
            consumed = 0
            for _ in range(20000):
                raw = response.readline(131073)
                consumed += len(raw)
                if len(raw) > 131072 or consumed > 4 * 1024 * 1024:
                    raise EvaluationError("评估消息流过大")
                if not raw:
                    break
                if not raw.startswith(b"data:"):
                    continue
                event = json.loads(raw[5:].strip())
                if not isinstance(event, dict) or event.get("type") not in {
                    "meta",
                    "delta",
                    "done",
                    "error",
                }:
                    raise EvaluationError("评估消息流格式无效")
                record["events"].append(event)
                if event["type"] == "delta":
                    if not isinstance(event.get("text"), str):
                        raise EvaluationError("文本增量无效")
                    if record["first_delta_ms"] is None:
                        record["first_delta_ms"] = (time.monotonic() - started) * 1000
                    if cancel:
                        code, _ = client.call(
                            "/api/v1/chat/cancel",
                            {"thread_id": thread_id, "request_id": payload["request_id"]},
                        )
                        record["cancel_http_status"] = code
                        cancel = False
                if event["type"] == "done":
                    confirmed = True
                    record["output"] = event["message"]["content"]
                    break
                if event["type"] == "error":
                    record["terminal_error"] = event.get("code")
                    confirmed = event.get("code") in {"CHAT_CANCELLED", "CLOUD_CANCELLED"}
                    break
        if not confirmed:
            raise EvaluationError("聊天未确认完成或取消")
        if "output" in record:
            text = "".join(e["text"] for e in record["events"] if e["type"] == "delta")
            if text != record["output"]:
                raise EvaluationError("最终回复与流增量不一致")
        record["elapsed_ms"] = (time.monotonic() - started) * 1000
        record["history_after"] = history(client, thread_id)
        operation = status_snapshot(client).get("last_operation") or {}
        record["provider_metrics"] = {
            name: operation.get(name)
            for name in (
                "load_duration_ns",
                "eval_duration_ns",
                "tokens_per_second",
                "first_token_latency_ms",
                "first_activity_latency_ms",
                "request_duration_ms",
                "prompt_eval_count",
            )
        }
        return record
    except (EvaluationError, ValueError, KeyError, TypeError) as error:
        record["elapsed_ms"] = (time.monotonic() - started) * 1000
        record["failure_type"] = type(error).__name__
        raise EvaluationError("聊天未确认完成，保留部分合成评估记录", record=record) from None
    finally:
        if not confirmed:
            try:
                client.call(
                    "/api/v1/chat/cancel",
                    {"thread_id": thread_id, "request_id": payload["request_id"]},
                )
            except EvaluationError:
                pass


def run_case(client, model, case):
    validate_models([model])
    thread_id = new_thread(client, model, case["id"])
    record = {
        "model": model,
        "case_id": case["id"],
        "category": case["category"],
        "thread_id": thread_id,
        "turns": [],
        "scoring": scores_pending(),
    }
    if case.get("invalid_payload_probe"):
        code, _ = client.call(
            "/api/v1/chat",
            {
                "thread_id": thread_id,
                "model": model,
                "content": case["turns"][0],
                "request_id": str(uuid4()),
                "canon": {"identity": "synthetic forbidden"},
            },
        )
        record["automatic_checks"] = {"untrusted_admin_field_rejected": code == 422}
        if code != 422:
            raise EvaluationError("未受信任的管理字段未被拒绝")
        return record
    try:
        for content in case["turns"]:
            record["turns"].append(
                run_turn(
                    client,
                    thread_id,
                    model,
                    content,
                    cancel=case.get("cancel_after_first_delta", False),
                )
            )
    except EvaluationError as error:
        if error.record is not None:
            record["turns"].append(error.record)
        record["status"] = "failed"
        raise EvaluationError("合成用例未完成", record=record) from None
    if "isolated_probe" in case:
        other = new_thread(client, model, case["id"] + " probe")
        probe = run_turn(client, other, model, case["isolated_probe"])
        left = {m["id"] for m in history(client, thread_id)}
        right = {m["id"] for m in history(client, other)}
        record["isolated_probe"] = {"thread_id": other, "turn": probe}
        record["automatic_checks"] = {
            "stored_message_ids_disjoint": not bool(left & right),
            "probe_started_without_history": probe["history_before"] == [],
        }
        if not all(record["automatic_checks"].values()):
            raise EvaluationError("合成会话隔离检查失败")
    return record


def execute(client, models, suite, digest, output_dir, *, all_cases=False):
    models = validate_models(models)
    output_dir = Path(output_dir).resolve()
    if output_dir.is_relative_to(ROOT):
        raise EvaluationError("原始评估结果必须保存到源码仓库之外")
    initial = status_snapshot(client)
    guard(initial)
    if (initial.get("backend") or {}).get("models") != []:
        raise EvaluationError("运行前存在驻留模型，评估不会接管或卸载它们")
    code, catalog = client.call("/api/v1/models")
    available = {m["id"] for m in catalog.get("models", []) if m.get("available")}
    if code != 200 or not set(models) <= available:
        raise EvaluationError("所选9B/4B尚未安装或不可用，不会下载")
    cases = [c for c in suite["cases"] if all_cases or c["real_selected"]]
    output_dir.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": 1,
        "suite_id": suite["suite_id"],
        "suite_sha256": digest,
        "models": models,
        "cases_per_model": len(cases),
        "started_at_utc": datetime.now(UTC).isoformat(),
        "records": [],
        "status": "running",
        "human_acceptance": "pending",
        "not_verified": [
            "real_game_transition",
            "manual_character_scores",
            "27b_prohibited",
            "cloud_not_called",
        ],
        "cleanup": {"verified": False},
    }
    try:
        with (output_dir / "records.jsonl").open("x", encoding="utf-8") as stream:
            for model in models:
                for case in cases:
                    guard(status_snapshot(client))
                    record = run_case(client, model, case)
                    report["records"].append(record)
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                    stream.flush()
                    print(model + " " + case["id"] + ": recorded", flush=True)
        report["status"] = "completed"
    except (EvaluationError, OSError, ValueError, KeyError, TypeError) as error:
        report["status"] = "failed"
        report["failure_type"] = type(error).__name__
        if isinstance(error, EvaluationError) and error.record is not None:
            report["records"].append(error.record)
    finally:
        try:
            current = status_snapshot(client)
            loaded = (current.get("backend") or {}).get("models")
            if not isinstance(loaded, list) or any(m.get("name") not in models for m in loaded):
                report["cleanup"]["reason"] = "unknown_or_foreign_model_not_touched"
            elif (
                not loaded
                and current.get("active_requests") == 0
                and current.get("pending_unloads", 0) == 0
            ):
                report["cleanup"]["verified"] = True
            else:
                code, cleaned = client.call("/api/gpu/unload", {})
                report["cleanup"] = {
                    "verified": code == 200
                    and cleaned.get("unload_verified") is True
                    and (cleaned.get("backend") or {}).get("models") == [],
                    "final_mode": cleaned.get("mode"),
                    "note": "explicit unload retains original GAMING safety contract; no automatic AUTO override",
                }
        except EvaluationError:
            report["cleanup"]["reason"] = "resource_release_unconfirmed"
        report["finished_at_utc"] = datetime.now(UTC).isoformat()
        if not report["cleanup"]["verified"]:
            report["status"] = "failed"
        (output_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token-env", default="NOVA_GPU_TOKEN")
    parser.add_argument("--models", nargs="+", default=list(ALLOWED_MODELS))
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--all-cases", action="store_true", help="运行全部固定题集；默认每模型12个代表用例"
    )
    parser.add_argument(
        "--execute", action="store_true", help="显式执行真实Nova API；默认仅校验和列出计划"
    )
    args = parser.parse_args(argv)
    try:
        validate_base_url(args.base_url)
        models = validate_models(args.models)
        suite, digest = load_suite(args.cases)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,100}", args.token_env):
            raise EvaluationError("令牌环境变量名无效")
        if not args.execute:
            print(
                json.dumps(
                    {
                        "execute": False,
                        "suite_sha256": digest,
                        "models": models,
                        "cases_per_model": sum(
                            args.all_cases or c["real_selected"] for c in suite["cases"]
                        ),
                        "cloud_calls": False,
                        "model_calls": 0,
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        client = NovaClient(args.base_url, os.environ.get(args.token_env, ""))
        report = execute(client, models, suite, digest, args.output_dir, all_cases=args.all_cases)
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "records": len(report["records"]),
                    "cleanup_verified": report["cleanup"]["verified"],
                    "manual_acceptance": "pending",
                },
                ensure_ascii=False,
            )
        )
        return 0 if report["status"] == "completed" else 1
    except EvaluationError, OSError, ValueError, KeyError, TypeError:
        print("评估未执行或未确认成功，请检查题集、回环地址、权限及资源保护；不输出凭据。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
