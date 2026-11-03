import type { StreamProtocol } from '@/config/env';
import { NovaApiError, isNovaApiError } from '@/services/api/errors';
import { httpJson, httpRaw, type HttpRequestOptions } from '@/services/api/http';
import { parseNDJSON, parseSSE } from '@/services/api/streams';
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
import {
  localModelSelection,
  mapCapabilities,
  mapChannel,
  mapConversation,
  mapHealth,
  mapMemory,
  mapMessage,
  mapSendResult,
  mapStreamPayload,
  toSendMessageBody,
  unwrapList,
} from './realMappers';
import { DEFAULT_ROUTES, resolvePath, type RouteKey, type RouteMap, type RouteSpec } from './realRoutes';
import type { CallOptions, NovaAdapter } from './types';

export interface RealAdapterOptions {
  baseUrl: string;
  timeoutMs: number;
  streamProtocol: StreamProtocol;
  routes?: Partial<RouteMap>;
  fetchImpl?: typeof fetch;
  /** 流式响应两次数据之间的最长等待 */
  streamIdleTimeoutMs?: number;
}

/**
 * 连接真实 Nova FastAPI 后端的适配器。
 * 原则：任何失败都如实抛出 / 报告，绝不回退成 Mock 成功数据。
 */
export class RealAdapter implements NovaAdapter {
  readonly mode = 'real' as const;
  private readonly routes: RouteMap;
  private readonly opts: RealAdapterOptions;
  private openApiPromise?: Promise<unknown | null>;
  private streamProtocol: StreamProtocol | 'websocket';

  constructor(opts: RealAdapterOptions) {
    this.opts = opts;
    this.routes = { ...DEFAULT_ROUTES, ...opts.routes };
    this.streamProtocol = opts.streamProtocol;
  }

  getRouteMap(): RouteMap {
    return this.routes;
  }

  private route(key: RouteKey): RouteSpec {
    const spec = this.routes[key];
    if (!spec) throw new NovaApiError('not_implemented', `前端路由映射中未配置「${key}」，该能力暂不可用`);
    return spec;
  }

  private req(spec: RouteSpec, extra: Partial<HttpRequestOptions> = {}): HttpRequestOptions {
    return { method: spec.method, timeoutMs: this.opts.timeoutMs, fetchImpl: this.opts.fetchImpl, ...extra };
  }

  private call<T>(key: RouteKey, params: Record<string, string> = {}, extra: Partial<HttpRequestOptions> = {}) {
    const spec = this.route(key);
    return httpJson<T>(this.opts.baseUrl, resolvePath(spec, params), this.req(spec, extra));
  }

  /** 读取 FastAPI 的 OpenAPI 文档（缓存；失败返回 null，下次重试） */
  fetchOpenApi(signal?: AbortSignal): Promise<unknown | null> {
    if (!this.routes.openapi) return Promise.resolve(null);
    if (!this.openApiPromise) {
      this.openApiPromise = this.call<unknown>('openapi', {}, { signal }).catch((err: unknown) => {
        this.openApiPromise = undefined;
        if (isNovaApiError(err) && (err.kind === 'not_implemented' || err.kind === 'parse')) return null;
        throw err;
      });
    }
    return this.openApiPromise;
  }

  async getBackendHealth(options: CallOptions = {}): Promise<BackendHealth> {
    const started = performance.now();
    const offline = (err: unknown): BackendHealth => ({
      state: 'offline',
      checkedAt: new Date().toISOString(),
      detail: isNovaApiError(err) ? `${err.message}${err.status ? `（HTTP ${err.status}）` : ''}` : String(err),
      memoryService: 'unknown',
    });
    try {
      if (this.routes.health) {
        const raw = await this.call<unknown>('health', {}, { signal: options.signal });
        return mapHealth(raw, Math.round(performance.now() - started));
      }
    } catch (err) {
      if (isNovaApiError(err) && err.kind === 'aborted') throw err;
      if (!(isNovaApiError(err) && err.kind === 'not_implemented')) return offline(err);
    }
    return {
      state: 'not_configured',
      checkedAt: new Date().toISOString(),
      memoryService: 'unknown',
      detail: '无法找到 Nova 健康检查接口。请检查 API 地址与代理目标。',
    };
  }

