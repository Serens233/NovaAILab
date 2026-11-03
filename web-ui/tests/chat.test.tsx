import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { MockAdapter } from '@/services/adapters/mockAdapter';
import { RealAdapter } from '@/services/adapters/realAdapter';
import { queryKeys } from '@/hooks/queries';
import { fakeFetch, fastMock, json, renderApp } from './utils';

const input = () => screen.getByTestId('composer-input') as HTMLTextAreaElement;
const realThread = { id: '11111111-1111-4111-8111-111111111111', title: '本地验收', created_at: '2026-11-03T12:00:00Z' };

function renderRealChat(routes: Parameters<typeof fakeFetch>[0]) {
  const f = fakeFetch({
    'GET /api/health': () => json({ status: 'ok', database: 'ok' }),
    'GET /api/v1/models': () => json({ models: [{ id: 'qwen3.5:4b', provider: 'ollama', available: true }] }),
    'GET /api/v1/threads': () => json({ threads: [realThread] }),
    ['GET /api/v1/threads/' + realThread.id]: () => json({ thread: realThread, messages: [] }),
    ...routes,
  });
  const adapter = new RealAdapter({ baseUrl: '/api', timeoutMs: 2000, streamProtocol: 'sse', fetchImpl: f.fetch });
  return { ...renderApp('/chat/' + realThread.id, adapter), calls: f.calls };
}

describe('Mock 模式：发送与接收', () => {
  it('流式模式下发送消息并收到 Nova 的回复', async () => {
    const user = userEvent.setup();
    renderApp('/chat/mock-conv-orion');
    await screen.findByText('今晚能看到猎户座吗？');
    await waitFor(() => expect(screen.getAllByTestId('connection-badge')[0]).toHaveAttribute('data-state', 'online'));

    await user.type(input(), '你好{Enter}');
    const log = screen.getByRole('log');
    expect(await within(log).findByText('你好')).toBeInTheDocument();
    expect(await within(log).findByText(/星图档案馆的夜航记录员/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId('pending-assistant')).not.toBeInTheDocument());
    expect(within(log).getAllByText('演示回复').length).toBeGreaterThan(0);
    expect(input().value).toBe('');
  });

  it('普通响应模式（非流式）显示真实的等待状态，然后显示回复', async () => {
    const user = userEvent.setup();
    const adapter = new MockAdapter({ latencyMs: 30, scenario: { streaming: false } });
    renderApp('/chat/mock-conv-orion', adapter);
    await screen.findByText('今晚能看到猎户座吗？');
    await waitFor(() => expect(screen.getAllByTestId('connection-badge')[0]).toHaveAttribute('data-state', 'online'));
    await user.type(input(), '记忆是怎么保存的？{Enter}');
    expect(await screen.findByText(/普通响应，非流式/)).toBeInTheDocument();
    expect(await screen.findByText(/每条记忆都有形成时间/)).toBeInTheDocument();
  });

  it('Shift+Enter 换行而不发送', async () => {
    const user = userEvent.setup();
    renderApp('/chat/mock-conv-orion');
    await screen.findByText('今晚能看到猎户座吗？');
    await user.type(input(), '第一行{Shift>}{Enter}{/Shift}第二行');
    expect(input().value).toBe('第一行\n第二行');
    expect(screen.queryByTestId('pending-assistant')).not.toBeInTheDocument();
  });
});

