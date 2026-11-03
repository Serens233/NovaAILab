/**
 * 统一错误模型：让 UI 能区分「API 错误 / 网络断开 / 超时 / 未配置 / 未实现 / 主动取消」。
 */
export type NovaErrorKind =
  | 'network' // 无法连接（后端离线、代理失败、DNS 等）
  | 'timeout' // 请求超时
  | 'http' // 后端返回非 2xx
  | 'not_configured' // 前端未配置该能力或地址
  | 'not_implemented' // 后端尚未提供该接口（404/405/501 或映射未配置）
  | 'aborted' // 用户主动取消
  | 'parse' // 响应格式无法解析
  | 'stream'; // 流传输过程中的错误

export class NovaApiError extends Error {
  readonly kind: NovaErrorKind;
  readonly status?: number;
  readonly detail?: string;
  readonly retryable: boolean;

  constructor(
    kind: NovaErrorKind,
    message: string,
    options: { status?: number; detail?: string; cause?: unknown } = {},
  ) {
    super(message, { cause: options.cause });
    this.name = 'NovaApiError';
    this.kind = kind;
    this.status = options.status;
    this.detail = options.detail;
    this.retryable =
      kind === 'network' ||
      kind === 'timeout' ||
      kind === 'stream' ||
      (kind === 'http' && (options.status ?? 0) >= 500);
  }
}

export function isNovaApiError(err: unknown): err is NovaApiError {
  return err instanceof NovaApiError;
}

export function isAbortError(err: unknown): boolean {
  return (
    (isNovaApiError(err) && err.kind === 'aborted') ||
    (typeof DOMException !== 'undefined' && err instanceof DOMException && err.name === 'AbortError')
  );
}

const KIND_LABEL: Record<NovaErrorKind, string> = {
  network: '无法连接到 Nova 后端',
  timeout: '请求超时',
  http: '后端返回错误',
  not_configured: '尚未配置',
  not_implemented: '后端暂未提供此功能',
  aborted: '已取消',
  parse: '响应格式无法识别',
  stream: '流式传输中断',
};

export function errorKindLabel(kind: NovaErrorKind): string {
  return KIND_LABEL[kind];
}

/** 将任意错误转成适合展示给用户的中文说明 */
export function describeError(err: unknown): { title: string; detail?: string; kind: NovaErrorKind | 'unknown' } {
  if (isNovaApiError(err)) {
    return {
      title: KIND_LABEL[err.kind],
      detail: err.message + (err.status ? `（HTTP ${err.status}）` : ''),
      kind: err.kind,
    };
  }
  if (err instanceof Error) return { title: '发生未知错误', detail: err.message, kind: 'unknown' };
  return { title: '发生未知错误', kind: 'unknown' };
}