  async getBackendCapabilities(options: CallOptions = {}): Promise<BackendCapabilities> {
    let caps: BackendCapabilities | undefined;
    if (this.routes.capabilities) {
      try {
        caps = mapCapabilities(await this.call<unknown>('capabilities', {}, { signal: options.signal }));
      } catch (err) {
        if (!(isNovaApiError(err) && err.kind === 'not_implemented')) throw err;
      }
    }
    if (!caps) caps = await this.inferCapabilities(options.signal);
    this.streamProtocol = caps.streaming === 'none' ? 'none' : caps.streaming;
    return caps;
  }

  /** 已接通鉴权后的 Nova 聊天 API，再从核实过的路由表推断能力。 */
  private async inferCapabilities(signal?: AbortSignal): Promise<BackendCapabilities> {
    await this.modelSelection(signal);
    const has = (key: RouteKey) => this.routes[key]?.status === 'verified';
    const streamingAvailable = this.opts.streamProtocol !== 'none' && has('streamMessage');
    return {
      source: 'assumed',
      streaming: streamingAvailable ? this.opts.streamProtocol : 'none',
      stopGeneration: has('stopGeneration'),
      conversationPersistence: has('listConversations') && has('createConversation'),
      renameConversation: has('renameConversation'),
      deleteConversation: has('deleteConversation'),
      memory: has('memoryRecords'),
      memoryHistory: false,
      channels: has('channelStatuses'),
      modelSwitch: false,
      personaSettings: false,
    };
  }

  async listConversations(options: CallOptions = {}): Promise<Conversation[]> {
    const raw = await this.call<unknown>('listConversations', {}, { signal: options.signal });
    return unwrapList(raw, ['threads']).map(mapConversation);
  }

  async createConversation(input: { title?: string } = {}, options: CallOptions = {}): Promise<Conversation> {
    const selection = await this.modelSelection(options.signal);
    const raw = await this.call<unknown>('createConversation', {}, {
      signal: options.signal,
      body: { title: input.title, model: selection.availableModel ?? selection.defaultModel },
    });
    return mapConversation(raw);
  }

  async renameConversation(conversationId: string, title: string, options: CallOptions = {}): Promise<Conversation> {
    return mapConversation(
      await this.call<unknown>('renameConversation', { conversationId }, { signal: options.signal, body: { title } }),
    );
  }

  async deleteConversation(conversationId: string, options: CallOptions = {}): Promise<void> {
    await this.call<unknown>('deleteConversation', { conversationId }, { signal: options.signal });
  }

  async getConversationMessages(conversationId: string, options: CallOptions = {}): Promise<NovaMessage[]> {
    const raw = await this.call<unknown>('conversationMessages', { conversationId }, { signal: options.signal });
    return unwrapList(raw, ['messages']).map((m) => mapMessage(m, conversationId));
  }

  private async modelSelection(signal?: AbortSignal) {
    return localModelSelection(await this.call<unknown>('models', {}, { signal }));
  }

  private async modelForGeneration(signal?: AbortSignal) {
    const { availableModel } = await this.modelSelection(signal);
    if (!availableModel) {
      throw new NovaApiError('not_configured', '没有可用的本地 9B 或 4B 模型，请确认 Ollama 已启动且模型已安装');
    }
    return availableModel;
  }

  async sendMessage(input: SendMessageInput, options: CallOptions = {}): Promise<SendMessageResult> {
    const model = await this.modelForGeneration(options.signal);
    const raw = await this.call<unknown>(
      'sendMessage',
      { conversationId: input.conversationId },
      { signal: options.signal, body: toSendMessageBody(input, model, false), timeoutMs: Math.max(this.opts.timeoutMs, 300_000) },
    );
    return mapSendResult(raw, input);
  }

