import { useSyncExternalStore } from 'react';
import { useHealth } from './queries';
import { useNovaServices } from '@/services/novaContext';

export type ConnectionState = 'checking' | 'online' | 'degraded' | 'offline' | 'browser_offline' | 'not_configured';

function useBrowserOnline(): boolean {
  return useSyncExternalStore(
    (cb) => {
      window.addEventListener('online', cb);
      window.addEventListener('offline', cb);
      return () => {
        window.removeEventListener('online', cb);
        window.removeEventListener('offline', cb);
      };
    },
    () => navigator.onLine,
    () => true,
  );
}

export const CONNECTION_LABEL: Record<ConnectionState, string> = {
  checking: '正在检测连接',
  online: '后端在线',
  degraded: '部分可用',
  offline: '后端离线',
  browser_offline: '本机网络已断开',
  not_configured: '未识别到 Nova 后端',
};

/**
 * 统一的连接状态。只依据真实健康检查结果 —— 未检测到之前显示「正在检测」，不会预设为在线。
 */
export function useConnectionState() {
  const { adapter, config } = useNovaServices();
  const health = useHealth();
  const browserOnline = useBrowserOnline();

  let state: ConnectionState;
  // navigator.onLine describes the network, not reachability of a local backend.
  // A successful health check is authoritative even while the internet is offline.
  if (health.data?.state === 'online' || health.data?.state === 'degraded') state = health.data.state;
  else if (!browserOnline && adapter.mode === 'real' && !health.data) state = 'browser_offline';
  else if (!health.data) state = health.isError ? 'offline' : 'checking';
  else state = health.data.state;

  return {
    state,
    label: CONNECTION_LABEL[state],
    health: health.data,
    isMock: adapter.mode === 'mock',
    apiBaseUrl: config.apiBaseUrl,
    recheck: () => health.refetch(),
    isChecking: health.isFetching,
  };
}
