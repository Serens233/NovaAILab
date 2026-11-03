/**
 * Nova 前端数据模型。
 * 这些类型是「可讨论的前端接口草案」，最终以 Nova 后端已确认的数据模型为准。
 * 后端字段名不同时，只需修改 services/adapters/realMappers.ts，UI 不受影响。
 */

export type ChannelType = 'web' | 'qq' | 'wechat';

export type ChannelStatus = 'not_configured' | 'disconnected' | 'connecting' | 'connected' | 'error';

export interface ChannelInfo {
  channelId: string;
  channelType: ChannelType;
  displayName: string;
  status: ChannelStatus;
  capabilities?: string[];
  lastSyncAt?: string;
  lastError?: string;
  /** 主动消息授权状态（后端提供时） */
  proactiveMessaging?: 'granted' | 'denied' | 'unknown';
}

export type MessageRole = 'user' | 'assistant' | 'system' | 'tool';

export interface NovaMessage {
  conversationId: string;
  messageId: string;
  role: MessageRole;
  content: string;
  timestamp: string;
  channelType?: ChannelType;
  externalMessageId?: string;
  deliveryStatus?: string;
  model?: string;
  fallbackReason?: string;
}

export interface Conversation {
  conversationId: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  channelType?: ChannelType;
  messageCount?: number;
  preview?: string;
  model?: string;
  /** 仅保存在当前页面内存中的临时会话（后端未提供会话持久化时使用），刷新即丢失 */
  localOnly?: boolean;
}

export type BackendHealthState = 'online' | 'degraded' | 'offline' | 'not_configured';

export interface BackendHealth {
  state: BackendHealthState;
  checkedAt: string;
  /** 后端报告的模型名，未报告时为 undefined —— 不要猜测 */
  modelName?: string;
  backendVersion?: string;
  memoryService?: 'online' | 'offline' | 'unknown';
  latencyMs?: number;
  detail?: string;
}

/** 后端能力声明。未知的能力一律为 false，对应 UI 显示「暂不可用」。 */
export interface BackendCapabilities {
  /** 能力数据来源：backend=后端声明；assumed=按适配层配置推断；mock=演示 */
  source: 'backend' | 'assumed' | 'mock';
  streaming: 'sse' | 'ndjson' | 'websocket' | 'none';
  stopGeneration: boolean;
  conversationPersistence: boolean;
  renameConversation: boolean;
  deleteConversation: boolean;
  memory: boolean;
  memoryHistory: boolean;
  channels: boolean;
  modelSwitch: boolean;
  personaSettings: boolean;
}

export type MemoryCategory = 'preference' | 'event' | 'knowledge' | 'relationship' | 'habit' | 'other';

export interface MemoryRevision {
  revisedAt: string;
  summary: string;
}

export interface MemoryRecord {
  memoryId: string;
  title: string;
  content: string;
  category: MemoryCategory;
  createdAt: string;
  source?: { channelType?: ChannelType; conversationId?: string; description?: string };
  revisions?: MemoryRevision[];
  importance?: number;
}

export interface MemoryQuery {
  search?: string;
  category?: MemoryCategory | 'all';
}

/** 流式事件（适配层统一后的格式） */
export type StreamEvent =
  | { type: 'start'; messageId?: string; userMessageId?: string; conversationId?: string; model?: string; fallbackReason?: string }
  | { type: 'delta'; text: string }
  | { type: 'status'; label: string }
  | { type: 'done'; message?: Partial<NovaMessage> }
  | { type: 'error'; message: string; code?: string };

export interface SendMessageInput {
  conversationId: string;
  content: string;
  clientMessageId: string;
  /** Nova 服务端去重和取消共用的 UUID。 */
  requestId?: string;
}

export interface SendMessageResult {
  userMessage?: NovaMessage;
  assistantMessage: NovaMessage;
}