  async *streamMessage(input: SendMessageInput, options: CallOptions): AsyncGenerator<StreamEvent> {
    const protocol = this.streamProtocol;
    if (protocol === 'none') throw new NovaApiError('not_configured', '未启用流式协议');
    if (protocol === 'websocket') {
      throw new NovaApiError('not_implemented', 'WebSocket 流式尚未接入，请在适配层实现或改用普通响应');
    }
    const spec = this.route('streamMessage');
    const model = await this.modelForGeneration(options.signal);
    const { response, signal, release } = await httpRaw(
      this.opts.baseUrl,
      resolvePath(spec, { conversationId: input.conversationId }),
      this.req(spec, {
        signal: options.signal,
        body: toSendMessageBody(input, model, true),
        timeoutMs: Math.max(this.opts.timeoutMs, 120_000),
        headers: { Accept: protocol === 'sse' ? 'text/event-stream' : 'application/x-ndjson' },
      }),
    );
    let start: Extract<StreamEvent, { type: 'start' }> | undefined;
    try {
      if (!response.body) throw new NovaApiError('stream', '浏览器无法读取流式响应体');
      if (protocol === 'sse' && !response.headers.get('Content-Type')?.startsWith('text/event-stream')) {
        await response.body.cancel().catch(() => undefined);
        throw new NovaApiError('stream', '后端未返回 SSE 响应');
      }
      const idle = this.opts.streamIdleTimeoutMs ?? 120_000;
      const source =
        protocol === 'sse'
          ? (async function* () {
              for await (const m of parseSSE(response.body!, signal, idle)) {
                let payload: unknown;
                try {
                  payload = JSON.parse(m.data);
                } catch {
                  throw new NovaApiError('parse', '后端流事件格式无效');
                }
                yield mapStreamPayload(m.event, payload);
              }
            })()
          : (async function* () {
              for await (const obj of parseNDJSON(response.body!, signal, idle)) yield mapStreamPayload('message', obj);
            })();
      for await (const ev of source) {
        if (!ev) continue;
        if (ev.type === 'start') {
          if (start || ev.conversationId !== input.conversationId || !ev.userMessageId || !ev.messageId) {
            throw new NovaApiError('stream', '后端流式消息 ID 或会话不匹配');
          }
          start = ev;
        }
        if (ev.type === 'error') {
          throw new NovaApiError(ev.code === 'CHAT_CANCELLED' ? 'aborted' : 'stream', ev.message);
        }
        if (ev.type === 'done') {
          if (!start || !ev.message || ev.message.messageId !== start.messageId ||
              ev.message.conversationId !== input.conversationId || ev.message.role !== 'assistant' ||
              ev.message.deliveryStatus !== 'complete') {
            throw new NovaApiError('stream', '后端流未返回已保存的完整回复');
          }
          yield ev;
          return;
        }
        yield ev;
      }
      throw new NovaApiError('stream', '连接已结束，未收到后端完成确认');
    } catch (err) {
      if (signal.aborted && !(isNovaApiError(err) && err.kind === 'timeout')) {
        throw new NovaApiError('aborted', '已停止生成', { cause: err });
      }
      throw err;
    } finally {
      release();
    }
  }

  async stopGeneration(input: { conversationId: string; requestId?: string }, options: CallOptions = {}): Promise<void> {
    if (!input.requestId) throw new NovaApiError('not_configured', '缺少本轮生成的请求 ID');
    await this.call<unknown>(
      'stopGeneration',
      { conversationId: input.conversationId },
      { signal: options.signal, body: { thread_id: input.conversationId, request_id: input.requestId } },
    );
  }

  async getMemoryRecords(query: MemoryQuery = {}, options: CallOptions = {}): Promise<MemoryRecord[]> {
    const raw = await this.call<unknown>('memoryRecords', {}, {
      signal: options.signal,
      query: { q: query.search, category: query.category === 'all' ? undefined : query.category },
    });
    return unwrapList(raw, ['memories', 'records']).map(mapMemory);
  }

  async getChannelStatuses(options: CallOptions = {}): Promise<ChannelInfo[]> {
    const raw = await this.call<unknown>('channelStatuses', {}, { signal: options.signal });
    return unwrapList(raw, ['channels']).map(mapChannel);
  }
}
