import type { ReactNode } from 'react';
import { AlertTriangle, CloudOff, Hourglass, RotateCcw, WifiOff } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { NovaStar } from '@/components/character/NovaStar';
import { describeError } from '@/services/api/errors';
import { cn } from '@/utils/cn';

/** 统一的错误提示：根据错误类型区分图标与文案 */
export function ErrorNotice({
  error,
  onRetry,
  className,
  compact,
}: {
  error: unknown;
  onRetry?: () => void;
  className?: string;
  compact?: boolean;
}) {
  const info = describeError(error);
  const Icon =
    info.kind === 'network' ? WifiOff : info.kind === 'not_implemented' ? Hourglass : info.kind === 'timeout' ? CloudOff : AlertTriangle;
  const tone = info.kind === 'not_implemented' ? 'border-warning/30 bg-warning/8' : 'border-danger/30 bg-danger/8';
  return (
    <div
      role="alert"
      data-error-kind={info.kind}
      className={cn('flex items-start gap-3 rounded-lg border px-3 py-2.5 text-sm', tone, className)}
    >
      <Icon className={cn('mt-0.5 size-4 shrink-0', info.kind === 'not_implemented' ? 'text-warning' : 'text-danger')} />
      <div className="min-w-0 flex-1">
        <p className="font-medium">{info.title}</p>
        {!compact && info.detail && <p className="mt-0.5 break-words text-xs text-muted-foreground">{info.detail}</p>}
      </div>
      {onRetry && (
        <Button size="sm" variant="outline" onClick={onRetry} className="shrink-0">
          <RotateCcw /> 重试
        </Button>
      )}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
  icon,
  className,
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  icon?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('flex flex-col items-center justify-center gap-3 px-6 py-10 text-center', className)}>
      {icon ?? <NovaStar size={44} orbit />}
      <p className="font-semibold">{title}</p>
      {description && <div className="max-w-sm text-sm text-muted-foreground">{description}</div>}
      {action}
    </div>
  );
}

/** 档案风格的小标题：前置四芒星 */
export function SectionTitle({ children, className, aside }: { children: ReactNode; className?: string; aside?: ReactNode }) {
  return (
    <div className={cn('flex items-center justify-between gap-2', className)}>
      <h2 className="flex items-center gap-2 text-sm font-semibold tracking-wide">
        <NovaStar size={14} />
        {children}
      </h2>
      {aside}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-pulse rounded-md bg-muted', className)} aria-hidden />;
}
