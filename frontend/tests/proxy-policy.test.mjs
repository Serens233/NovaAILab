import test from "node:test";
import assert from "node:assert/strict";
import { backendPath, safeBackendBase, sameOriginRequest } from "../lib/proxy-policy.mjs";

function request(method = "GET", host = "localhost:3000", origin) {
    return new Request(`http://${host}/api/nova/models`, {
        method,
        headers: { host, ...(origin ? { origin } : {}) },
    });
}
test("固定allowlist支持会话、模型和GPU；拒绝任意后端路径", () => {
    assert.equal(backendPath(["models"], "GET"), "/api/v1/models");
    assert.equal(backendPath(["threads", "safe-id"], "DELETE"), "/api/v1/threads/safe-id");
    assert.equal(backendPath(["gpu", "status"], "GET"), "/api/gpu/status");
    assert.equal(backendPath(["chat", "cancel"], "POST"), "/api/v1/chat/cancel");
    assert.equal(backendPath(["chat", "cancel"], "GET"), null);
    assert.equal(backendPath(["chat", "cancel", "extra"], "POST"), null);
    for (const p of [
        ["..", "chat"],
        ["threads", "%2Fsecret"],
        ["proxy"],
        ["http://evil"],
        ["gpu", "wake"],
    ])
        assert.equal(backendPath(p, "POST"), null);
    assert.equal(backendPath(["models"], "POST"), null);
});
test("上游只接受无凭据和路径的本机固定地址", () => {
    assert.equal(safeBackendBase("http://127.0.0.1:8000"), "http://127.0.0.1:8000");
    for (const url of [
        "https://evil.example",
        "https://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1@evil.example",
        "http://127.0.0.1:8000/foo",
        "http://127.0.0.1:8000?target=evil",
        "file:///C:/secret",
    ])
        assert.equal(safeBackendBase(url), null);
});
test("GET同源与POST同源可用；修改请求缺Origin拒绝", () => {
    assert.equal(sameOriginRequest(request()), true);
    assert.equal(
        sameOriginRequest(request("POST", "127.0.0.1:3000", "http://127.0.0.1:3000")),
        true,
    );
    assert.equal(sameOriginRequest(request("POST")), false);
});
test("拒绝跨站Origin、伪造Host、null和端口不匹配", () => {
    for (const origin of [
        "https://evil.example",
        "null",
        "http://localhost:3001",
        "http://localhost:3000/",
        "https://localhost:3000.evil.example",
    ])
        assert.equal(sameOriginRequest(request("POST", "localhost:3000", origin)), false);
    assert.equal(sameOriginRequest(request("GET", "evil.example")), false);
    const cross = request();
    cross.headers.set("sec-fetch-site", "cross-site");
    assert.equal(sameOriginRequest(cross), false);
});
test("局域网Host必须明确配置，不能隐式开放", () => {
    assert.equal(sameOriginRequest(request("GET", "192.168.1.5:3000")), false);
    assert.equal(
        sameOriginRequest(request("GET", "192.168.1.5:3000"), "localhost,192.168.1.5"),
        true,
    );
});
