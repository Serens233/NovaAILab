import { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useNovaAdapter } from '@/services/novaContext';
import { describeError, isAbortError } from '@/services/api/errors';
import type { BackendCapabilities, NovaMessage } from '@/types/nova';
import { createClientId } from '@/utils/id';
import { queryKeys } from './queries';

/**
 * 当前一轮对话的本地状态。只反映真实发生的事件：
 *  - sending：请求已发出，尚未收到任何响应
 *  - waiting：普通 HTTP 模式，等待完整回复
 *  - streaming：流式模式，已建立连接 / 正在接收
 *  - failed / stopped：失败或被用户停止
 */
export type TurnPhase = 'sending' | 'waiting' | 'streaming' | 'failed' | 'stopped';

export interface ActiveTurn {
  conversationId: string;
  requestId?: string;
  userMessage: NovaMessage;
  phase: TurnPhase;
  transport: 'stream' | 'http';
  draft: string;
  receivedFirstChunk: boolean;
  backendStatus?: string;
  assistantMessageId?: string;
  error?: { title: string; detail?: string; kind: string };
  stopError?: string;
}

export function useChatSession(conversationId: string | undefined, capabilities: BackendCapabilities | undefined) {
  const adapter = useNovaAdapter();
  const qc = useQueryClient();
  const [turn, setTurn] = useState<ActiveTurn | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const turnRef = useRef<ActiveTurn | null>(null);
  turnRef.current = turn;

  const update = useCallback((patch: Partial<ActiveTurn>) => {
    setTurn((prev) => (prev ? { ...prev, ...patch } : prev));
  }, []);

  // 切换会话或卸载时：取消进行中的请求，释放流与定时器
  useEffect(() => {
    return () => {
      controllerRef.current?.abort();
      controllerRef.current = null;
      setTurn(null);
    };
  }, [conversationId]);

  const commitMessages = useCallback(
    (convId: string, msgs: NovaMessage[]) => {
      qc.setQueryData<NovaMessage[]>(queryKeys.messages(convId), (prev) => {
        const list = [...(prev ?? [])];
        for (const m of msgs) if (!list.some((x) => x.messageId === m.messageId)) list.push(m);
        return list;
      });
      void qc.invalidateQueries({ queryKey: queryKeys.conversations });
    },
    [qc],
  );

  const useStreaming =
    !!capabilities && (capabilities.streaming === 'sse' || capabilities.streaming === 'ndjson');

  const send = useCallback(
    async (content: string, targetConversationId?: string) => {
      const convId = targetConversationId ?? conversationId;
      const text = content.trim();
      if (!convId || !text) return;
      if (controllerRef.current && !controllerRef.current.signal.aborted) return;

      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;

      const userMessage: NovaMessage = {
        conversationId: convId,
        messageId: createClientId('usr'),
        role: 'user',
        content: text,
        timestamp: new Date().toISOString(),
        channelType: 'web',
      };
      const transport = useStreaming ? 'stream' : 'http';
      const initial: ActiveTurn = {
        conversationId: convId,
        requestId: adapter.mode === 'real' ? crypto.randomUUID() : undefined,
        userMessage,
        phase: transport === 'stream' ? 'sending' : 'waiting',
        transport,
        draft: '',
        receivedFirstChunk: false,
      };
      turnRef.current = initial;
      setTurn(initial);
      const input = {
        conversationId: convId, content: text, clientMessageId: userMessage.messageId,
        requestId: initial.requestId,
      };

      let draft = '';
      let assistantId: string | undefined;
      let storedUserId: string | undefined;
      try {
        if (transport === 'stream') {
          for await (const ev of adapter.streamMessage(input, { signal: controller.signal })) {
            if (controller.signal.aborted) break;
            if (ev.type === 'start') {
              assistantId = ev.messageId ?? assistantId;
              storedUserId = ev.userMessageId ?? storedUserId;
              update({
                phase: 'streaming', assistantMessageId: assistantId,
                ...(storedUserId && { userMessage: { ...userMessage, messageId: storedUserId } }),
              });
            } else if (ev.type === 'status') {
              update({ phase: 'streaming', backendStatus: ev.label });
            } else if (ev.type === 'delta') {
              draft += ev.text;
              update({ phase: 'streaming', draft, receivedFirstChunk: true });
            } else if (ev.type === 'done') {
              const finalMsg: NovaMessage = {
                conversationId: convId,
                messageId: ev.message?.messageId ?? assistantId ?? createClientId('ast'),
                role: 'assistant',
                content: ev.message?.content || draft,
                timestamp: ev.message?.timestamp ?? new Date().toISOString(),
                channelType: 'web',
                deliveryStatus: ev.message?.deliveryStatus,
              };
              commitMessages(convId, [
                ...(storedUserId ? [{ ...userMessage, messageId: storedUserId }] : adapter.mode === 'mock' ? [userMessage] : []),
                finalMsg,
              ]);
              setTurn(null);
              return;
            }
          }
        } else {
          const result = await adapter.sendMessage(input, { signal: controller.signal });
          commitMessages(convId, [
            ...(result.userMessage ? [result.userMessage] : adapter.mode === 'mock' ? [userMessage] : []),
            result.assistantMessage,
          ]);
          setTurn(null);
          return;
        }
      } catch (err) {
        if (controllerRef.current !== controller) return;
        if (isAbortError(err) || controller.signal.aborted) {
          if (adapter.mode === 'real') {
            setTurn((prev) => (prev ? { ...prev, phase: 'stopped' } : prev));
            return;
          }
          // 停止：保留已接收的部分内容，并明确标注「已停止」
          if (draft) {
            commitMessages(convId, [
              userMessage,
              {
                conversationId: convId,
                messageId: assistantId ?? createClientId('ast'),
                role: 'assistant',
                content: draft,
                timestamp: new Date().toISOString(),
                deliveryStatus: 'stopped',
              },
            ]);
            setTurn(null);
          } else {
            setTurn((prev) => (prev ? { ...prev, phase: 'stopped' } : prev));
          }
          return;
        }
        const info = describeError(err);
        setTurn((prev) => (prev ? { ...prev, phase: 'failed', draft, error: info } : prev));
      } finally {
        if (controllerRef.current === controller) controllerRef.current = null;
        if (adapter.mode === 'real') {
          void qc.invalidateQueries({ queryKey: queryKeys.messages(convId) });
          void qc.invalidateQueries({ queryKey: queryKeys.conversations });
        }
      }
    },
    [adapter, commitMessages, conversationId, qc, update, useStreaming],
  );

  const stop = useCallback(async () => {
    const current = turnRef.current;
    const controller = controllerRef.current;
    if (adapter.mode === 'real' && current?.requestId && controller) {
      try {
        await adapter.stopGeneration({
          conversationId: current.conversationId,
          requestId: current.requestId,
        });
      } catch (error) {
        if (controllerRef.current === controller) {
          const info = describeError(error);
          setTurn((prev) => prev ? { ...prev, stopError: info.detail ?? info.title } : prev);
        }
      }
      return;
    }
    controllerRef.current?.abort();
    if (current && capabilities?.stopGeneration) {
      // 通知后端停止生成；失败不影响前端取消
      try {
        await adapter.stopGeneration({
          conversationId: current.conversationId,
          messageId: current.assistantMessageId,
        });
      } catch {
        /* 后端未能处理停止请求：前端已取消接收 */
      }
    }
  }, [adapter, capabilities?.stopGeneration]);

  const retry = useCallback(() => {
    const current = turnRef.current;
    if (!current) return;
    const content = current.userMessage.content;
    const convId = current.conversationId;
    setTurn(null);
    void send(content, convId);
  }, [send]);

  const dismiss = useCallback(() => setTurn(null), []);

  const isBusy = !!turn && ['sending', 'waiting', 'streaming'].includes(turn.phase);

  return { turn, send, stop, retry, dismiss, isBusy, transport: useStreaming ? 'stream' : 'http' } as const;
}
