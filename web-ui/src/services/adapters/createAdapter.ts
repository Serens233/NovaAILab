import type { NovaConfig } from '@/config/env';
import { MockAdapter } from './mockAdapter';
import { RealAdapter } from './realAdapter';
import type { NovaAdapter } from './types';

/** 根据配置创建适配器。real 模式下绝不会创建 MockAdapter。 */
export function createAdapter(config: NovaConfig): NovaAdapter {
  if (config.mode === 'real') {
    return new RealAdapter({
      baseUrl: config.apiBaseUrl,
      timeoutMs: config.requestTimeoutMs,
      streamProtocol: config.streamProtocol,
    });
  }
  return new MockAdapter();
}
