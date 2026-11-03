import { RotateCcw, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { NovaAvatar } from '@/components/character/NovaAvatar';
import type { ActiveTurn } from '@/hooks/useChatSession';
import { MarkdownContent } from './MarkdownContent';

/**
 * 进行中的回复。状态文字严格对应真实事件，不把加载动画描述为「思考过程」。
 */
export function PendingAssistant({ turn }: { turn: ActiveTurn }) {
  let label: string;
  if (turn.phase === 'sending') label = '消息已发出，正在等待后端建立连接…';
  else if (turn.phase === 'waiting') label = '等待后端返回完整回复（当前为普通响应，非流式）…';
  else if (!turn.receivedFirstChunk) label = '已连接，等待第一段回复…';
  else label = '正在接收回复…';

  return (
    <div className="nova-anim-rise flex gap-2.5" aria-live="polite" data-testid="pending-assistant">
      <NovaAvatar size="sm" className="mt-5" />
      <div className="flex min-w-0 max-w-[min(85%,680px)] flex-col items-start">
        <div className="mb-1 px-1 text-[0.7rem] text-muted-foreground">Nova</div>
        <div className="nova-frame min-w-24 rounded-2xl rounded-tl-md border bg-bubble-nova px-4 py-2.5 text-[0.95rem] shadow-soft">
          {turn.draft ? (
            <MarkdownContent content={turn.draft} />
          ) : (
            <span className="flex h-6 items-center gap-1" aria-hidden>
              <span className="nova-typing-dot size-1.5 rounded-full bg-primary" />
              <span className="nova-typing-dot size-1.5 rounded-full bg-accent" />
              <span className="nova-typing-dot size-1.5 rounded-full bg-star" />
            </span>
          )}
        </div>
        <p className="mt-1 px-1 text-[0.72rem] text-muted-foreground" data-testid="turn-status">
          {label}
          {turn.backendStatus && ` · 后端状态：${turn.backendStatus}`}
        </p>
        {turn.stopError && <p role="alert" className="mt-1 px-1 text-xs text-danger">停止请求失败：{turn.stopError}</p>}
      </div>
    </div>
  );
}

/** 失败 / 停止后的提示与操作 */
export function TurnFooter({ turn, onRetry, onDismiss }: { turn: ActiveTurn; onRetry: () => void; onDismiss: () => void }) {
  if (turn.phase !== 'failed' && turn.phase !== 'stopped') return null;
  const failed = turn.phase === 'failed';
  return (
    <div
      role={failed ? 'alert' : 'status'}
      data-testid={failed ? 'turn-error' : 'turn-stopped'}
      data-error-kind={turn.error?.kind}
      className={`mt-1.5 w-full max-w-sm rounded-lg border px-3 py-2 text-xs ${failed ? 'border-danger/30 bg-danger/8' : 'border-warning/30 bg-warning/8'}`}
    >
      <p className="font-medium text-foreground">
        {failed ? `发送失败：${turn.error?.title ?? '未知错误'}` : '已停止。'}
        {!failed && turn.transport === 'http' && '已取消等待，后端可能仍在处理这条消息。'}
      </p>
      {failed && turn.error?.detail && <p className="mt-0.5 break-words text-muted-foreground">{turn.error.detail}</p>}
      <div className="mt-2 flex gap-2">
        <Button size="sm" variant="outline" onClick={onRetry}>
          <RotateCcw /> 重新发送
        </Button>
        <Button size="sm" variant="ghost" onClick={onDismiss}>
          <X /> 撤回
        </Button>
      </div>
    </div>
  );
}
