import { describe, expect, it } from 'vitest';
import { RealAdapter } from '@/services/adapters/realAdapter';
import { NovaApiError } from '@/services/api/errors';
import type { StreamEvent } from '@/types/nova';
import { chunkedStream, fakeFetch, json } from './utils';

const base = { baseUrl: '/api', timeoutMs: 2000 };
const threadId = '11111111-1111-4111-8111-111111111111';
const requestId = '22222222-2222-4222-8222-222222222222';
const time = '2026-11-03T12:00:00Z';
const input = { conversationId: threadId, content: 'hi', clientMessageId: 'u-local', requestId };
const thread = { id: threadId, title: '夜航', model: 'qwen3.5:4b', created_at: time, updated_at: time };
const reply = { id: 'a1', thread_id: threadId, role: 'assistant', content: '晚上好', status: 'complete', created_at: time };
const modelRoute = 'GET /api/v1/models';
const models = () => json({
  default_model: 'qwen3.5:4b',
  preferred_model: 'experimental',
  models: [
    { id: 'qwen3.5:9b', provider: 'ollama', available: true },
    { id: 'qwen3.5:4b', provider: 'ollama', available: true },
    { id: 'experimental', provider: 'ollama', available: true },
    { id: 'deepseek', provider: 'deepseek', available: true },
  ],
});
const meta = { type: 'meta', thread_id: threadId, assistant_message_id: reply.id, user_message_id: 'u1' };
const sse = (payload: unknown) => 'data: ' + JSON.stringify(payload) + '\n\n';

describe('RealAdapter：健康检查与离线', () => {
  it('后端无法连接时报告 offline，而不是在线', async () => {
    const fetch = (async () => { throw new TypeError('Failed to fetch'); }) as typeof globalThis.fetch;
    const a = new RealAdapter({ ...base, streamProtocol: 'none', fetchImpl: fetch });
    const h = await a.getBackendHealth();
    expect(h.state).toBe('offline');
    expect(h.modelName).toBeUndefined();
  });

  it('/health 只显示实际报告的状态，模型和记忆未知', async () => {
    const f = fakeFetch({ 'GET /api/health': () => json({ status: 'ok', database: 'ok' }) });
    const a = new RealAdapter({ ...base, streamProtocol: 'none', fetchImpl: f.fetch });
    const h = await a.getBackendHealth();
    expect(h).toMatchObject({ state: 'online', memoryService: 'unknown' });
    expect(h.modelName).toBeUndefined();
  });

  it('健康检查返回空对象不应冒充已上线', async () => {
    const f = fakeFetch({ 'GET /api/health': () => json({}) });
    const a = new RealAdapter({ ...base, streamProtocol: 'none', fetchImpl: f.fetch });
    expect((await a.getBackendHealth()).state).toBe('offline');
  });

  it('健康检查 404 时报告未识别，不以 OpenAPI 替代在线证据', async () => {
    const f = fakeFetch({ 'GET /api/openapi.json': () => json({ openapi: '3.1.0', paths: {} }) });
    const a = new RealAdapter({ ...base, streamProtocol: 'none', fetchImpl: f.fetch });
    const h = await a.getBackendHealth();
    expect(h.state).toBe('not_configured');
    expect(h.detail).toContain('无法找到 Nova 健康检查接口');
    expect(f.calls).toHaveLength(1);
  });

  it('请求超时被归类为 timeout', async () => {
    const hanging = ((_: unknown, init: RequestInit) =>
      new Promise((_, reject) => {
        init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      })) as unknown as typeof fetch;
    const a = new RealAdapter({ baseUrl: '/api', timeoutMs: 1000, streamProtocol: 'none', fetchImpl: hanging });
    await expect(a.listConversations()).rejects.toMatchObject({ kind: 'timeout' });
  });
});

describe('RealAdapter：能力推断', () => {
  it('基于真实聊天 API 的模型目录与核实路由表推断能力', async () => {
    const f = fakeFetch({ [modelRoute]: models });
    const a = new RealAdapter({ ...base, streamProtocol: 'sse', fetchImpl: f.fetch });
    expect(await a.getBackendCapabilities()).toMatchObject({
      source: 'assumed', streaming: 'sse', stopGeneration: true,
      conversationPersistence: true, renameConversation: true, deleteConversation: true,
      memory: false, channels: false, modelSwitch: false,
    });
  });

  it('目录鉴权失败不能显示聊天能力可用', async () => {
    const f = fakeFetch({ [modelRoute]: () => json({ error: { code: 'UNAUTHORIZED', message: '需要身份验证' } }, 401) });
    const a = new RealAdapter({ ...base, streamProtocol: 'sse', fetchImpl: f.fetch });
    await expect(a.getBackendCapabilities()).rejects.toMatchObject({ status: 401, message: '需要身份验证' });
  });

  it('显式映射时兼容后端声明的能力', async () => {
    const f = fakeFetch({ 'GET /api/capabilities': () => json({ streaming: 'ndjson', memory: true, stop_generation: true }) });
    const a = new RealAdapter({ ...base, streamProtocol: 'none', fetchImpl: f.fetch, routes: {
      capabilities: { method: 'GET', path: '/capabilities', status: 'verified' },
    } });
    expect(await a.getBackendCapabilities()).toMatchObject({ source: 'backend', streaming: 'ndjson', memory: true, stopGeneration: true });
  });
});

