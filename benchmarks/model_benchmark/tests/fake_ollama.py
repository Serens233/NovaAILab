"""A tiny in-process Ollama API double used by the unit tests.

It implements only the endpoints the benchmark needs: version, tags, show,
ps, generate (unload), and streaming chat.  It never touches a real model.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

TAGS = {
    "qwen3.5:9b": {
        "size": 6_550_825_550,
        "digest": "56671c2ab9385f9cfcb404638e32cd62d88e3501d44822208363c010179a3c90",
        "parameter_size": "9.0B",
        "quantization_level": "Q4_K_M",
        "context_length": 262144,
    },
    "qwen3.5:4b": {
        "size": 3_324_173_934,
        "digest": "d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04",
        "parameter_size": "4.2B",
        "quantization_level": "Q4_K_M",
        "context_length": 262144,
    },
    "qwen3.8:27b-q3_k_xl": {
        "size": 14_074_001_250,
        "digest": "d4a3fff01c21e9d4bd9fb694c4d2f3f2289b20a133e29fdc24df5cf7bf9c0fcc",
        "parameter_size": "27.3B",
        "quantization_level": "Q3_K_XL",
        "context_length": 262144,
    },
}


class FakeState:
    def __init__(self) -> None:
        self.loaded: dict[str, dict[str, object]] = {}
        self.requests: list[dict[str, object]] = []
        self.unload_calls: list[str] = []
        self.fail_chat = False
        self.malformed = False
        self.thinking = False


def _json_bytes(document: object) -> bytes:
    return json.dumps(document, ensure_ascii=False).encode("utf-8")


def _stream_chunks(model: str, payload: dict[str, object]) -> list[bytes]:
    chunks: list[dict[str, object]] = [
        {
            "model": model,
            "created_at": "2026-11-01T00:00:00Z",
            "message": {"role": "assistant", "content": ""},
            "done": False,
        }
    ]
    if payload.get("think") is True:
        chunks.append(
            {
                "model": model,
                "message": {"role": "assistant", "content": "", "thinking": "内部思考"},
                "done": False,
            }
        )
    for piece in ("你", "好", "，", "世界", "。"):
        chunks.append(
            {
                "model": model,
                "message": {"role": "assistant", "content": piece},
                "done": False,
            }
        )
    chunks.append(
        {
            "model": model,
            "created_at": "2026-11-01T00:00:01Z",
            "message": {"role": "assistant", "content": ""},
            "done": True,
            "done_reason": "stop",
            "total_duration": 200_000_000,
            "load_duration": 20_000_000,
            "prompt_eval_count": 12,
            "prompt_eval_duration": 10_000_000,
            "eval_count": 20,
            "eval_duration": 100_000_000,
        }
    )
    return [_json_bytes(chunk) + b"\n" for chunk in chunks]


class _Handler(BaseHTTPRequestHandler):
    state: FakeState

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        return

    def _send_json(self, document: object, status: int = 200) -> None:
        body = _json_bytes(document)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        if self.path == "/api/version":
            self._send_json({"version": "0.40.13-test"})
            return
        if self.path == "/api/tags":
            self._send_json(
                {
                    "models": [
                        {
                            "name": name,
                            "model": name,
                            "size": info["size"],
                            "digest": info["digest"],
                            "modified_at": "2026-11-01T01:50:41+08:00",
                            "details": {
                                "format": "gguf",
                                "family": "qwen35",
                                "families": ["qwen35"],
                                "parameter_size": info["parameter_size"],
                                "quantization_level": info["quantization_level"],
                            },
                        }
                        for name, info in TAGS.items()
                    ]
                }
            )
            return
        if self.path == "/api/ps":
            models = [
                {
                    "name": name,
                    "model": name,
                    "size": 2_000_000_000,
                    "size_vram": 1_500_000_000,
                    "details": {"quantization_level": TAGS[name]["quantization_level"]},
                }
                for name in self.state.loaded
            ]
            self._send_json({"models": models})
            return
        self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError, json.JSONDecodeError:
            self._send_json({"error": "invalid json"}, status=400)
            return
        if not isinstance(payload, dict):
            self._send_json({"error": "invalid payload"}, status=400)
            return
        model = str(payload.get("model", ""))
        self.state.requests.append({"path": self.path, "payload": payload})
        if self.path == "/api/show":
            info = TAGS.get(model)
            if info is None:
                self._send_json({"error": "model not found"}, status=404)
                return
            self._send_json(
                {
                    "details": {
                        "format": "gguf",
                        "family": "qwen35",
                        "families": ["qwen35"],
                        "parameter_size": info["parameter_size"],
                        "quantization_level": info["quantization_level"],
                        "runner": "llamacpp",
                    },
                    "model_info": {
                        "general.architecture": "qwen35",
                        "qwen35.context_length": info["context_length"],
                        "general.parameter_count": 4_205_751_296,
                        "qwen35.embedding_length": 2560,
                        "qwen35.block_count": 32,
                        "general.quantization_version": 2,
                        "general.license": "apache-2.0",
                        "general.base_model.0.name": "Qwen3.5 4B Base",
                    },
                    "parameters": "temperature 1\ntop_k 20\ntop_p 0.95\npresence_penalty 1.5",
                    "template": "{{ .Prompt }}",
                    "modelfile": "FROM test",
                    "license": "Apache License 2.0",
                    "capabilities": ["completion", "tools", "thinking"],
                    "modified_at": "2026-11-01T01:50:41+08:00",
                }
            )
            return
        if self.path == "/api/generate":
            if payload.get("keep_alive") == 0:
                self.state.unload_calls.append(model)
                self.state.loaded.pop(model, None)
                self._send_json({"model": model, "done": True})
                return
            self._send_json({"error": "generate is not used by tests"}, status=400)
            return
        if self.path == "/api/chat":
            if self.state.fail_chat:
                self._send_json({"error": "simulated failure"}, status=500)
                return
            if model not in self.state.loaded:
                time.sleep(0.02)
                self.state.loaded[model] = {"loaded": True}
            if self.state.malformed:
                body = b"{not-json}\n"
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()
                return
            chunks = _stream_chunks(model, payload)
            body_length = sum(len(chunk) for chunk in chunks)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Content-Length", str(body_length))
            self.end_headers()
            for chunk in chunks:
                self.wfile.write(chunk)
                self.wfile.flush()
                time.sleep(0.003)
            return
        self._send_json({"error": "not found"}, status=404)


class FakeOllamaServer:
    def __init__(self) -> None:
        self.state = FakeState()
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._httpd.daemon_threads = True
        _Handler.state = self.state
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> FakeOllamaServer:
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2.0)
