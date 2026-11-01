"""Minimal Ollama HTTP client used by the benchmark.

It intentionally bypasses Nova's production router and GPU manager.  The
benchmark therefore only runs after an explicit GPU lease is supplied; see
``safety.py``.  The client is read-only except for Ollama's documented
load/unload endpoints needed to create a reproducible cold start.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from urllib.parse import urlsplit

from .config import MODEL_NAME_PATTERN


class OllamaError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    return host.casefold() in {"127.0.0.1", "localhost", "::1"}


def _validate_model(model: str) -> str:
    if not isinstance(model, str) or not MODEL_NAME_PATTERN.fullmatch(model):
        raise ValueError(f"invalid Ollama model identifier: {model!r}")
    return model


class OllamaClient:
    def __init__(self, base_url: str = "http://127.0.0.1:11434", timeout_s: float = 30.0) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Ollama URL must use http or https")
        if not _is_loopback(parsed.hostname):
            raise ValueError("Ollama URL must point at the loopback interface")
        if parsed.query or parsed.fragment:
            raise ValueError("Ollama URL must not contain a query or fragment")
        self.base_url = base_url.rstrip("/")
        self.timeout_s = float(timeout_s)

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            raise ValueError("path must start with /")
        return f"{self.base_url}{path}"

    def _open(
        self,
        path: str,
        payload: dict[str, object] | None = None,
        *,
        timeout_s: float | None = None,
        accept: str = "application/json",
    ) -> object:
        data = None
        headers = {"Accept": accept}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self._url(path),
            data=data,
            headers=headers,
            method="POST" if data is not None else "GET",
        )
        try:
            return urllib.request.urlopen(request, timeout=timeout_s or self.timeout_s)
        except urllib.error.HTTPError as exc:
            body = exc.read(1000).decode("utf-8", "replace").strip()
            raise OllamaError(
                f"Ollama HTTP {exc.code} for {path}",
                status=exc.code,
                body=body,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise OllamaError(f"Ollama request failed for {path}: {reason}") from exc

    def _json(self, response: object) -> dict[str, object]:
        try:
            raw = response.read()  # type: ignore[attr-defined]
        finally:
            response.close()  # type: ignore[attr-defined]
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OllamaError("Ollama returned invalid JSON") from exc
        if not isinstance(document, dict):
            raise OllamaError("Ollama returned a non-object JSON document")
        return document

    def get_json(self, path: str) -> dict[str, object]:
        response = self._open(path)
        return self._json(response)

    def post_json(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        response = self._open(path, payload)
        return self._json(response)

    def version(self) -> dict[str, object]:
        return self.get_json("/api/version")

    def tags(self) -> list[dict[str, object]]:
        document = self.get_json("/api/tags")
        models = document.get("models")
        if not isinstance(models, list):
            raise OllamaError("Ollama /api/tags did not return a models list")
        return [item for item in models if isinstance(item, dict)]

    def show(self, model: str) -> dict[str, object]:
        return self.post_json("/api/show", {"model": _validate_model(model)})

    def ps(self) -> list[dict[str, object]]:
        document = self.get_json("/api/ps")
        models = document.get("models")
        if not isinstance(models, list):
            raise OllamaError("Ollama /api/ps did not return a models list")
        return [item for item in models if isinstance(item, dict)]

    def loaded_models(self) -> list[str]:
        return [
            str(item.get("name") or item.get("model"))
            for item in self.ps()
            if item.get("name") or item.get("model")
        ]

    def unload(self, model: str, *, wait_s: float = 30.0) -> dict[str, object]:
        """Unload one model and wait until ``/api/ps`` confirms it is gone."""
        model = _validate_model(model)
        loaded = self.loaded_models()
        started = time.perf_counter()
        if model not in loaded:
            return {
                "model": model,
                "already_unloaded": True,
                "unload_request_ms": 0.0,
                "unload_wait_ms": 0.0,
            }
        try:
            self.post_json("/api/generate", {"model": model, "keep_alive": 0})
        except OllamaError as exc:
            if exc.status != 404:
                raise
        request_ms = (time.perf_counter() - started) * 1000.0
        deadline = time.monotonic() + max(1.0, wait_s)
        while time.monotonic() < deadline:
            if model not in self.loaded_models():
                wait_ms = (time.perf_counter() - started) * 1000.0
                return {
                    "model": model,
                    "already_unloaded": False,
                    "unload_request_ms": round(request_ms, 3),
                    "unload_wait_ms": round(wait_ms, 3),
                }
            time.sleep(0.1)
        raise OllamaError(f"model {model} did not unload within {wait_s:.1f}s")

    def chat_stream(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        options: dict[str, int | float] | None = None,
        keep_alive: str | int = "5m",
        think: bool | None = None,
        timeout_s: float | None = None,
    ) -> Iterator[dict[str, object]]:
        model = _validate_model(model)
        if not isinstance(messages, list) or not messages:
            raise ValueError("messages must be a non-empty list")
        payload: dict[str, object] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "keep_alive": keep_alive,
        }
        if options:
            payload["options"] = options
        if think is not None:
            payload["think"] = bool(think)
        response = self._open(
            "/api/chat",
            payload,
            timeout_s=timeout_s,
            accept="application/x-ndjson",
        )
        completed = False
        try:
            while True:
                line = response.readline()  # type: ignore[attr-defined]
                if not line:
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                if len(stripped) > 1_048_576:
                    raise OllamaError("Ollama stream chunk is unexpectedly large")
                try:
                    document = json.loads(stripped.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise OllamaError("Ollama stream contained invalid JSON") from exc
                if not isinstance(document, dict):
                    raise OllamaError("Ollama stream chunk is not an object")
                if "error" in document:
                    raise OllamaError(f"Ollama stream error: {document['error']}")
                yield document
                if document.get("done") is True:
                    completed = True
                    break
        finally:
            response.close()  # type: ignore[attr-defined]
        if not completed:
            raise OllamaError("Ollama stream ended before the done chunk")
