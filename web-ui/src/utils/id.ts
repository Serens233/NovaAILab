/** 客户端消息 ID（用于幂等与乐观更新），不依赖 crypto.randomUUID 以兼容非安全上下文（局域网 http） */
export function createClientId(prefix = 'cli'): string {
  const rand =
    typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function'
      ? Array.from(crypto.getRandomValues(new Uint8Array(8)), (b) => b.toString(16).padStart(2, '0')).join('')
      : Math.random().toString(16).slice(2, 18);
  return `${prefix}-${Date.now().toString(36)}-${rand}`;
}
