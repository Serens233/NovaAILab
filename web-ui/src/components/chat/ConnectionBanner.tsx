import { RefreshCw, WifiOff } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { useConnectionState } from '@/hooks/useConnectionState';

/** 后端离线 / 网络断开时的醒目提示。在线时不渲染。 */
export function ConnectionBanner({ conn }: { conn: ReturnType<typeof useConnectionState> }) {
  if (conn.state === 'online' || conn.state === 'checking') return null;
  const text =
    conn.state === 'browser_offline'
      ? '本机网络已断开，消息无法发送。'
      : conn.state === 'degraded'
        ? '后端部分可用，部分功能可能失败。'
        : conn.state === 'not_configured'
          ? `${conn.apiBaseUrl} 上的服务不是可识别的 Nova 后端。`
        : `无法连接到 Nova 后端（${conn.apiBaseUrl}），消息暂时无法送达。`;
  return (
    <div
      role="alert"
      data-testid="connection-banner"
      className={`flex items-center gap-2 border-b px-4 py-2 text-xs ${conn.state === 'degraded' ? 'bg-warning/10' : 'bg-danger/10'}`}
    >
      <WifiOff className={`size-4 shrink-0 ${conn.state === 'degraded' ? 'text-warning' : 'text-danger'}`} />
      <span className="min-w-0 flex-1">
        {text}
        {conn.health?.detail && <span className="ml-1 text-muted-foreground">{conn.health.detail}</span>}
      </span>
      <Button size="sm" variant="ghost" onClick={() => conn.recheck()} disabled={conn.isChecking}>
        <RefreshCw className={conn.isChecking ? 'animate-spin' : undefined} /> 重新检测
      </Button>
    </div>
  );
}
