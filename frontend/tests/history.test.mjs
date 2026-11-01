import test from "node:test";
import assert from "node:assert/strict";
import { historyMessage } from "../lib/history.ts";

const entry = {
    id: "stable-message-id",
    role: "assistant",
    content: "已经保存的部分文本",
    created_at: "2026-11-01T06:00:00Z",
};
test("只有后端complete状态映射完整回复", () => {
    const message = historyMessage({ ...entry, status: "complete" });
    assert.equal(message.status.type, "complete");
    assert.equal(message.id, entry.id);
});
test("error/interrupted/pending都保留文本并明确未完成", () => {
    for (const status of ["error", "interrupted", "pending"]) {
        const message = historyMessage({ ...entry, status });
        assert.equal(message.status.type, "incomplete");
        assert.equal(message.content[0].text, entry.content);
        assert.equal(message.metadata.custom.novaStoredStatus, status);
    }
});
test("用户消息不套用assistant运行状态", () => {
    assert.equal(historyMessage({ ...entry, role: "user", status: "complete" }).status, undefined);
});
