import test from "node:test";
import assert from "node:assert/strict";
import { createNovaModelAdapter } from "../lib/model-adapter.mjs";

const options = () => ({
    messages: [{ role: "user", content: [{ type: "text", text: "合成测试" }] }],
    abortSignal: new AbortController().signal,
});
const response = () =>
    new Response(
        'data: {"type":"delta","text":"你"}\n\ndata: {"type":"delta","text":"好"}\n\ndata: {"type":"done","message":{"content":"你好"}}\n\n',
    );
const settings = () => ({
    current: { model: "qwen3.5:9b", confirmCloud: () => false, onError() {} },
});
async function run(adapter, args = options()) {
    const result = [];
    for await (const item of adapter.run(args)) result.push(item);
    return result;
}

function deferred() {
    let resolve;
    const promise = new Promise((done) => {
        resolve = done;
    });
    return { promise, resolve };
}

test("已创建线程adapter后从9B切4B，下一次请求必须使用4B", async () => {
    const ref = settings();
    const sent = [];
    const adapter = createNovaModelAdapter(ref, {
        async initialize() {
            return { remoteId: "thread-4b" };
        },
        async send(body) {
            sent.push(body);
            return response();
        },
        reload() {},
    });
    ref.current = { ...ref.current, model: "qwen3.5:4b" };
    await run(adapter);
    assert.equal(sent[0].model, "qwen3.5:4b");
    assert.equal(sent[0].thread_id, "thread-4b");
    assert.equal(sent[0].cloud_consent, false);
});

test("初始化等待期间改变选择，不得改变已经发送这一条的模型", async () => {
    const ref = settings();
    ref.current.model = "qwen3.5:4b";
    const sent = [];
    const adapter = createNovaModelAdapter(ref, {
        async initialize() {
            ref.current = { ...ref.current, model: "qwen3.5:9b" };
            return { remoteId: "existing" };
        },
        async send(body) {
            sent.push(body);
            return response();
        },
        reload() {},
    });
    await run(adapter);
    assert.equal(sent[0].model, "qwen3.5:4b");
    await run(adapter);
    assert.equal(sent[1].model, "qwen3.5:9b");
    assert.notEqual(sent[0].request_id, sent[1].request_id);
});

test("流式adapter每次返回累积文本，且传递原始取消signal", async () => {
    const ref = settings();
    const args = options();
    let signal;
    const adapter = createNovaModelAdapter(ref, {
        async initialize() {
            return { remoteId: "t" };
        },
        async send(_body, incoming) {
            signal = incoming;
            return response();
        },
        reload() {},
    });
    const updates = [];
    for await (const value of adapter.run(args)) updates.push(value.content[0].text);
    assert.deepEqual(updates, ["你", "你好", "你好"]);
    assert.equal(signal, args.abortSignal);
});

test("本地请求失败不会自动改用收费云端", async () => {
    const ref = settings();
    let count = 0;
    let error;
    ref.current.onError = (message) => {
        error = message;
    };
    const adapter = createNovaModelAdapter(ref, {
        async initialize() {
            return { remoteId: "t" };
        },
        async send(body) {
            count++;
            assert.equal(body.model, "qwen3.5:9b");
            throw new Error("游戏模式阻止请求");
        },
        reload() {},
    });
    await assert.rejects(run(adapter), /游戏模式/);
    assert.equal(count, 1);
    assert.equal(error, "游戏模式阻止请求");
    assert.equal(ref.current.confirmCloud(), false);
});

test("云端每次发送都要单独确认，拒绝时不创建会话或发送", async () => {
    const ref = settings();
    ref.current.model = "deepseek";
    let confirmations = 0;
    let initialized = 0;
    const sent = [];
    ref.current.confirmCloud = () => ++confirmations <= 2;
    const adapter = createNovaModelAdapter(ref, {
        async initialize() {
            initialized++;
            return { remoteId: `thread-${initialized}` };
        },
        async send(body) {
            sent.push(body);
            return response();
        },
        reload() {},
    });
    await run(adapter);
    await run(adapter);
    await assert.rejects(run(adapter), /未获同意/);
    assert.equal(confirmations, 3);
    assert.equal(initialized, 2);
    assert.deepEqual(
        sent.map((body) => body.cloud_consent),
        [true, true],
    );
    assert.deepEqual(
        sent.map((body) => body.thread_id),
        ["thread-1", "thread-2"],
    );
});

test("开始前已停止，不创建会话、不发送聊天或取消请求", async () => {
    const controller = new AbortController();
    controller.abort();
    const calls = [];
    const adapter = createNovaModelAdapter(settings(), {
        async initialize() {
            calls.push("initialize");
            return { remoteId: "t" };
        },
        async send() {
            calls.push("send");
            return response();
        },
        async cancel() {
            calls.push("cancel");
        },
        reload() {},
    });
    await assert.rejects(run(adapter, { ...options(), abortSignal: controller.signal }), {
        name: "AbortError",
    });
    assert.deepEqual(calls, []);
});

test("等待初始化时停止，完成初始化后不再创建后台聊天任务", async () => {
    const controller = new AbortController();
    const initialized = deferred();
    const started = deferred();
    const calls = [];
    const adapter = createNovaModelAdapter(settings(), {
        async initialize() {
            started.resolve();
            return initialized.promise;
        },
        async send() {
            calls.push("send");
            return response();
        },
        async cancel() {
            calls.push("cancel");
        },
        reload() {},
    });
    const pending = run(adapter, { ...options(), abortSignal: controller.signal });
    await started.promise;
    controller.abort();
    initialized.resolve({ remoteId: "t" });
    await assert.rejects(pending, { name: "AbortError" });
    assert.deepEqual(calls, []);
});

