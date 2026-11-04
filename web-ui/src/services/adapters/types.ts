import type {
  BackendCapabilities,
  BackendHealth,
  ChannelInfo,
  Conversation,
  MemoryQuery,
  MemoryRecord,
  NovaMessage,
  SendMessageInput,
  SendMessageResult,
  StreamEvent,
} from '@/types/nova';

export interface CallOptions {
  signal?: AbortSignal;
}

/**
 * 前端逻辑服务能力。UI 只依赖这个接口，不直接依赖任何 FastAPI 路由。
 * 注意：方法名不代表后端存在同名端点；真实路由见 realRoutes.ts。
 */
export interface NovaAdapter {
  /** 放弃本地重试信息，不影响服务器中的记录。 */
  forgetRequest?(requestId: string): void;
  readonly mode: 'mock' | 'real';

  getBackendHealth(options?: CallOptions): Promise<BackendHealth>;
  getBackendCapabilities(options?: CallOptions): Promise<BackendCapabilities>;

  listConversations(options?: CallOptions): Promise<Conversation[]>;
  createConversation(input?: { title?: string }, options?: CallOptions): Promise<Conversation>;
  renameConversation(conversationId: string, title: string, options?: CallOptions): Promise<Conversation>;
  deleteConversation(conversationId: string, options?: CallOptions): Promise<void>;
  getConversationMessages(conversationId: string, options?: CallOptions): Promise<NovaMessage[]>;

  /** 普通（非流式）发送，返回完整回复 */
  sendMessage(input: SendMessageInput, options?: CallOptions): Promise<SendMessageResult>;
  /** 流式发送。调用方通过 signal 取消；迭代器结束时必须释放所有资源 */
  streamMessage(input: SendMessageInput, options: CallOptions): AsyncIterable<StreamEvent>;
  /** 通知后端停止生成（后端支持时） */
  stopGeneration(input: { conversationId: string; messageId?: string; requestId?: string }, options?: CallOptions): Promise<void>;

  getMemoryRecords(query?: MemoryQuery, options?: CallOptions): Promise<MemoryRecord[]>;
  getChannelStatuses(options?: CallOptions): Promise<ChannelInfo[]>;
}

export const NO_CAPABILITIES: BackendCapabilities = {
  source: 'assumed',
  streaming: 'none',
  stopGeneration: false,
  conversationPersistence: false,
  renameConversation: false,
  deleteConversation: false,
  memory: false,
  memoryHistory: false,
  channels: false,
  modelSwitch: false,
  personaSettings: false,
};
