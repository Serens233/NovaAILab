import type {
  BackendCapabilities,
  BackendHealth,
  ChannelInfo,
  ChannelStatus,
  ChannelType,
  Conversation,
  MemoryCategory,
  MemoryRecord,
  MessageRole,
  NovaMessage,
  SendMessageInput,
  StreamEvent,
} from '@/types/nova';
import { NovaApiError } from '@/services/api/errors';

/**
 * Nova FastAPI JSON 与前端数据模型的映射。
 */

type Obj = Record<string, unknown>;

const isObj = (v: unknown): v is Obj => !!v && typeof v === 'object' && !Array.isArray(v);

function pick(obj: Obj, keys: string[]): unknown {
  for (const k of keys) if (obj[k] !== undefined && obj[k] !== null) return obj[k];
  return undefined;
}
function str(obj: Obj, keys: string[], fallback = ''): string {
  const v = pick(obj, keys);
  return v === undefined ? fallback : String(v);
}
function optStr(obj: Obj, keys: string[]): string | undefined {
  const v = pick(obj, keys);
  return v === undefined ? undefined : String(v);
}
function toIso(v: string | undefined): string {
  if (!v) return new Date().toISOString();
  const n = Number(v);
  // 兼容秒/毫秒时间戳
  if (Number.isFinite(n) && v.trim() !== '') return new Date(n < 1e12 ? n * 1000 : n).toISOString();
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? v : d.toISOString();
}

/** 解包常见的 { data: ... } / { items: [...] } 结构 */
export function unwrapList(raw: unknown, keys: string[]): unknown[] {
  if (Array.isArray(raw)) return raw;
  if (isObj(raw)) {
    for (const k of [...keys, 'items', 'data', 'results']) {
      const v = raw[k];
      if (Array.isArray(v)) return v;
      if (isObj(v)) {
        return unwrapList(v, keys);
      }
    }
  }
  throw new NovaApiError('parse', '后端返回的列表格式无法识别');
}

function unwrapObj(raw: unknown, keys: string[]): Obj {
  if (!isObj(raw)) throw new NovaApiError('parse', '后端返回的对象格式无法识别');
  for (const k of keys) if (isObj(raw[k])) return raw[k] as Obj;
  return raw;
}

const CHANNEL_TYPES: ChannelType[] = ['web', 'qq', 'wechat'];
function toChannelType(v: unknown): ChannelType | undefined {
  const s = String(v ?? '').toLowerCase();
  if (s === 'weixin' || s === 'wx') return 'wechat';
  return (CHANNEL_TYPES as string[]).includes(s) ? (s as ChannelType) : undefined;
}

export function mapConversation(raw: unknown): Conversation {
  const o = unwrapObj(raw, ['thread', 'conversation', 'data']);
  const id = str(o, ['conversation_id', 'conversationId', 'id', 'session_id', 'sessionId']);
  if (!id) throw new NovaApiError('parse', '会话数据缺少 id');
  const created = toIso(optStr(o, ['created_at', 'createdAt', 'created']));
  return {
    conversationId: id,
    title: str(o, ['title', 'name', 'topic'], '未命名的夜航记录'),
    createdAt: created,
    updatedAt: toIso(optStr(o, ['updated_at', 'updatedAt', 'last_message_at', 'lastMessageAt']) ?? created),
    channelType: toChannelType(pick(o, ['channel_type', 'channelType', 'channel', 'source'])),
    messageCount: Number(pick(o, ['message_count', 'messageCount'])) || undefined,
    preview: optStr(o, ['preview', 'last_message', 'lastMessage']),
  };
}

const ROLES: MessageRole[] = ['user', 'assistant', 'system', 'tool'];
export function mapMessage(raw: unknown, conversationId: string): NovaMessage {
  const o = unwrapObj(raw, ['message', 'data']);
  let role = str(o, ['role', 'sender', 'author'], 'assistant').toLowerCase();
  if (role === 'human') role = 'user';
  if (role === 'ai' || role === 'bot' || role === 'nova') role = 'assistant';
  const id = str(o, ['message_id', 'messageId', 'id']);
  const threadId = str(o, ['thread_id', 'conversation_id', 'conversationId', 'session_id'], conversationId);
  if (!id || (conversationId && conversationId !== threadId)) {
    throw new NovaApiError('parse', '后端消息缺少 ID 或会话不匹配');
  }
  const status = optStr(o, ['status', 'delivery_status', 'deliveryStatus']);
  return {
    conversationId: threadId,
    messageId: id,
    role: (ROLES as string[]).includes(role) ? (role as MessageRole) : 'assistant',
    content: str(o, ['content', 'text', 'message', 'reply', 'response']),
    timestamp: toIso(optStr(o, ['timestamp', 'created_at', 'createdAt', 'time'])),
    channelType: toChannelType(pick(o, ['channel_type', 'channelType', 'channel'])),
    externalMessageId: optStr(o, ['external_message_id', 'externalMessageId']),
    deliveryStatus: status === 'interrupted' ? 'stopped' : status,
  };
}

