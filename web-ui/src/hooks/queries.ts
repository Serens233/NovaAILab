import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNovaServices } from '@/services/novaContext';
import type { Conversation, MemoryQuery } from '@/types/nova';

export const queryKeys = {
  health: ['nova', 'health'] as const,
  capabilities: ['nova', 'capabilities'] as const,
  conversations: ['nova', 'conversations'] as const,
  messages: (id: string) => ['nova', 'messages', id] as const,
  memories: (q: MemoryQuery) => ['nova', 'memories', q.search ?? '', q.category ?? 'all'] as const,
  channels: ['nova', 'channels'] as const,
};

/** 后端健康状态（轮询）。健康检查本身不抛错：离线会以 state='offline' 返回。 */
export function useHealth() {
  const { adapter, config } = useNovaServices();
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: ({ signal }) => adapter.getBackendHealth({ signal }),
    refetchInterval: config.healthPollMs,
    refetchIntervalInBackground: false,
    retry: false,
    staleTime: 5_000,
  });
}

export function useCapabilities() {
  const { adapter } = useNovaServices();
  const health = useHealth();
  const online = health.data?.state === 'online' || health.data?.state === 'degraded';
  return useQuery({
    // 后端从离线恢复后重新获取能力
    queryKey: [...queryKeys.capabilities, online],
    queryFn: ({ signal }) => adapter.getBackendCapabilities({ signal }),
    enabled: online,
    retry: 1,
    staleTime: 60_000,
  });
}

export function useConversations() {
  const { adapter } = useNovaServices();
  return useQuery({
    queryKey: queryKeys.conversations,
    queryFn: ({ signal }) => adapter.listConversations({ signal }),
    retry: 1,
    staleTime: 15_000,
  });
}

export function useMessages(conversationId: string | undefined) {
  const { adapter } = useNovaServices();
  return useQuery({
    queryKey: queryKeys.messages(conversationId ?? ''),
    queryFn: ({ signal }) => adapter.getConversationMessages(conversationId!, { signal }),
    enabled: !!conversationId,
    retry: 1,
    staleTime: 30_000,
  });
}

export function useMemories(query: MemoryQuery, enabled = true) {
  const { adapter } = useNovaServices();
  return useQuery({
    queryKey: queryKeys.memories(query),
    queryFn: ({ signal }) => adapter.getMemoryRecords(query, { signal }),
    enabled,
    retry: false,
    placeholderData: (prev) => prev,
  });
}

export function useChannelStatuses() {
  const { channels } = useNovaServices();
  return useQuery({
    queryKey: queryKeys.channels,
    queryFn: ({ signal }) => channels.getChannelStatuses(signal),
    retry: false,
    refetchInterval: 60_000,
  });
}

export function useCreateConversation() {
  const { adapter } = useNovaServices();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { title?: string } = {}) => adapter.createConversation(input),
    onSuccess: (conv) => {
      qc.setQueryData<Conversation[]>(queryKeys.conversations, (prev) => [
        conv,
        ...(prev ?? []).filter((c) => c.conversationId !== conv.conversationId),
      ]);
      qc.setQueryData(queryKeys.messages(conv.conversationId), []);
    },
  });
}

export function useRenameConversation() {
  const { adapter } = useNovaServices();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) => adapter.renameConversation(id, title),
    onSuccess: (conv) => {
      qc.setQueryData<Conversation[]>(queryKeys.conversations, (prev) =>
        (prev ?? []).map((c) => (c.conversationId === conv.conversationId ? { ...c, ...conv } : c)),
      );
    },
  });
}

export function useDeleteConversation() {
  const { adapter } = useNovaServices();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => adapter.deleteConversation(id),
    onSuccess: (_, id) => {
      qc.setQueryData<Conversation[]>(queryKeys.conversations, (prev) =>
        (prev ?? []).filter((c) => c.conversationId !== id),
      );
      qc.removeQueries({ queryKey: queryKeys.messages(id) });
    },
  });
}
