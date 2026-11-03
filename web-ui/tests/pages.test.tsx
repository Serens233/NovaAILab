import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { MarkdownContent } from '@/components/chat/MarkdownContent';
import { RealAdapter, type RealAdapterOptions } from '@/services/adapters/realAdapter';
import { fakeFetch, fastMock, json, renderApp } from './utils';
import { setViewportWidth } from './setup';

function realAdapterWith(routes: Parameters<typeof fakeFetch>[0], overrides?: RealAdapterOptions['routes']) {
  const fetch = fakeFetch({ 'GET /api/v1/models': () => json({ models: [] }), ...routes }).fetch;
  return new RealAdapter({ baseUrl: '/api', timeoutMs: 2000, streamProtocol: 'none', fetchImpl: fetch, routes: overrides });
}

describe('渠道管理', () => {
  it('Mock 模式：展示三种渠道，并标注为演示状态', async () => {
    renderApp('/channels');
    // 加载期间显示「状态未知」，拿到数据后才显示演示状态
    expect(screen.getByTestId('channel-web-status')).toHaveTextContent('状态未知');
    await waitFor(() => expect(screen.getByTestId('channel-web-status')).toHaveTextContent('已连接'));
    expect(screen.getByTestId('channel-qq-status')).toHaveTextContent('未配置');
    expect(screen.getByTestId('channel-wechat-status')).toHaveTextContent('未配置');
    expect(within(screen.getByTestId('channel-web')).getByText('演示状态')).toBeInTheDocument();
  });

  it('真实模式且后端没有渠道接口：全部显示「状态未知」，绝不显示「已连接」', async () => {
    renderApp('/channels', realAdapterWith({ 'GET /api/health': () => json({ status: 'ok' }) }));
    expect(await screen.findAllByText('后端暂未提供渠道状态接口，等待对接。')).toHaveLength(3);
    for (const t of ['web', 'qq', 'wechat']) {
      expect(screen.getByTestId(`channel-${t}`)).toHaveAttribute('data-status', 'unknown');
      expect(screen.getByTestId(`channel-${t}-status`)).toHaveTextContent('状态未知');
    }
    expect(screen.queryByText('已连接')).not.toBeInTheDocument();
  });

  it('真实模式：显示后端报告的连接失败与错误信息', async () => {
    renderApp(
      '/channels',
      realAdapterWith({
        'GET /api/health': () => json({ status: 'ok' }),
        'GET /api/channels': () =>
          json([
            { channel_type: 'web', status: 'connected' },
            { channel_type: 'qq', status: 'error', last_error: '回调签名校验失败' },
          ]),
      }, { channelStatuses: { method: 'GET', path: '/channels', status: 'verified' } }),
    );
    expect(await screen.findByText('最近错误：回调签名校验失败')).toBeInTheDocument();
    expect(screen.getByTestId('channel-qq-status')).toHaveTextContent('连接失败');
    expect(screen.getByTestId('channel-wechat-status')).toHaveTextContent('状态未知');
  });

  it('配置入口在后端未提供时提示「暂未提供」', async () => {
    const user = userEvent.setup();
    renderApp('/channels');
    const qq = await screen.findByTestId('channel-qq');
    await user.click(within(qq).getByRole('button', { name: /配置入口/ }));
    expect(await within(qq).findByText('后端暂未提供此功能')).toBeInTheDocument();
  });
});

describe('星图档案馆', () => {
  it('Mock 模式显示演示记忆，并明确标注不是真实记录', async () => {
    const user = userEvent.setup();
    renderApp('/archive');
    expect(await screen.findByText('喜欢在冬夜观星')).toBeInTheDocument();
    expect(screen.getByRole('note')).toHaveTextContent('Mock 演示数据');
    await user.click(screen.getByText('喜欢在冬夜观星'));
    expect(await screen.findByText('补充：在意参宿四的颜色')).toBeInTheDocument();
  });

  it('真实后端没有记忆接口时显示「等待记忆服务接入」', async () => {
    renderApp('/archive', realAdapterWith({ 'GET /api/health': () => json({ status: 'ok' }) }));
    expect(await screen.findByText('等待记忆服务接入')).toBeInTheDocument();
    expect(screen.queryByText('喜欢在冬夜观星')).not.toBeInTheDocument();
  });
});

