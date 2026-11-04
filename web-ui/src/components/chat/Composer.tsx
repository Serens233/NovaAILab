import { useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react';
import { SendHorizontal, Square } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/input';
import { cn } from '@/utils/cn';

const MAX_LENGTH = 8000;

/**
 * 输入栏。
 *  - Enter 发送，Shift+Enter 换行；
 *  - 中文输入法组字（composition）期间按 Enter 只确认候选词，不会发送；
 *  - 生成中显示「停止」。
 */
export function Composer({
  onSend,
  onStop,
  busy,
  disabled,
  disabledReason,
  canStop = true,
}: {
  onSend: (text: string) => void;
  onStop: () => void;
  busy: boolean;
  disabled?: boolean;
  disabledReason?: string;
  canStop?: boolean;
}) {
  const [value, setValue] = useState('');
  const composingRef = useRef(false);
  /** 部分浏览器（Safari）在 compositionend 之后才派发 keydown(Enter)，用时间窗兜底 */
  const compositionEndAtRef = useRef(0);
  const taRef = useRef<HTMLTextAreaElement>(null);

  // 自适应高度（最多约 8 行）
  useLayoutEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`;
  }, [value]);

  const canSend = !busy && !disabled && value.trim().length > 0;

  const submit = () => {
    if (!canSend) return;
    onSend(value.trim());
    setValue('');
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== 'Enter' || e.shiftKey) return;
    const native = e.nativeEvent;
    const imeActive =
      composingRef.current ||
      native.isComposing ||
      native.keyCode === 229 ||
      Date.now() - compositionEndAtRef.current < 50;
    if (imeActive) return; // 让输入法处理这次回车
    e.preventDefault();
    submit();
  };

  return (
    <div className="shrink-0 px-3 pt-2 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:px-6">
      <form
        className={cn(
          'nova-panel mx-auto flex w-full max-w-[var(--nova-chat-max-width)] items-end gap-2 p-2 focus-within:shadow-glow',
          disabled && 'opacity-80',
        )}
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <Textarea
          ref={taRef}
          value={value}
          onChange={(e) => setValue(e.target.value.slice(0, MAX_LENGTH))}
          onKeyDown={onKeyDown}
          onCompositionStart={() => {
            composingRef.current = true;
          }}
          onCompositionEnd={() => {
            composingRef.current = false;
            compositionEndAtRef.current = Date.now();
          }}
          rows={1}
          disabled={disabled}
          placeholder={disabled ? (disabledReason ?? '暂时无法发送') : '和 Nova 说点什么…（Enter 发送，Shift+Enter 换行）'}
          aria-label="输入消息"
          enterKeyHint="send"
          className="max-h-[200px] min-h-10 resize-none border-0 bg-transparent px-2 py-2 text-[0.95rem] shadow-none focus-visible:ring-0"
          data-testid="composer-input"
        />
        {busy ? (
          <Button
            type="button"
            variant="outline"
            size="icon"
            onClick={onStop}
            disabled={!canStop}
            aria-label="停止生成"
            data-testid="stop-button"
            className="shrink-0 rounded-xl"
          >
            <Square className="fill-current" />
          </Button>
        ) : (
          <Button
            type="submit"
            size="icon"
            disabled={!canSend}
            aria-label="发送"
            data-testid="send-button"
            className="shrink-0 rounded-xl"
          >
            <SendHorizontal />
          </Button>
        )}
      </form>
      {disabled && disabledReason && (
        <p className="mx-auto mt-1.5 max-w-[var(--nova-chat-max-width)] px-1 text-center text-[0.72rem] text-muted-foreground">
          {disabledReason}
        </p>
      )}
    </div>
  );
}