test("等待响应头时停止，立即使用相同会话和请求标识发送独立取消请求", async () => {
    const controller = new AbortController();
    const sent = deferred();
    const headers = deferred();
    const cancelled = deferred();
    let submitted;
    const calls = [];
    const adapter = createNovaModelAdapter(settings(), {
        async initialize() {
            return { remoteId: "preparing-thread" };
        },
        async send(body, signal) {
            submitted = body;
            assert.equal(signal, controller.signal);
            sent.resolve();
            return headers.promise;
        },
        async cancel(body, ...args) {
            calls.push(body);
            assert.deepEqual(args, []);
            cancelled.resolve();
        },
        reload() {},
    });
    const pending = run(adapter, { ...options(), abortSignal: controller.signal });
    await sent.promise;
    controller.abort();
    controller.abort();
    await cancelled.promise;
    assert.deepEqual(calls, [{ thread_id: submitted.thread_id, request_id: submitted.request_id }]);
    // send 尚未返回响应头，取消控制请求已经完成；原请求即使随后返回也不会显示内容。
    headers.resolve(response());
    await assert.rejects(pending, { name: "AbortError" });
    assert.equal(calls.length, 1);
});

test("send 内同步触发停止也不会丢失取消，并且不显示后到达的回复", async () => {
    const controller = new AbortController();
    const cancelled = [];
    let sent;
    const adapter = createNovaModelAdapter(settings(), {
        async initialize() {
            return { remoteId: "race-thread" };
        },
        async send(body) {
            sent = body;
            controller.abort();
            return response();
        },
        async cancel(body) {
            cancelled.push(body);
        },
        reload() {},
    });
    await assert.rejects(run(adapter, { ...options(), abortSignal: controller.signal }), {
        name: "AbortError",
    });
    assert.deepEqual(cancelled, [{ thread_id: sent.thread_id, request_id: sent.request_id }]);
});

test("已输出部分文本时停止，取消后台任务且不再输出剩余增量", async () => {
    const controller = new AbortController();
    const cancelled = [];
    let sent;
    const adapter = createNovaModelAdapter(settings(), {
        async initialize() {
            return { remoteId: "stream-thread" };
        },
        async send(body) {
            sent = body;
            return response();
        },
        async cancel(body) {
            cancelled.push(body);
        },
        reload() {},
    });
    const iterator = adapter.run({ ...options(), abortSignal: controller.signal });
    assert.equal((await iterator.next()).value.content[0].text, "你");
    controller.abort();
    await assert.rejects(iterator.next(), { name: "AbortError" });
    assert.deepEqual(cancelled, [{ thread_id: sent.thread_id, request_id: sent.request_id }]);
});

test("done 已返回时停止不会取消已完成任务，后续同样不触发取消", async () => {
    const controller = new AbortController();
    const cancelled = [];
    const adapter = createNovaModelAdapter(settings(), {
        async initialize() {
            return { remoteId: "finished-thread" };
        },
        async send() {
            return new Response('data: {"type":"done","message":{"content":"完成"}}\n\n');
        },
        async cancel(body) {
            cancelled.push(body);
        },
        reload() {},
    });
    const iterator = adapter.run({ ...options(), abortSignal: controller.signal });
    assert.equal((await iterator.next()).value.content[0].text, "完成");
    controller.abort();
    assert.equal((await iterator.next()).done, true);
    await Promise.resolve();
    assert.deepEqual(cancelled, []);
});

test("取消控制请求失败会提示未确认，不产生未处理的 Promise 拒绝", async () => {
    for (const synchronous of [false, true]) {
        const controller = new AbortController();
        const errors = [];
        const ref = settings();
        ref.current.onError = (message) => errors.push(message);
        const adapter = createNovaModelAdapter(ref, {
            async initialize() {
                return { remoteId: "failure-thread" };
            },
            async send() {
                controller.abort();
                throw new DOMException("aborted", "AbortError");
            },
            cancel() {
                if (synchronous) throw new Error("合成网络故障");
                return Promise.reject(new Error("合成网络故障"));
            },
            reload() {},
        });
        await assert.rejects(run(adapter, { ...options(), abortSignal: controller.signal }), {
            name: "AbortError",
        });
        await new Promise((resolve) => setImmediate(resolve));
        assert.equal(errors.length, 2);
        assert.equal(errors[0], null);
        assert.match(errors[1], /停止请求未获服务器确认/);
        assert.doesNotMatch(errors[1], /合成网络故障/);
    }
});

test("普通网络失败后清理停止监听，不额外取消或自动回退", async () => {
    const controller = new AbortController();
    const cancelled = [];
    const adapter = createNovaModelAdapter(settings(), {
        async initialize() {
            return { remoteId: "failed-thread" };
        },
        async send() {
            throw new Error("合成连接失败");
        },
        async cancel(body) {
            cancelled.push(body);
        },
        reload() {},
    });
    await assert.rejects(
        run(adapter, { ...options(), abortSignal: controller.signal }),
        /连接失败/,
    );
    controller.abort();
    await Promise.resolve();
    assert.deepEqual(cancelled, []);
});
