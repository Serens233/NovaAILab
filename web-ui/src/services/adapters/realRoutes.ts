import type { HttpMethod } from '@/services/api/http';

/**
 * Nova FastAPI 路由相对于同源代理 `/api`。健康检查由代理转发至 `/health`，
 * `/api/v1/*` 保留原路径。未接入的能力不会发请求。
 */

export type RouteKey =
  | 'health'
  | 'models'
  | 'capabilities'
  | 'openapi'
  | 'listConversations'
  | 'createConversation'
  | 'renameConversation'
  | 'deleteConversation'
  | 'conversationMessages'
  | 'sendMessage'
  | 'streamMessage'
  | 'stopGeneration'
  | 'memoryRecords'
  | 'channelStatuses';

export interface RouteSpec {
  method: HttpMethod;
  path: string;
  status: 'verified' | 'suggested';
  note?: string;
}

export type RouteMap = Record<RouteKey, RouteSpec | null>;

export const DEFAULT_ROUTES: RouteMap = {
  health: { method: 'GET', path: '/health', status: 'verified' },
  models: { method: 'GET', path: '/v1/models', status: 'verified' },
  capabilities: null,
  openapi: null,
  listConversations: { method: 'GET', path: '/v1/threads', status: 'verified' },
  createConversation: { method: 'POST', path: '/v1/threads', status: 'verified' },
  renameConversation: { method: 'PATCH', path: '/v1/threads/{conversationId}', status: 'verified' },
  deleteConversation: { method: 'DELETE', path: '/v1/threads/{conversationId}', status: 'verified' },
  conversationMessages: { method: 'GET', path: '/v1/threads/{conversationId}', status: 'verified' },
  sendMessage: { method: 'POST', path: '/v1/chat', status: 'verified', note: 'stream=false' },
  streamMessage: {
    method: 'POST',
    path: '/v1/chat',
    status: 'verified',
    note: 'stream=true，SSE',
  },
  stopGeneration: { method: 'POST', path: '/v1/chat/cancel', status: 'verified' },
  memoryRecords: null,
  channelStatuses: null,
};

export function resolvePath(spec: RouteSpec, params: Record<string, string> = {}): string {
  return spec.path.replace(/\{(\w+)\}/g, (_, key: string) => {
    const value = params[key];
    if (value === undefined) throw new Error(`路由 ${spec.path} 缺少参数 ${key}`);
    return encodeURIComponent(value);
  });
}

/** 判断 OpenAPI 文档中是否存在与路由规格匹配的路径和方法（路径参数名不同也视为匹配） */
export function openApiHasRoute(openapi: unknown, spec: RouteSpec, apiPrefix = ''): boolean {
  if (!openapi || typeof openapi !== 'object') return false;
  const paths = (openapi as { paths?: Record<string, Record<string, unknown>> }).paths;
  if (!paths) return false;
  const normalize = (p: string) => p.replace(/\{[^}]+\}/g, '{}').replace(/\/+$/, '') || '/';
  const candidates = [normalize(spec.path), normalize(`${apiPrefix}${spec.path}`)];
  return Object.entries(paths).some(
    ([path, ops]) => candidates.includes(normalize(path)) && !!ops && spec.method.toLowerCase() in ops,
  );
}
