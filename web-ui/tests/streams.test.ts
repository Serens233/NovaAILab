import { describe, expect, it } from 'vitest';
import { createNDJSONParser, createSSEParser, parseNDJSON, parseSSE } from '@/services/api/streams';
import { mapStreamPayload } from '@/services/adapters/realMappers';
import { chunkedStream } from './utils';

describe('SSE 解析器', () => {
  it('处理被拆成多个分包的事件', () => {
    const p = createSSEParser();
    expect(p.push('data: {"de')).toEqual([]);
    expect(p.push('lta":"你好"}\n')).toEqual([]);
    expect(p.push('\n')).toEqual([{ event: 'message', data: '{"delta":"你好"}', id: undefined }]);
  });

  it('一个分包里包含多个事件、注释心跳与自定义事件名', () => {
    const p = createSSEParser();
    const out = p.push(': ping\n\nevent: delta\ndata: a\n\nevent: done\ndata: [DONE]\n\n');
    expect(out).toEqual([
      { event: 'delta', data: 'a', id: undefined },
      { event: 'done', data: '[DONE]', id: undefined },
    ]);
  });

  it('\\r\\n 被拆在两个分包之间时不会产生多余的空行', () => {
    const p = createSSEParser();
    expect(p.push('data: a\r')).toEqual([]);
    expect(p.push('\ndata: b\r\n\r\n')).toEqual([{ event: 'message', data: 'a\nb', id: undefined }]);
  });

  it('流结束时 flush 未以空行结尾的最后一个事件', () => {
    const p = createSSEParser();
    p.push('data: tail');
    expect(p.flush()).toEqual([{ event: 'message', data: 'tail', id: undefined }]);
  });

  it('parseSSE 从字节流中按顺序产出事件', async () => {
    const { stream } = chunkedStream(['data: 一', '\n\ndata: 二\n', '\n']);
    const got: string[] = [];
    for await (const m of parseSSE(stream)) got.push(m.data);
    expect(got).toEqual(['一', '二']);
  });

  it('多字节中文被拆在字节边界上也能正确解码', async () => {
    const bytes = new TextEncoder().encode('data: 星图\n\n');
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(bytes.slice(0, 8)); // 截断在「星」字中间
        c.enqueue(bytes.slice(8));
        c.close();
      },
    });
    const got: string[] = [];
    for await (const m of parseSSE(stream)) got.push(m.data);
    expect(got).toEqual(['星图']);
  });
});

describe('NDJSON 解析器', () => {
  it('处理跨分包的 JSON 行', () => {
    const p = createNDJSONParser();
    expect(p.push('{"delta":"a"}\n{"del')).toEqual([{ delta: 'a' }]);
    expect(p.push('ta":"b"}\n')).toEqual([{ delta: 'b' }]);
    expect(p.flush()).toEqual([]);
  });

  it('parseNDJSON 读取流并在结束时 flush', async () => {
    const { stream } = chunkedStream(['{"a":1}\n{"a"', ':2}']);
    const got: unknown[] = [];
    for await (const o of parseNDJSON(stream)) got.push(o);
    expect(got).toEqual([{ a: 1 }, { a: 2 }]);
  });

  it('非法 JSON 行抛出 parse 错误', () => {
    const p = createNDJSONParser();
    expect(() => p.push('not json\n')).toThrowError(/NDJSON/);
  });
});

describe('流事件映射（兼容多种后端格式）', () => {
  it.each([
    ['message', '{"type":"delta","text":"x"}', { type: 'delta', text: 'x' }],
    ['message', '{"delta":"x"}', { type: 'delta', text: 'x' }],
    ['message', '{"content":"x"}', { type: 'delta', text: 'x' }],
    ['message', '{"choices":[{"delta":{"content":"x"}}]}', { type: 'delta', text: 'x' }],
    ['message', '纯文本片段', { type: 'delta', text: '纯文本片段' }],
    ['message', '[DONE]', { type: 'done' }],
    ['done', '', { type: 'done' }],
    ['error', 'boom', { type: 'error', message: 'boom' }],
    ['message', '{"error":{"message":"限流"}}', { type: 'error', message: '限流', code: undefined }],
  ])('%s / %s', (event, data, expected) => {
    expect(mapStreamPayload(event, data)).toEqual(expected);
  });
});
