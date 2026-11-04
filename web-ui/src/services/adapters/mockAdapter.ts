import { NovaApiError } from '@/services/api/errors';
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
import { mockReplyFor, seedChannels, seedConversations, seedMemories, seedMessages } from './mockData';
import type { CallOptions, NovaAdapter } from './types';

/** 可在设置页与测试中切换的演示场景 */
export interface MockScenario {
  /** 模拟后端离线：健康检查报告离线，其它请求抛出网络错误 */
  offline: boolean;
  /** 下一次发送失败一次（之后自动恢复） */
  failNextMessage: boolean;
  /** 流式输出：true 为逐段输出，false 为普通响应 */
  streaming: boolean;
}

export interface MockAdapterOptions {
  latencyMs?: number;
  streamChunkDelayMs?: number;
  scenario?: Partial<MockScenario>;
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new NovaApiError('aborted', '已取消'));
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(new NovaApiError('aborted', '已取消'));
    };
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

/** 将文本按自然断点切片，模拟流式分包 */
export function chunkText(text: string): string[] {
  const parts = text.match(/[^，。！？、\n,.!?]*[，。！？、\n,.!?]?/g) ?? [text];
  const out: string[] = [];
  for (const p of parts) {
    if (!p) continue;
    for (let i = 0; i < p.length; i += 6) out.push(p.slice(i, i + 6));
  }
  return out;
}

export class MockAdapter implements NovaAdapter {
  readonly mode = 'mock' as const;
  readonly scenario: MockScenario;
  private conversations: Conversation[];
  private messages: Record<string, NovaMessage[]>;
  private memories: MemoryRecord[];
  private channels: ChannelInfo[];
  private latency: number;
  private chunkDelay: number;
  private idCounter = 0;
  /** 记录被取消的流，供测试检查资源清理 */
  readonly stats = { streamsStarted: 0, streamsCleanedUp: 0, stopRequests: 0 };

  constructor(options: MockAdapterOptions = {}) {
    this.latency = options.latencyMs ?? 350;
    this.chunkDelay = options.streamChunkDelayMs ?? 45;
    this.scenario = { offline: false, failNextMessage: false, streaming: true, ...options.scenario };
    this.conversations = seedConversations();
    this.messages = seedMessages();
    this.memories = seedMemories();
    this.channels = seedChannels();
  }

  private nextId(prefix: string) {
    this.idCounter += 1;
    return `${prefix}-${this.idCounter}`;
  }

  private async guard(signal?: AbortSignal) {
    await sleep(this.latency, signal);
    if (this.scenario.offline) throw new NovaApiError('network', '（演示）模拟后端离线：无法连接');
  }

  async getBackendHealth(options: CallOptions = {}): Promise<BackendHealth> {
    await sleep(Math.min(this.latency, 120), options.signal);
    if (this.scenario.offline) {
      return {
        state: 'offline',
        checkedAt: new Date().toISOString(),
        memoryService: 'unknown',
        detail: '（演示）模拟后端离线',
      };
    }
    return {
      state: 'online',
      checkedAt: new Date().toISOString(),
      modelName: 'mock-model（演示）',
      backendVersion: 'mock',
      memoryService: 'online',
      latencyMs: Math.min(this.latency, 120),
      detail: 'Mock 演示模式：未连接真实后端',
    };
  }

  async getBackendCapabilities(options: CallOptions = {}): Promise<BackendCapabilities> {
    await this.guard(options.signal);
    return {
      source: 'mock',
      streaming: this.scenario.streaming ? 'sse' : 'none',
      stopGeneration: true,
      conversationPersistence: true,
      renameConversation: true,
      deleteConversation: true,
      memory: true,
      memoryHistory: true,
      channels: true,
      modelSwitch: false,
      personaSettings: false,
    };
  }

  async listConversations(options: CallOptions = {}): Promise<Conversation[]> {
    await this.guard(options.signal);
    return [...this.conversations].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  }

  async createConversation(input: { title?: string } = {}, options: CallOptions = {}): Promise<Conversation> {
    await this.guard(options.signal);
    const now = new Date().toISOString();
    const conv: Conversation = {
      conversationId: this.nextId('mock-conv'),
      title: input.title?.trim() || '新的夜航记录',
      createdAt: now,
      updatedAt: now,
      channelType: 'web',
      messageCount: 0,
    };
    this.conversations.unshift(conv);
    this.messages[conv.conversationId] = [];
    return conv;
  }