const LOCAL_MODELS = ['qwen3.5:9b', 'qwen3.5:4b'] as const;

export function localModelSelection(raw: unknown) {
  const catalog = unwrapObj(raw, []);
  const models = catalog.models;
  if (!Array.isArray(models)) {
    throw new NovaApiError('parse', '后端模型列表格式无效');
  }
  const defaultModel = LOCAL_MODELS.find((id) => id === catalog.default_model);
  const available = LOCAL_MODELS.filter((id) => models.some(
    (entry: unknown) => isObj(entry) && entry.id === id && entry.provider === 'ollama' && entry.available === true,
  ));
  return {
    defaultModel,
    availableModel: defaultModel && available.includes(defaultModel) ? defaultModel : available[0],
  };
}

export function toSendMessageBody(input: SendMessageInput, model: string, stream: boolean) {
  return {
    thread_id: input.conversationId,
    model,
    content: input.content,
    request_id: input.requestId ?? crypto.randomUUID(),
    stream,
    cloud_consent: false,
  };
}

export function mapSendResult(raw: unknown, input: SendMessageInput) {
  if (!isObj(raw) || raw.thread_id !== input.conversationId || !isObj(raw.message)) {
    throw new NovaApiError('parse', '后端回复缺少真实的会话或消息');
  }
  const assistantMessage = mapMessage(raw.message, input.conversationId);
  if (assistantMessage.role !== 'assistant' || assistantMessage.deliveryStatus !== 'complete') {
    throw new NovaApiError('parse', '后端未返回已保存的完整回复');
  }
  return { assistantMessage };
}

const HEALTH_OK = ['ok', 'healthy', 'online', 'up', 'ready', 'pass'];
export function mapHealth(raw: unknown, latencyMs: number): BackendHealth {
  const o = unwrapObj(raw, []);
  const rawStatus = pick(o, ['status', 'state', 'health']);
  if (typeof rawStatus !== 'string' || !rawStatus.trim()) {
    throw new NovaApiError('parse', '后端健康检查缺少状态');
  }
  const status = rawStatus.toLowerCase();
  const memory = pick(o, ['memory_service', 'memoryService', 'memory']);
  let memoryService: BackendHealth['memoryService'] = 'unknown';
  if (typeof memory === 'boolean') memoryService = memory ? 'online' : 'offline';
  else if (typeof memory === 'string') memoryService = HEALTH_OK.includes(memory.toLowerCase()) ? 'online' : 'offline';
  else if (isObj(memory)) {
    const ms = String(memory.status ?? '').toLowerCase();
    memoryService = ms ? (HEALTH_OK.includes(ms) ? 'online' : 'offline') : 'unknown';
  }
  return {
    state: HEALTH_OK.includes(status) ? 'online' : 'degraded',
    checkedAt: new Date().toISOString(),
    modelName: optStr(o, ['model', 'model_name', 'modelName', 'llm_model']),
    backendVersion: optStr(o, ['version', 'backend_version', 'app_version']),
    memoryService,
    latencyMs,
    detail: HEALTH_OK.includes(status) ? undefined : `后端报告状态：${status}`,
  };
}

export function mapCapabilities(raw: unknown): BackendCapabilities {
  const o = unwrapObj(raw, ['capabilities', 'data']);
  const b = (keys: string[]) => pick(o, keys) === true;
  const streamingRaw = String(pick(o, ['streaming', 'stream_protocol', 'streamProtocol']) ?? 'none').toLowerCase();
  const streaming: BackendCapabilities['streaming'] =
    streamingRaw === 'sse' || streamingRaw === 'ndjson' || streamingRaw === 'websocket'
      ? streamingRaw
      : streamingRaw === 'true'
        ? 'sse'
        : 'none';
  return {
    source: 'backend',
    streaming,
    stopGeneration: b(['stop_generation', 'stopGeneration', 'cancel']),
    conversationPersistence: b(['conversation_persistence', 'conversationPersistence', 'conversations']),
    renameConversation: b(['rename_conversation', 'renameConversation']),
    deleteConversation: b(['delete_conversation', 'deleteConversation']),
    memory: b(['memory', 'memories']),
    memoryHistory: b(['memory_history', 'memoryHistory']),
    channels: b(['channels', 'channel_management']),
    modelSwitch: b(['model_switch', 'modelSwitch']),
    personaSettings: b(['persona_settings', 'personaSettings']),
  };
}