describe('响应式布局', () => {
  it('删除会话前提示已有数据库备份可能仍包含对话', async () => {
    setViewportWidth(1440);
    const user = userEvent.setup();
    renderApp('/chat/mock-conv-orion');
    await screen.findByText('今晚能看到猎户座吗？');
    await user.click(screen.getByRole('button', { name: '「猎户座的观星笔记」的更多操作' }));
    await user.click(await screen.findByRole('menuitem', { name: '删除' }));
    expect(await screen.findByText(/先前创建的数据库备份可能仍包含这段对话/)).toBeInTheDocument();
  });
  it('手机宽度：左侧导航为抽屉，右侧角色状态为独立面板', async () => {
    setViewportWidth(390);
    const user = userEvent.setup();
    renderApp('/chat/mock-conv-orion');
    await screen.findByText('今晚能看到猎户座吗？');
    expect(screen.queryByTestId('sidebar')).not.toBeInTheDocument();
    expect(screen.queryByTestId('status-panel')).not.toBeInTheDocument();

    await user.click(screen.getByTestId('open-nav'));
    const drawer = await screen.findByTestId('nav-drawer');
    expect(within(drawer).getByTestId('new-conversation')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByTestId('nav-drawer')).not.toBeInTheDocument());

    await user.click(screen.getByTestId('open-status'));
    const sheet = await screen.findByTestId('status-sheet');
    expect(within(sheet).getByTestId('status-panel')).toBeInTheDocument();
  });

  it('桌面宽度：三栏布局（导航 + 对话 + 角色状态）', async () => {
    setViewportWidth(1440);
    renderApp('/chat/mock-conv-orion');
    await screen.findByText('今晚能看到猎户座吗？');
    expect(screen.getByTestId('sidebar')).toBeInTheDocument();
    expect(screen.getByTestId('status-panel')).toBeInTheDocument();
    expect(screen.queryByTestId('open-nav')).not.toBeInTheDocument();
  });

  it('平板宽度：导航常驻，角色状态改为按钮打开', async () => {
    setViewportWidth(900);
    renderApp('/chat/mock-conv-orion');
    await screen.findByText('今晚能看到猎户座吗？');
    expect(screen.getByTestId('sidebar')).toBeInTheDocument();
    expect(screen.queryByTestId('status-panel')).not.toBeInTheDocument();
    expect(screen.getByTestId('open-status')).toBeInTheDocument();
  });
});

describe('设置页', () => {
  it('Mock 场景开关可让界面切换到离线状态', async () => {
    const user = userEvent.setup();
    const adapter = fastMock();
    renderApp('/settings', adapter);
    await waitFor(() => expect(screen.getAllByTestId('connection-badge')[0]).toHaveAttribute('data-state', 'online'));
    await user.click(screen.getByRole('switch', { name: '模拟后端离线' }));
    await waitFor(() => expect(screen.getAllByTestId('connection-badge')[0]).toHaveAttribute('data-state', 'offline'));
  });

  it('主题切换会给 <html> 加上 dark class', async () => {
    const user = userEvent.setup();
    renderApp('/settings');
    await user.click(screen.getByRole('radio', { name: /夜航深色/ }));
    expect(document.documentElement).toHaveClass('dark');
    await user.click(screen.getByRole('radio', { name: /明亮星图/ }));
    expect(document.documentElement).not.toHaveClass('dark');
  });
});

describe('Markdown 安全渲染', () => {
  it('不执行原始 HTML 与 javascript: 链接', () => {
    const { container } = render(
      <MarkdownContent content={'<img src=x onerror="alert(1)"><script>alert(2)</script>\n\n[点我](javascript:alert(3))\n\n**粗体**'} />,
    );
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('[onerror]')).toBeNull();
    const link = screen.getByText('点我').closest('a');
    expect(link?.getAttribute('href') ?? '').not.toMatch(/javascript:/i);
    expect(screen.getByText('粗体').tagName).toBe('STRONG');
  });

  it('代码块带复制按钮，外链新窗口打开', () => {
    render(<MarkdownContent content={'```ts\nconst a = 1;\n```\n\n[Nova](https://example.com)'} />);
    expect(screen.getByRole('button', { name: '复制代码' })).toBeInTheDocument();
    expect(screen.getByText('ts')).toBeInTheDocument();
    const a = screen.getByText('Nova').closest('a')!;
    expect(a).toHaveAttribute('target', '_blank');
    expect(a.getAttribute('rel')).toContain('noopener');
  });
});