describe('中文输入法', () => {
  it('组字期间按 Enter 不会发送；组字结束后再按 Enter 才发送', async () => {
    const adapter = fastMock();
    renderApp('/chat/mock-conv-orion', adapter);
    await screen.findByText('今晚能看到猎户座吗？');
    const ta = input();

    fireEvent.compositionStart(ta);
    fireEvent.change(ta, { target: { value: 'xing' } });
    fireEvent.keyDown(ta, { key: 'Enter', code: 'Enter', isComposing: true, keyCode: 229 });
    fireEvent.change(ta, { target: { value: '星星' } });
    fireEvent.compositionEnd(ta);
    // 部分浏览器在 compositionend 之后紧接着派发 Enter，也不应发送
    fireEvent.keyDown(ta, { key: 'Enter', code: 'Enter' });
    expect(ta.value).toBe('星星');
    expect(screen.queryByTestId('pending-assistant')).not.toBeInTheDocument();

    await act(() => new Promise((r) => setTimeout(r, 80)));
    fireEvent.keyDown(ta, { key: 'Enter', code: 'Enter' });
    expect(await within(screen.getByRole('log')).findByText('星星')).toBeInTheDocument();
    expect(ta.value).toBe('');
  });
});

describe('会话：创建与切换', () => {
  it('新建会话进入欢迎界面，切换到已有会话显示其消息', async () => {
    const user = userEvent.setup();
    const { router } = renderApp('/');
    expect(await screen.findByTestId('welcome')).toBeInTheDocument();

    await user.click(screen.getByTestId('new-conversation'));
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/chat\/mock-conv-/));
    const newConversation = await screen.findByRole('log');
    expect(await within(newConversation).findByTestId('welcome')).toBeInTheDocument();
    expect(screen.getAllByText('新的夜航记录').length).toBeGreaterThan(0);

    await user.click(screen.getByRole('link', { name: /初次见面的夜航记录/ }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/chat/mock-conv-first'));
    expect(await screen.findByText(/一起阅读、记录/)).toBeInTheDocument();
  });

  it('在首页直接发送会自动创建会话并发送', async () => {
    const user = userEvent.setup();
    const { router } = renderApp('/');
    await screen.findByTestId('welcome');
    await waitFor(() => expect(screen.getAllByTestId('connection-badge')[0]).toHaveAttribute('data-state', 'online'));
    await user.type(input(), '你好{Enter}');
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/chat\//));
    expect(await screen.findByText(/星图档案馆的夜航记录员。/)).toBeInTheDocument();
  });
});

describe('错误与离线', () => {
  it('真实模式后端 500 时展示实际错误，不降级为演示回复', async () => {
    const user = userEvent.setup();
    const { queryClient } = renderRealChat({
      'POST /api/v1/chat': () => json({ error: { code: 'INTERNAL_ERROR', message: '本地模型不可用' } }, 500),
    });
    await waitFor(() => expect(queryClient.getQueryData([...queryKeys.capabilities, true])).toBeDefined());
    await user.type(input(), '本地验收{Enter}');
    expect(await screen.findByTestId('turn-error')).toHaveTextContent('本地模型不可用');
    expect(screen.queryByText('演示回复')).not.toBeInTheDocument();
  });

  it('API 失败时显示错误并可重新发送', async () => {
    const user = userEvent.setup();
    const adapter = fastMock({ failNextMessage: true });
    renderApp('/chat/mock-conv-orion', adapter);
    await screen.findByText('今晚能看到猎户座吗？');
    await waitFor(() => expect(screen.getAllByTestId('connection-badge')[0]).toHaveAttribute('data-state', 'online'));
    await user.type(input(), '在吗{Enter}');

    const err = await screen.findByTestId('turn-error');
    expect(err).toHaveTextContent('发送失败：后端返回错误');
    expect(err).toHaveTextContent('HTTP 500');
    expect(err).toHaveAttribute('data-error-kind', 'http');

    await user.click(within(err).getByRole('button', { name: /重新发送/ }));
    expect(await screen.findByText(/我把「在吗」记在今晚的夜航记录里了/)).toBeInTheDocument();
    expect(screen.queryByTestId('turn-error')).not.toBeInTheDocument();
  });

  it('后端离线时不显示在线状态，并禁止发送', async () => {
    renderApp('/chat/mock-conv-orion', fastMock({ offline: true }));
    await waitFor(() => expect(screen.getAllByTestId('connection-badge')[0]).toHaveAttribute('data-state', 'offline'));
    expect(screen.queryByText('后端在线')).not.toBeInTheDocument();
    expect(screen.getByTestId('connection-banner')).toHaveTextContent('无法连接到 Nova 后端');
    expect(input()).toBeDisabled();
    expect(await screen.findByText('无法连接到 Nova 后端', { selector: 'p' }, { timeout: 4000 })).toBeInTheDocument();
  });
});

