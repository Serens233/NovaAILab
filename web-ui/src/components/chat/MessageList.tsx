import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { ArrowDown } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { ActiveTurn } from '@/hooks/useChatSession';
import type { NovaMessage } from '@/types/nova';
import { MessageBubble } from './MessageBubble';
import { PendingAssistant, TurnFooter } from './TurnStatus';

const STICK_THRESHOLD = 96;

/**
 * 消息列表。
 *  - 用户位于底部附近时，新内容自动滚到底；
 *  - 用户向上翻阅历史时保持阅读位置，并显示「回到最新」按钮。
 */
export function MessageList({
  conversationId,
  messages,
  turn,
  onRetry,
  onDismiss,
  header,
}: {
  conversationId: string;
  messages: NovaMessage[];
  turn: ActiveTurn | null;
  onRetry: () => void;
  onDismiss: () => void;
  header?: ReactNode;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const [showJump, setShowJump] = useState(false);

  const scrollToBottom = useCallback((smooth = false) => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
    stickRef.current = true;
    setShowJump(false);
  }, []);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD;
    stickRef.current = atBottom;
    if (atBottom) setShowJump(false);
  };

  // 切换会话：直接定位到底部
  useLayoutEffect(() => {
    scrollToBottom(false);
  }, [conversationId, scrollToBottom]);

  // 新内容：仅当用户在底部时跟随
  const contentKey = `${messages.length}:${turn?.phase ?? ''}:${turn?.draft.length ?? 0}`;
  useEffect(() => {
    if (stickRef.current) scrollToBottom(false);
    else setShowJump(true);
  }, [contentKey, scrollToBottom]);

  // 用户自己发送消息时总是滚到底
  const userTurnId = turn?.userMessage.messageId;
  useEffect(() => {
    if (userTurnId) scrollToBottom(true);
  }, [userTurnId, scrollToBottom]);

  const showTurnUser = turn && !messages.some((m) => m.messageId === turn.userMessage.messageId);
  const busy = turn && ['sending', 'waiting', 'streaming'].includes(turn.phase);

  return (
    <div className="relative min-h-0 flex-1">
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="h-full overflow-y-auto overscroll-contain"
        data-testid="message-scroll"
        role="log"
        aria-label="对话消息"
        aria-live="polite"
        aria-relevant="additions"
      >
        <div className="mx-auto flex w-full max-w-[var(--nova-chat-max-width)] flex-col gap-5 px-3 py-6 sm:px-6">
          {header}
          {messages.map((m, index) => (
            <MessageBubble key={m.messageId} message={m} requestedModel={m.role === 'assistant' && messages[index - 1]?.role === 'user' ? messages[index - 1]?.model : undefined} />
          ))}
          {turn && showTurnUser && (
            <MessageBubble
              message={turn.userMessage}
              pending={busy ?? false}
              footer={<TurnFooter turn={turn} onRetry={onRetry} onDismiss={onDismiss} />}
            />
          )}
          {turn && busy && <PendingAssistant turn={turn} />}
          {turn && !showTurnUser && !busy && (
            <TurnFooter turn={turn} onRetry={onRetry} onDismiss={onDismiss} />
          )}
        </div>
      </div>
      {showJump && (
        <Button
          size="sm"
          variant="outline"
          className="nova-anim-rise absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full shadow-panel"
          onClick={() => scrollToBottom(true)}
        >
          <ArrowDown /> 有新内容
        </Button>
      )}
    </div>
  );
}