describe('RealAdapter：会话 CRUD 和已保存消息', () => {
  it('使用真实 routes，返回真实 ID，并保留历史消息状态', async () => {
    const path = '/api/v1/threads/' + threadId;
    const f = fakeFetch({
      [modelRoute]: models,
      'GET /api/v1/threads': () => json({ threads: [thread] }),
      'POST /api/v1/threads': () => json(thread, 201),
      ['PATCH ' + path]: () => json({ ...thread, title: '已重命名' }),
      ['GET ' + path]: () => json({ thread, messages: [{ ...reply, status: 'interrupted' }] }),
      ['DELETE ' + path]: () => new Response(null, { status: 204 }),
    });
    const a = new RealAdapter({ ...base, streamProtocol: 'none', fetchImpl: f.fetch });
    expect(await a.listConversations()).toMatchObject([{ conversationId: threadId, title: '夜航' }]);
    expect(await a.createConversation({ title: '夜航' })).toMatchObject({ conversationId: threadId });
    expect(f.calls.find((c) => c.method === 'POST')?.body).toEqual({ title: '夜航', model: 'qwen3.5:4b' });
    expect(await a.renameConversation(threadId, '已重命名')).toMatchObject({ title: '已重命名' });
    expect(await a.getConversationMessages(threadId)).toMatchObject([{ messageId: 'a1', deliveryStatus: 'stopped' }]);
    await expect(a.deleteConversation(threadId)).resolves.toBeUndefined();
  });

  it('会话 API 404 如实报告，不生成临时假会话', async () => {
    const f = fakeFetch({ [modelRoute]: models });
    const a = new RealAdapter({ ...base, streamProtocol: 'none', fetchImpl: f.fetch });
    await expect(a.listConversations()).rejects.toMatchObject({ kind: 'not_implemented' });
    await expect(a.createConversation()).rejects.toMatchObject({ kind: 'not_implemented' });
  });
});

describe('RealAdapter：发送消息', () => {
  it('后端 500 时显示公开错误提示', async () => {
    const f = fakeFetch({ [modelRoute]: models, 'POST /api/v1/chat': () => json({ error: { message: 'LLM 服务不可用' } }, 500) });
    const a = new RealAdapter({ ...base, streamProtocol: 'none', fetchImpl: f.fetch });
    const err = await a.sendMessage(input).catch((e) => e);
    expect(err).toBeInstanceOf(NovaApiError);
    expect(err).toMatchObject({ kind: 'http', status: 500, message: 'LLM 服务不可用' });
  });

  it('普通响应带正式消息 ID，请求只发送真实协议的字段', async () => {
    const f = fakeFetch({ [modelRoute]: models, 'POST /api/v1/chat': () => json({ thread_id: threadId, message: reply }) });
    const a = new RealAdapter({ ...base, streamProtocol: 'none', fetchImpl: f.fetch });
    expect(await a.sendMessage(input)).toMatchObject({ assistantMessage: { messageId: 'a1', content: '晚上好' } });
    expect(f.calls.at(-1)?.body).toEqual({
      thread_id: threadId, content: 'hi', request_id: requestId, stream: false, model: 'qwen3.5:4b', cloud_consent: false,
    });
  });

  it('缺少已安装的稳定本地模型时禁止云端或实验模型回退', async () => {
    const f = fakeFetch({
      [modelRoute]: () => json({ default_model: 'qwen3.5:9b', models: [
        { id: 'qwen3.5:9b', provider: 'ollama', available: false },
        { id: 'deepseek', provider: 'deepseek', available: true },
      ] }),
    });
    const a = new RealAdapter({ ...base, streamProtocol: 'none', fetchImpl: f.fetch });
    await expect(a.sendMessage(input)).rejects.toMatchObject({ kind: 'not_configured' });
    expect(f.calls).toHaveLength(1);
  });

  it('早到的真实取消 HTTP 409 识别为取消，stop 使用原始请求 UUID', async () => {
    const f = fakeFetch({
      [modelRoute]: models,
      'POST /api/v1/chat/cancel': () => json({ cancel_requested: true }),
      'POST /api/v1/chat': () => json({ error: { code: 'CHAT_CANCELLED', message: '生成已取消' } }, 409),
    });
    const a = new RealAdapter({ ...base, streamProtocol: 'none', fetchImpl: f.fetch });
    await a.stopGeneration({ conversationId: threadId, requestId });
    expect(f.calls[0].body).toEqual({ thread_id: threadId, request_id: requestId });
    await expect(a.sendMessage(input)).rejects.toMatchObject({ kind: 'aborted', status: 409 });
  });
});