describe('停止生成', () => {
  it('真实模式在收到首个 SSE 事件之前用原始请求 UUID 停止后端', async () => {
    const user = userEvent.setup();
    let completeChat: ((response: Response) => void) | undefined;
    const { queryClient, calls } = renderRealChat({
      'POST /api/v1/chat': () => new Promise<Response>((resolve) => { completeChat = resolve; }),
      'POST /api/v1/chat/cancel': () => {
        completeChat?.(json({ error: { code: 'CHAT_CANCELLED', message: '生成已取消' } }, 409));
        return json({ cancel_requested: true });
      },
    });
    await waitFor(() => expect(queryClient.getQueryData([...queryKeys.capabilities, true])).toBeDefined());
    await user.type(input(), '停止验收{Enter}');
    await waitFor(() => expect(completeChat).toBeDefined());
    expect(screen.getByTestId('turn-status')).toHaveTextContent('等待后端建立连接');
    await user.click(screen.getByTestId('stop-button'));
    const chat = calls.find((c) => c.url === '/api/v1/chat');
    const cancel = calls.find((c) => c.url === '/api/v1/chat/cancel');
    const expected = { thread_id: realThread.id, request_id: expect.stringMatching(/^[0-9a-f-]{36}$/i) };
    expect(chat?.body).toMatchObject({ ...expected, stream: true });
    expect(cancel?.body).toEqual(expected);
    expect((chat?.body as { request_id: string }).request_id).toBe((cancel?.body as { request_id: string }).request_id);
    expect(await screen.findByTestId('turn-stopped')).toHaveTextContent('已停止');
    expect(screen.queryByTestId('pending-assistant')).not.toBeInTheDocument();
  });

  it('停止后释放流资源，保留已接收内容并标记「已停止」', async () => {
    const user = userEvent.setup();
    const adapter = new MockAdapter({ latencyMs: 5, streamChunkDelayMs: 40 });
    renderApp('/chat/mock-conv-orion', adapter);
    await screen.findByText('今晚能看到猎户座吗？');
    await waitFor(() => expect(screen.getAllByTestId('connection-badge')[0]).toHaveAttribute('data-state', 'online'));

    await user.type(input(), '讲讲星星{Enter}');
    await waitFor(() => expect(screen.getByTestId('turn-status')).toHaveTextContent('正在接收回复'));
    await user.click(screen.getByTestId('stop-button'));

    expect(await screen.findByText('已停止 · 内容不完整')).toBeInTheDocument();
    await waitFor(() => expect(adapter.stats.streamsCleanedUp).toBe(adapter.stats.streamsStarted));
    expect(adapter.stats.stopRequests).toBe(1);
    expect(screen.getByTestId('send-button')).toBeInTheDocument();
  });

  it('切换会话时取消进行中的流', async () => {
    const user = userEvent.setup();
    const adapter = new MockAdapter({ latencyMs: 5, streamChunkDelayMs: 40 });
    const { router } = renderApp('/chat/mock-conv-orion', adapter);
    await screen.findByText('今晚能看到猎户座吗？');
    await waitFor(() => expect(screen.getAllByTestId('connection-badge')[0]).toHaveAttribute('data-state', 'online'));
    await user.type(input(), '讲讲星星{Enter}');
    await waitFor(() => expect(adapter.stats.streamsStarted).toBe(1));
    await act(() => router.navigate('/chat/mock-conv-first'));
    await waitFor(() => expect(adapter.stats.streamsCleanedUp).toBe(1));
  });
});
