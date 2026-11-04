import { render } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { AppProviders } from '@/AppProviders';
import { routes } from '@/routes';
import { MockAdapter, type MockScenario } from '@/services/adapters/mockAdapter';
import type { NovaAdapter } from '@/services/adapters/types';

export function fastMock(scenario: Partial<MockScenario> = {}) {
  return new MockAdapter({ latencyMs: 5, streamChunkDelayMs: 1, scenario });
}

export function renderApp(path = '/', adapter: NovaAdapter = fastMock()) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, gcTime: Infinity }, mutations: { retry: false } },
  });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const utils = render(
    <AppProviders adapter={adapter} queryClient={queryClient}>
      <RouterProvider router={router} />
    </AppProviders>,
  );
  return { ...utils, router, adapter, queryClient };
}

/** 构造一个按分包输出的 ReadableStream，并记录是否被取消 */
export function chunkedStream(chunks: string[], options: { delayMs?: number; hang?: boolean } = {}) {
  const encoder = new TextEncoder();
  const state = { cancelled: false };
  let i = 0;
  const stream = new ReadableStream<Uint8Array>({
    async pull(controller) {
      if (options.delayMs) await new Promise((r) => setTimeout(r, options.delayMs));
      if (i < chunks.length) {
        controller.enqueue(encoder.encode(chunks[i++]));
      } else if (options.hang) {
        await new Promise(() => {}); // 永不结束，模拟挂起的连接
      } else {
        controller.close();
      }
    },
    cancel() {
      state.cancelled = true;
    },
  });
  return { stream, state };
}

type Handler = (url: string, init: RequestInit) => Response | Promise<Response>;

/** 简单的 fetch 路由桩：按「方法 路径」匹配 */
export function fakeFetch(routes: Record<string, Handler>) {
  const calls: { method: string; url: string; body?: unknown }[] = [];
  const fn = async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? 'GET').toUpperCase();
    const path = url.replace(/^https?:\/\/[^/]+/, '').split('?')[0];
    calls.push({ method, url, body: init.body ? JSON.parse(String(init.body)) : undefined });
    if (init.signal?.aborted) throw new DOMException('aborted', 'AbortError');
    const handler = routes[`${method} ${path}`];
    if (!handler) return new Response(JSON.stringify({ detail: 'Not Found' }), { status: 404 });
    return handler(url, init);
  };
  return { fetch: fn as typeof fetch, calls };
}

export const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
