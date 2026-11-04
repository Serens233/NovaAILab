import { describe, expect, it } from 'vitest';
import { parseConfig } from '@/config/env';
import { createAdapter } from '@/services/adapters/createAdapter';
import { MockAdapter } from '@/services/adapters/mockAdapter';
import { RealAdapter } from '@/services/adapters/realAdapter';
import { fastMock } from './utils';

describe('MockAdapter', () => {
  it('回复可重现，并标记为演示回复', async () => {
    const a = fastMock({ streaming: false });
    const r1 = await a.sendMessage({ conversationId: 'mock-conv-orion', content: '你好', clientMessageId: 'u1' });
    const r2 = await fastMock().sendMessage({ conversationId: 'mock-conv-orion', content: '你好', clientMessageId: 'u2' });
    expect(r1.assistantMessage.content).toBe(r2.assistantMessage.content);
    expect(r1.assistantMessage.deliveryStatus).toBe('mock');
  });

  it('离线场景：健康检查报告 offline，其它请求抛出网络错误', async () => {
    const a = fastMock({ offline: true });
    expect((await a.getBackendHealth()).state).toBe('offline');
    await expect(a.listConversations()).rejects.toMatchObject({ kind: 'network' });
  });

  it('failNextMessage 只失败一次', async () => {
    const a = fastMock({ failNextMessage: true });
    const input = { conversationId: 'mock-conv-orion', content: 'x', clientMessageId: 'u1' };
    await expect(a.sendMessage(input)).rejects.toMatchObject({ kind: 'http', status: 500 });
    await expect(a.sendMessage(input)).resolves.toBeTruthy();
  });

  it('流式中途取消后释放资源，且不写入半截消息', async () => {
    const a = new MockAdapter({ latencyMs: 1, streamChunkDelayMs: 5 });
    const controller = new AbortController();
    const before = (await a.getConversationMessages('mock-conv-orion')).length;
    const run = (async () => {
      let n = 0;
      for await (const ev of a.streamMessage(
        { conversationId: 'mock-conv-orion', content: '讲讲星星', clientMessageId: 'u1' },
        { signal: controller.signal },
      )) {
        if (ev.type === 'delta' && ++n === 2) controller.abort();
      }
    })();
    await expect(run).rejects.toMatchObject({ kind: 'aborted' });
    expect(a.stats.streamsStarted).toBe(1);
    expect(a.stats.streamsCleanedUp).toBe(1);
    expect((await a.getConversationMessages('mock-conv-orion')).length).toBe(before);
  });
});

describe('配置与适配器选择', () => {
  it('默认 mock；real 模式创建 RealAdapter，绝不会创建 MockAdapter', () => {
    expect(createAdapter(parseConfig({}))).toBeInstanceOf(MockAdapter);
    const real = createAdapter(parseConfig({ VITE_NOVA_API_MODE: 'real', VITE_NOVA_API_BASE_URL: 'http://x/api/' }));
    expect(real).toBeInstanceOf(RealAdapter);
    expect(real.mode).toBe('real');
  });

  it('非法配置给出警告并使用安全默认值', () => {
    const c = parseConfig({ VITE_NOVA_API_MODE: 'prod', VITE_NOVA_STREAM_PROTOCOL: 'grpc', VITE_NOVA_API_BASE_URL: '/api///' });
    expect(c.mode).toBe('mock');
    expect(c.streamProtocol).toBe('none');
    expect(c.apiBaseUrl).toBe('/api');
    expect(c.warnings).toHaveLength(2);
  });
});