  async renameConversation(conversationId: string, title: string, options: CallOptions = {}): Promise<Conversation> {
    await this.guard(options.signal);
    const idx = this.conversations.findIndex((c) => c.conversationId === conversationId);
    if (idx < 0) throw new NovaApiError('http', '会话不存在', { status: 404 });
    this.conversations[idx] = { ...this.conversations[idx], title };
    return this.conversations[idx];
  }

  async deleteConversation(conversationId: string, options: CallOptions = {}): Promise<void> {
    await this.guard(options.signal);
    this.conversations = this.conversations.filter((c) => c.conversationId !== conversationId);
    delete this.messages[conversationId];
  }

  async getConversationMessages(conversationId: string, options: CallOptions = {}): Promise<NovaMessage[]> {
    await this.guard(options.signal);
    const list = this.messages[conversationId];
    if (!list) throw new NovaApiError('http', '会话不存在', { status: 404 });
    return [...list];
  }

  private consumeFailure() {
    if (this.scenario.failNextMessage) {
      this.scenario.failNextMessage = false;
      throw new NovaApiError('http', '（演示）模拟后端处理失败', { status: 500 });
    }
  }

  private record(input: SendMessageInput, reply: string): { user: NovaMessage; assistant: NovaMessage } {
    const now = new Date().toISOString();
    const user: NovaMessage = {
      conversationId: input.conversationId,
      messageId: input.clientMessageId,
      role: 'user',
      content: input.content,
      timestamp: now,
      channelType: 'web',
    };
    const assistant: NovaMessage = {
      conversationId: input.conversationId,
      messageId: this.nextId('mock-msg'),
      role: 'assistant',
      content: reply,
      timestamp: new Date().toISOString(),
      channelType: 'web',
      deliveryStatus: 'mock',
    };
    const list = (this.messages[input.conversationId] ??= []);
    list.push(user, assistant);
    const idx = this.conversations.findIndex((c) => c.conversationId === input.conversationId);
    if (idx >= 0) {
      const conv = this.conversations[idx];
      this.conversations[idx] = {
        ...conv,
        updatedAt: assistant.timestamp,
        messageCount: list.length,
        preview: reply.slice(0, 40),
        title: conv.messageCount === 0 && conv.title === '新的夜航记录' ? input.content.slice(0, 16) : conv.title,
      };
    }
    return { user, assistant };
  }

  async sendMessage(input: SendMessageInput, options: CallOptions = {}): Promise<SendMessageResult> {
    await this.guard(options.signal);
    this.consumeFailure();
    await sleep(this.latency, options.signal);
    const { user, assistant } = this.record(input, mockReplyFor(input.content));
    return { userMessage: user, assistantMessage: assistant };
  }

  async *streamMessage(input: SendMessageInput, options: CallOptions): AsyncGenerator<StreamEvent> {
    this.stats.streamsStarted += 1;
    try {
      await this.guard(options.signal);
      this.consumeFailure();
      const messageId = this.nextId('mock-stream');
      yield { type: 'start', messageId, conversationId: input.conversationId };
      const reply = mockReplyFor(input.content);
      for (const piece of chunkText(reply)) {
        await sleep(this.chunkDelay, options.signal);
        yield { type: 'delta', text: piece };
      }
      const { assistant } = this.record(input, reply);
      yield { type: 'done', message: { ...assistant, messageId } };
    } finally {
      this.stats.streamsCleanedUp += 1;
    }
  }

  async stopGeneration(_input: { conversationId: string; messageId?: string }, options: CallOptions = {}): Promise<void> {
    this.stats.stopRequests += 1;
    await sleep(20, options.signal);
  }

  async getMemoryRecords(query: MemoryQuery = {}, options: CallOptions = {}): Promise<MemoryRecord[]> {
    await this.guard(options.signal);
    const q = query.search?.trim().toLowerCase();
    return this.memories
      .filter((m) => !query.category || query.category === 'all' || m.category === query.category)
      .filter((m) => !q || m.title.toLowerCase().includes(q) || m.content.toLowerCase().includes(q))
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }

  async getChannelStatuses(options: CallOptions = {}): Promise<ChannelInfo[]> {
    await this.guard(options.signal);
    return this.channels.map((c) => ({ ...c }));
  }
}