const MEMORY_CATEGORIES: MemoryCategory[] = ['preference', 'event', 'knowledge', 'relationship', 'habit', 'other'];
export function mapMemory(raw: unknown): MemoryRecord {
  const o = unwrapObj(raw, ['memory']);
  const cat = str(o, ['category', 'type', 'kind'], 'other').toLowerCase();
  const sourceRaw = pick(o, ['source', 'origin']);
  const revisionsRaw = pick(o, ['revisions', 'history', 'edits']);
  return {
    memoryId: str(o, ['memory_id', 'memoryId', 'id']),
    title: str(o, ['title', 'summary', 'name'], '未命名记忆'),
    content: str(o, ['content', 'text', 'detail', 'summary']),
    category: (MEMORY_CATEGORIES as string[]).includes(cat) ? (cat as MemoryCategory) : 'other',
    createdAt: toIso(optStr(o, ['created_at', 'createdAt', 'formed_at', 'timestamp'])),
    importance: Number(pick(o, ['importance', 'weight', 'score'])) || undefined,
    source: isObj(sourceRaw)
      ? {
          channelType: toChannelType(pick(sourceRaw, ['channel_type', 'channelType', 'channel'])),
          conversationId: optStr(sourceRaw, ['conversation_id', 'conversationId']),
          description: optStr(sourceRaw, ['description', 'label']),
        }
      : typeof sourceRaw === 'string'
        ? { description: sourceRaw }
        : undefined,
    revisions: Array.isArray(revisionsRaw)
      ? revisionsRaw.filter(isObj).map((r) => ({
          revisedAt: toIso(optStr(r, ['revised_at', 'revisedAt', 'timestamp', 'created_at'])),
          summary: str(r, ['summary', 'description', 'change'], '修改'),
        }))
      : undefined,
  };
}

const CHANNEL_STATUSES: ChannelStatus[] = ['not_configured', 'disconnected', 'connecting', 'connected', 'error'];
export function mapChannel(raw: unknown): ChannelInfo {
  const o = unwrapObj(raw, ['channel']);
  const type = toChannelType(pick(o, ['channel_type', 'channelType', 'type', 'platform']));
  if (!type) throw new NovaApiError('parse', '渠道数据缺少有效的 channel_type');
  const status = str(o, ['status', 'state'], 'not_configured').toLowerCase();
  const proactive = pick(o, ['proactive_messaging', 'proactiveMessaging', 'proactive']);
  return {
    channelId: str(o, ['channel_id', 'channelId', 'id'], type),
    channelType: type,
    displayName: str(o, ['display_name', 'displayName', 'name'], type),
    status: (CHANNEL_STATUSES as string[]).includes(status) ? (status as ChannelStatus) : 'error',
    capabilities: Array.isArray(o.capabilities) ? o.capabilities.map(String) : undefined,
    lastSyncAt: optStr(o, ['last_sync_at', 'lastSyncAt']),
    lastError: optStr(o, ['last_error', 'lastError', 'error']),
    proactiveMessaging:
      proactive === true || proactive === 'granted'
        ? 'granted'
        : proactive === false || proactive === 'denied'
          ? 'denied'
          : 'unknown',
  };
}

/**
 * 把后端流事件统一成前端 StreamEvent。
 * 兼容：OpenAI 风格 [DONE]、{type:'delta',text}、{delta}、{content}、{token}、纯文本 data、event:error/done。
 */
export function mapStreamPayload(eventName: string, data: unknown): StreamEvent | null {
  const ev = eventName.toLowerCase();
  if (typeof data === 'string') {
    const trimmed = data.trim();
    if (trimmed === '[DONE]') return { type: 'done' };
    if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
      try {
        return mapStreamPayload(eventName, JSON.parse(trimmed));
      } catch {
        /* 当作纯文本 */
      }
    }
    if (ev === 'error') return { type: 'error', message: data || '后端流式错误' };
    if (ev === 'done' || ev === 'end' || ev === 'complete') return { type: 'done' };
    if (ev === 'start') return { type: 'start' };
    return data ? { type: 'delta', text: data } : null;
  }
  if (!isObj(data)) return null;
  const type = String(data.type ?? data.event ?? ev).toLowerCase();
  if (type === 'error' || data.error) {
    const e = data.error;
    const message = typeof e === 'string' ? e : isObj(e) ? String(e.message ?? '后端流式错误') : String(data.message ?? '后端流式错误');
    return { type: 'error', message, code: optStr(data, ['code']) };
  }
  if (type === 'done' || type === 'end' || type === 'complete' || data.done === true) {
    const m = pick(data, ['message', 'assistant_message']);
    return { type: 'done', message: isObj(m) ? mapMessage(m, '') : undefined };
  }
  if (type === 'meta') {
    return {
      type: 'start',
      conversationId: optStr(data, ['thread_id']),
      messageId: optStr(data, ['assistant_message_id']),
      userMessageId: optStr(data, ['user_message_id']),
    };
  }
  if (type === 'start') {
    return { type: 'start', messageId: optStr(data, ['message_id', 'messageId', 'id']), conversationId: optStr(data, ['conversation_id', 'conversationId']) };
  }
  if (type === 'status') return { type: 'status', label: str(data, ['label', 'status', 'message'], '处理中') };
  // OpenAI 兼容：choices[0].delta.content
  const choices = data.choices;
  if (Array.isArray(choices) && isObj(choices[0]) && isObj(choices[0].delta)) {
    const c = choices[0].delta.content;
    return typeof c === 'string' && c ? { type: 'delta', text: c } : null;
  }
  const text = pick(data, ['delta', 'text', 'content', 'token', 'chunk']);
  if (typeof text === 'string') return text ? { type: 'delta', text } : null;
  return null;
}
