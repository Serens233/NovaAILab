import { NovaApiError } from './errors';

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export interface HttpRequestOptions {
  method?: HttpMethod;
  body?: unknown;
  query?: Record<string, string | number | undefined>;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  /** 等待响应头的超时时间；流式请求在收到响应头后不再受此限制 */
  timeoutMs: number;
  fetchImpl?: typeof fetch;
}

/**
 * 合并外部取消信号与超时信号。返回 cleanup，调用方必须在结束后调用，避免定时器泄漏。
 */
export function createLinkedAbort(external: AbortSignal | undefined, timeoutMs: number) {
  const controller = new AbortController();
  let timedOut = false;
  const onExternalAbort = () => controller.abort(external?.reason);
  if (external) {
    if (external.aborted) controller.abort(external.reason);
    else external.addEventListener('abort', onExternalAbort, { once: true });
  }
  let timer: ReturnType<typeof setTimeout> | undefined = setTimeout(() => {
    timedOut = true;
    controller.abort(new DOMException('timeout', 'TimeoutError'));
  }, timeoutMs);

  return {
    signal: controller.signal,
    didTimeout: () => timedOut,
    /** 停止超时计时（例如流式请求已收到响应头） */
    clearTimer: () => {
      if (timer) clearTimeout(timer);
      timer = undefined;
    },
    cleanup: () => {
      if (timer) clearTimeout(timer);
      timer = undefined;
      external?.removeEventListener('abort', onExternalAbort);
    },
  };
}

export function buildUrl(baseUrl: string, path: string, query?: HttpRequestOptions['query']): string {
  const base = baseUrl.replace(/\/+$/, '');
  const p = path.startsWith('/') ? path : `/${path}`;
  let url = `${base}${p}`;
  if (query) {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== '') params.set(k, String(v));
    const qs = params.toString();
    if (qs) url += (url.includes('?') ? '&' : '?') + qs;
  }
  return url;
}

async function readErrorDetail(res: Response): Promise<{ detail?: string; code?: string }> {
  try {
    const text = await res.text();
    if (!text) return {};
    try {
      const json = JSON.parse(text) as { detail?: unknown; message?: unknown; error?: unknown };
      const d = json.detail ?? json.message ?? json.error;
      if (d && typeof d === 'object' && 'message' in d && typeof d.message === 'string') {
        return { detail: d.message.slice(0, 300), code: 'code' in d && typeof d.code === 'string' ? d.code : undefined };
      }
      return { detail: typeof d === 'string' ? d.slice(0, 300) : JSON.stringify(d ?? json).slice(0, 300) };
    } catch {
      return { detail: text.slice(0, 300) };
    }
  } catch {
    return {};
  }
}

export async function toHttpError(res: Response): Promise<NovaApiError> {
  const { detail, code } = await readErrorDetail(res);
  if ((res.status === 409 || res.status === 503) && code === 'CHAT_CANCELLED') {
    return new NovaApiError('aborted', detail || '生成已取消', { status: res.status });
  }
  if (res.status === 404 || res.status === 405 || res.status === 501) {
    return new NovaApiError('not_implemented', detail || `接口不存在或方法不支持`, { status: res.status, detail });
  }
  if (res.status === 502 || res.status === 503 || res.status === 504) {
    return new NovaApiError('network', detail || '代理或网关无法连接到后端', { status: res.status, detail });
  }
  return new NovaApiError('http', detail || res.statusText || '请求失败', { status: res.status, detail });
}

function normalizeFetchError(err: unknown, timedOut: boolean, external?: AbortSignal): NovaApiError {
  if (timedOut) return new NovaApiError('timeout', '后端在规定时间内没有响应', { cause: err });
  if (external?.aborted) return new NovaApiError('aborted', '请求已取消', { cause: err });
  if (err instanceof DOMException && err.name === 'AbortError') {
    return new NovaApiError('aborted', '请求已取消', { cause: err });
  }
  return new NovaApiError('network', err instanceof Error ? err.message : '网络错误', { cause: err });
}

/**
 * 发送请求并返回 Response（已校验 2xx）。用于流式读取。
 * 返回的 release 必须在读取完毕或出错后调用。
 */
export async function httpRaw(
  baseUrl: string,
  path: string,
  options: HttpRequestOptions,
): Promise<{ response: Response; signal: AbortSignal; release: () => void }> {
  const fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  const linked = createLinkedAbort(options.signal, options.timeoutMs);
  const headers: Record<string, string> = { Accept: 'application/json', ...options.headers };
  let body: BodyInit | undefined;
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(options.body);
  }
  try {
    const response = await fetchImpl(buildUrl(baseUrl, path, options.query), {
      method: options.method ?? 'GET',
      headers,
      body,
      signal: linked.signal,
      credentials: 'same-origin',
    });
    linked.clearTimer();
    if (!response.ok) {
      const error = await toHttpError(response);
      linked.cleanup();
      throw error;
    }
    return { response, signal: linked.signal, release: linked.cleanup };
  } catch (err) {
    linked.cleanup();
    if (err instanceof NovaApiError) throw err;
    throw normalizeFetchError(err, linked.didTimeout(), options.signal);
  }
}

/** 发送请求并解析 JSON（204 返回 undefined） */
export async function httpJson<T>(baseUrl: string, path: string, options: HttpRequestOptions): Promise<T> {
  const fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  const linked = createLinkedAbort(options.signal, options.timeoutMs);
  const headers: Record<string, string> = { Accept: 'application/json', ...options.headers };
  let body: BodyInit | undefined;
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(options.body);
  }
  try {
    const response = await fetchImpl(buildUrl(baseUrl, path, options.query), {
      method: options.method ?? 'GET',
      headers,
      body,
      signal: linked.signal,
      credentials: 'same-origin',
    });
    if (!response.ok) throw await toHttpError(response);
    if (response.status === 204) return undefined as T;
    const text = await response.text();
    if (!text) return undefined as T;
    try {
      return JSON.parse(text) as T;
    } catch (err) {
      throw new NovaApiError('parse', '后端返回的不是有效 JSON', { cause: err, detail: text.slice(0, 200) });
    }
  } catch (err) {
    if (err instanceof NovaApiError) throw err;
    throw normalizeFetchError(err, linked.didTimeout(), options.signal);
  } finally {
    linked.cleanup();
  }
}
