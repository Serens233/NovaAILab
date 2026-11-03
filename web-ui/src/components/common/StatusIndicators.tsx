import { FlaskConical, PlugZap } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Tip } from '@/components/ui/tooltip';
import type { ConnectionState } from '@/hooks/useConnectionState';
import { CONNECTION_LABEL } from '@/hooks/useConnectionState';
import { cn } from '@/utils/cn';

const TONE: Record<ConnectionState, 'success' | 'warning' | 'danger' | 'neutral'> = {
  online: 'success',
  degraded: 'warning',
  offline: 'danger',
  browser_offline: 'danger',
  checking: 'neutral',
  not_configured: 'warning',
};

const DOT: Record<ConnectionState, string> = {
  online: 'bg-success',
  degraded: 'bg-warning',
  offline: 'bg-danger',
  browser_offline: 'bg-danger',
  checking: 'bg-muted-foreground animate-pulse',
  not_configured: 'bg-warning',
};

export function ConnectionBadge({ state, className }: { state: ConnectionState; className?: string }) {
  return (
    <Badge tone={TONE[state]} className={className} data-testid="connection-badge" data-state={state}>
      <span className={cn('size-1.5 rounded-full', DOT[state])} aria-hidden />
      {CONNECTION_LABEL[state]}
    </Badge>
  );
}

/** 当前 API 模式：Mock 演示 / 真实后端。Mock 时始终醒目提示。 */
export function ModeBadge({ isMock, className }: { isMock: boolean; className?: string }) {
  return isMock ? (
    <Tip content="Mock 演示模式：所有数据均为本地模拟，未连接真实 Nova 后端。">
      <Badge tone="gold" className={className} data-testid="mode-badge">
        <FlaskConical /> 演示模式
      </Badge>
    </Tip>
  ) : (
    <Tip content="真实模式：通过 API 适配层连接 Nova 后端。">
      <Badge tone="star" className={className} data-testid="mode-badge">
        <PlugZap /> 真实后端
      </Badge>
    </Tip>
  );
}