describe('RealAdapter：SSE 流式', () => {
  const sseAdapter = (stream: ReadableStream<Uint8Array>) => new RealAdapter({
    ...base, streamProtocol: 'sse',
    fetchImpl: fakeFetch({
      [modelRoute]: models,
      'POST /api/v1/chat': () => new Response(stream, { headers: { 'Content-Type': 'text/event-stream; charset=utf-8' } }),
    }).fetch,
  });

  it('解析分包的 SSE，传递真实 user/assistant ID 和已保存终态', async () => {
    const first = sse({ type: 'delta', text: '你' });
    const { stream } = chunkedStream([
      sse(meta), first.slice(0, -1), first.slice(-1) + sse({ type: 'delta', text: '好' }),
      sse({ type: 'done', message: reply }),
    ]);
    const events: StreamEvent[] = [];
    for await (const ev of sseAdapter(stream).streamMessage(input, {})) events.push(ev);
    expect(events.map((e) => e.type)).toEqual(['start', 'delta', 'delta', 'done']);
    expect(events[0]).toMatchObject({ messageId: 'a1', userMessageId: 'u1', conversationId: threadId });
    expect(events[3]).toMatchObject({ message: { messageId: 'a1', content: '晚上好', deliveryStatus: 'complete' } });
  });

  it.each([
    ['BACKEND_TIMEOUT', 'stream'],
    ['CHAT_CANCELLED', 'aborted'],
  ])('实际后端错误 %s 报告为 %s', async (code, kind) => {
    const { stream } = chunkedStream([sse(meta), sse({ type: 'error', code, message: '运行已结束' })]);
    const run = async () => {
      for await (const _ of sseAdapter(stream).streamMessage(input, {})) { /* consume */ }
    };
    await expect(run()).rejects.toMatchObject({ kind, message: '运行已结束' });
  });

  it('没有完成确认的 EOF 不算成功', async () => {
    const { stream } = chunkedStream([sse(meta), sse({ type: 'delta', text: '半' })]);
    const seen: string[] = [];
    const run = async () => {
      for await (const ev of sseAdapter(stream).streamMessage(input, {})) seen.push(ev.type);
    };
    await expect(run()).rejects.toMatchObject({ kind: 'stream' });
    expect(seen).toEqual(['start', 'delta']);
  });

  it('用户取消时释放流并报告取消', async () => {
    const { stream, state } = chunkedStream([sse(meta), sse({ type: 'delta', text: '一' })], { hang: true });
    const controller = new AbortController();
    const seen: string[] = [];
    const run = (async () => {
      for await (const ev of sseAdapter(stream).streamMessage(input, { signal: controller.signal })) {
        seen.push(ev.type);
        if (ev.type === 'delta') controller.abort();
      }
    })();
    await expect(run).rejects.toMatchObject({ kind: 'aborted' });
    expect(seen).toEqual(['start', 'delta']);
    expect(state.cancelled).toBe(true);
  });

  it('流式未启用时不会发出请求', async () => {
    const f = fakeFetch({});
    const a = new RealAdapter({ ...base, streamProtocol: 'none', fetchImpl: f.fetch });
    const gen = a.streamMessage(input, {});
    await expect(gen[Symbol.asyncIterator]().next()).rejects.toMatchObject({ kind: 'not_configured' });
    expect(f.calls).toHaveLength(0);
  });
});

describe('RealAdapter：未实现的接口', () => {
  it('记忆和渠道均未映射，直接报告未实现且不发请求', async () => {
    const f = fakeFetch({});
    const a = new RealAdapter({ ...base, streamProtocol: 'none', fetchImpl: f.fetch });
    await expect(a.getMemoryRecords()).rejects.toMatchObject({ kind: 'not_implemented' });
    await expect(a.getChannelStatuses()).rejects.toMatchObject({ kind: 'not_implemented' });
    expect(f.calls).toHaveLength(0);
  });
});
