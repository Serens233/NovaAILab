import { memo, type ReactNode } from 'react';
import { Badge } from '@/components/ui/badge';
import { NovaAvatar } from '@/components/character/NovaAvatar';
import type { NovaMessage } from '@/types/nova';
import { cn } from '@/utils/cn';
import { formatMessageTime } from '@/utils/time';
import { MarkdownContent } from './MarkdownContent';

const CHANNEL_LABEL = { web: 'Web', qq: 'QQ', wechat: '微信' } as const;

export const MessageBubble = memo(function MessageBubble({
  message,
  requestedModel,
  footer,
  pending,
}: {
  message: NovaMessage;
  requestedModel?: string;
  footer?: ReactNode;
  pending?: boolean;
}) {
  const isUser = message.role === 'user';
  const isSystem = message.role === 'system' || message.role === 'tool';

  if (isSystem) {
    return (
      <div className="nova-anim-rise mx-auto max-w-[90%] rounded-full border bg-surface px-3 py-1 text-center text-xs text-muted-foreground">
        {message.role === 'tool' ? '工具：' : ''}
        {message.content}
      </div>
    );
  }

  return (
    <article
      className={cn('nova-anim-rise flex gap-2.5', isUser ? 'flex-row-reverse' : 'flex-row')}
      aria-label={isUser ? '你的消息' : 'Nova 的消息'}
      data-role={message.role}
    >
      {!isUser && <NovaAvatar size="sm" className="mt-5" />}
      <div className={cn('flex min-w-0 max-w-[min(85%,680px)] flex-col', isUser ? 'items-end' : 'items-start')}>
        <div className="mb-1 flex items-center gap-1.5 px-1 text-[0.7rem] text-muted-foreground">
          <span className="font-medium">{isUser ? '你' : 'Nova'}</span>
          <time dateTime={message.timestamp}>{formatMessageTime(message.timestamp)}</time>
          {!isUser && message.model && <span>模型：{message.model}</span>}
          {!isUser && requestedModel && message.model && requestedModel !== message.model && <span>请求模型：{requestedModel}</span>}
          {!isUser && message.fallbackReason && <span>已回退（{message.fallbackReason}）</span>}
          {message.channelType && message.channelType !== 'web' && (
            <Badge tone="star" className="py-0">
              来自 {CHANNEL_LABEL[message.channelType]}
            </Badge>
          )}
          {message.deliveryStatus === 'mock' && (
            <Badge tone="gold" className="py-0">
              演示回复
            </Badge>
          )}
          {message.deliveryStatus === 'stopped' && (
            <Badge tone="warning" className="py-0">
              已停止 · 内容不完整
            </Badge>
          )}
        </div>
        <div
          className={cn(
            'rounded-2xl px-4 py-2.5 text-[0.95rem] shadow-soft',
            isUser
              ? 'rounded-tr-md text-bubble-user-fg'
              : 'nova-frame rounded-tl-md border bg-bubble-nova backdrop-blur-sm',
            pending && 'opacity-80',
          )}
          style={isUser ? { background: 'var(--nova-bubble-user)' } : undefined}
        >
          {isUser ? (
            <p className="break-words whitespace-pre-wrap">{message.content}</p>
          ) : (
            <MarkdownContent content={message.content} />
          )}
        </div>
        {footer}
      </div>
    </article>
  );
});
