import test from "node:test";
import assert from "node:assert/strict";
import { readNovaEvents } from "../lib/stream.mjs";

function stream(text, chunkSize = 1) {
    const bytes = new TextEncoder().encode(text);
    return new ReadableStream({
        start(controller) {
            for (let offset = 0; offset < bytes.length; offset += chunkSize)
                controller.enqueue(bytes.slice(offset, offset + chunkSize));
            controller.close();
        },
    });
}
async function collect(body) {
    const events = [];
    for await (const event of readNovaEvents(body)) events.push(event);
    return events;
}
test("中文UTF-8、CRLF和事件边界可跨网络块", async () => {
    const events = await collect(
        stream(
            ': ping\r\ndata: {"type":"delta","text":"你好，Nova"}\r\n\r\ndata: {"type":"done","message":{"content":"你好，Nova"}}\r\n\r\n',
        ),
    );
    assert.equal(events.length, 2);
    assert.equal(events[0].text, "你好，Nova");
});
test("标准SSE多行data和无结尾空行", async () => {
    const events = await collect(
        stream('data: {"type":"done",\ndata: "message":{"content":"ok"}}'),
    );
    assert.equal(events[0].message.content, "ok");
});
test("流提前结束不会假装成功", async () => {
    await assert.rejects(
        collect(stream('data: {"type":"delta","text":"半条回复"}\n\n')),
        /提前结束/,
    );
});
test("非法JSON或未知协议不混作文本", async () => {
    await assert.rejects(collect(stream("data: wrong\n\n")), /无法解析/);
    await assert.rejects(collect(stream('data: {"type":"ai-sdk"}\n\n')), /未知消息/);
});
test("消费者取消会关闭上游reader", async () => {
    let cancelled = false;
    const body = new ReadableStream({
        start(c) {
            c.enqueue(new TextEncoder().encode('data: {"type":"delta","text":"a"}\n\n'));
        },
        cancel() {
            cancelled = true;
        },
    });
    for await (const _ of readNovaEvents(body)) {
        break;
    }
    assert.equal(cancelled, true);
});
