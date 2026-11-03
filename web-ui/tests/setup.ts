import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, beforeEach } from 'vitest';
import { useSettingsStore } from '@/stores/settingsStore';
import { useUiStore } from '@/stores/uiStore';

/** 可控的视口宽度，用于测试响应式布局（配合 matchMedia 模拟） */
let viewportWidth = 1440;
const listeners = new Set<() => void>();

export function setViewportWidth(width: number) {
  viewportWidth = width;
  listeners.forEach((l) => l());
}

function evaluate(query: string): boolean {
  const min = /min-width:\s*(\d+)px/.exec(query);
  const max = /max-width:\s*(\d+)px/.exec(query);
  if (min && viewportWidth < Number(min[1])) return false;
  if (max && viewportWidth > Number(max[1])) return false;
  if (query.includes('prefers-reduced-motion')) return false;
  return true;
}

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => {
    const handlers = new Set<() => void>();
    const mql = {
      get matches() {
        return evaluate(query);
      },
      media: query,
      onchange: null,
      addEventListener: (_: string, cb: () => void) => {
        handlers.add(cb);
        listeners.add(cb);
      },
      removeEventListener: (_: string, cb: () => void) => {
        handlers.delete(cb);
        listeners.delete(cb);
      },
      addListener: (cb: () => void) => listeners.add(cb),
      removeListener: (cb: () => void) => listeners.delete(cb),
      dispatchEvent: () => false,
    };
    return mql;
  },
});

// jsdom 的 AbortSignal 与 Node 自带 Request 不兼容（React Router 导航时会创建 Request），测试中去掉该 signal
const NodeRequest = globalThis.Request;
class TestRequest extends NodeRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) {
    if (init?.signal) {
      const { signal: _signal, ...rest } = init;
      super(input, rest);
    } else super(input, init);
  }
}
globalThis.Request = TestRequest as typeof Request;

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;

Element.prototype.scrollTo ??= function scrollTo() {};
Element.prototype.scrollIntoView ??= function scrollIntoView() {};
Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.releasePointerCapture ??= () => {};

// Node ≥ 22 自带实验性的全局 localStorage，可能遮蔽 jsdom 的实现；统一提供内存版
function memoryStorage(): Storage {
  const m = new Map<string, string>();
  return {
    get length() {
      return m.size;
    },
    clear: () => m.clear(),
    getItem: (k) => m.get(k) ?? null,
    key: (i) => [...m.keys()][i] ?? null,
    removeItem: (k) => void m.delete(k),
    setItem: (k, v) => void m.set(k, String(v)),
  };
}
if (typeof window.localStorage?.clear !== 'function') {
  Object.defineProperty(window, 'localStorage', { configurable: true, value: memoryStorage() });
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: window.localStorage });
}

beforeEach(() => {
  setViewportWidth(1440);
  window.localStorage.clear();
  // 全局 store 在测试之间重置
  useUiStore.setState({ navOpen: false, statusSheetOpen: false });
  useSettingsStore.setState({ theme: 'light', motion: true, fontSize: 'md', statusPanelOpen: true });
});

afterEach(() => {
  cleanup();
});
