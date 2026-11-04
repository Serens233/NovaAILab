import { NovaApiError } from './errors';

/**
 * 流式响应解析：SSE（text/event-stream）与 NDJSON（每行一个 JSON）。
 * 两者都能正确处理「一个事件被拆成多个网络分包」或「一个分包里有多个事件」的情况。
 */

export interface SSEMessage {
  event: string;
  data: string;
  id?: string;
}

/** 读取字节流为文本块，支持空闲超时与取消，结束时一定释放 reader。 */
async function* readTextChunks(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
  idleTimeoutMs = 60_000,
): AsyncGenerator<string> {
  const reader = body.getReader();
  const decoder = new TextDecoder('utf-8');
  let idleTimer: ReturnType<typeof setTimeout> | undefined;
  let rejectIdle: ((e: unknown) => void) | undefined;
  const onAbort = () => rejectIdle?.(new NovaApiError('aborted', '已停止接收'));
  signal?.addEventListener('abort', onAbort, { once: true });

  try {
    while (true) {
      if (signal?.aborted) throw new NovaApiError('aborted', '已停止接收');
      const idle = new Promise<never>((_, reject) => {
        rejectIdle = reject;
        idleTimer = setTimeout(
          () => reject(new NovaApiError('timeout', `流式响应超过 ${Math.round(idleTimeoutMs / 1000)} 秒没有新数据`)),
          idleTimeoutMs,
        );
      });
      let result: ReadableStreamReadResult<Uint8Array>;
      try {
        result = await Promise.race([reader.read(), idle]);
      } finally {
        clearTimeout(idleTimer);
      }
      if (result.done) break;
      const text = decoder.decode(result.value, { stream: true });
      if (text) yield text;
    }
    const tail = decoder.decode();
    if (tail) yield tail;
  } finally {
    signal?.removeEventListener('abort', onAbort);
    try {
      await reader.cancel();
    } catch {
      /* 已关闭 */
    }
    reader.releaseLock();
  }
}

/** 纯函数：增量 SSE 解析器，便于单元测试 */
export function createSSEParser() {
  let buffer = '';
  let event = '';
  let data: string[] = [];
  let id: string | undefined;

  function dispatch(out: SSEMessage[]) {
    if (data.length > 0) out.push({ event: event || 'message', data: data.join('\n'), id });
    event = '';
    data = [];
  }

  return {
    push(chunk: string): SSEMessage[] {
      buffer += chunk;
      const out: SSEMessage[] = [];
      // 若以 \r 结尾，可能是 \r\n 被拆在两个分包之间：先保留这个 \r
      const keepCR = buffer.endsWith('\r');
      const usable = keepCR ? buffer.slice(0, -1) : buffer;
      // 统一换行符；末尾可能是不完整的一行，留在 buffer
      const lines = usable.split(/\r\n|\r|\n/);
      buffer = (lines.pop() ?? '') + (keepCR ? '\r' : '');
      for (const line of lines) {
        if (line === '') {
          dispatch(out);
          continue;
        }
        if (line.startsWith(':')) continue; // 注释 / 心跳
        const idx = line.indexOf(':');
        const field = idx === -1 ? line : line.slice(0, idx);
        let value = idx === -1 ? '' : line.slice(idx + 1);
        if (value.startsWith(' ')) value = value.slice(1);
        if (field === 'event') event = value;
        else if (field === 'data') data.push(value);
        else if (field === 'id') id = value;
      }
      return out;
    },
    flush(): SSEMessage[] {
      const out: SSEMessage[] = buffer ? this.push('\n') : [];
      buffer = '';
      dispatch(out);
      return out;
    },
  };
}

export async function* parseSSE(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
  idleTimeoutMs?: number,
): AsyncGenerator<SSEMessage> {
  const parser = createSSEParser();
  for await (const chunk of readTextChunks(body, signal, idleTimeoutMs)) {
    for (const msg of parser.push(chunk)) yield msg;
  }
  for (const msg of parser.flush()) yield msg;
}

export function createNDJSONParser() {
  let buffer = '';
  const parseLine = (line: string): unknown => {
    try {
      return JSON.parse(line);
    } catch (err) {
      throw new NovaApiError('parse', 'NDJSON 行不是有效 JSON', { cause: err, detail: line.slice(0, 200) });
    }
  };
  return {
    push(chunk: string): unknown[] {
      buffer += chunk;
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      return lines.map((l) => l.trim()).filter(Boolean).map(parseLine);
    },
    flush(): unknown[] {
      const rest = buffer.trim();
      buffer = '';
      return rest ? [parseLine(rest)] : [];
    },
  };
}

export async function* parseNDJSON(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
  idleTimeoutMs?: number,
): AsyncGenerator<unknown> {
  const parser = createNDJSONParser();
  for await (const chunk of readTextChunks(body, signal, idleTimeoutMs)) {
    for (const obj of parser.push(chunk)) yield obj;
  }
  for (const obj of parser.flush()) yield obj;
}
