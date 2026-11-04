/** 运行时配置：统一从 import.meta.env 读取并校验，避免在组件中散落读取。 */

export type ApiMode = 'mock' | 'real';
export type StreamProtocol = 'sse' | 'ndjson' | 'none';

export interface NovaConfig {
  mode: ApiMode;
  apiBaseUrl: string;
  streamProtocol: StreamProtocol;
  requestTimeoutMs: number;
  healthPollMs: number;
  appVersion: string;
  /** 配置中的问题（非致命），会在设置页诊断区域展示 */
  warnings: string[];
}

type EnvLike = Record<string, string | boolean | undefined>;

function toNumber(value: unknown, fallback: number, min: number): number {
  const n = Number(value);
  return Number.isFinite(n) && n >= min ? n : fallback;
}

export function parseConfig(env: EnvLike, appVersion = '0.0.0'): NovaConfig {
  const warnings: string[] = [];
  const rawMode = String(env.VITE_NOVA_API_MODE ?? 'mock').trim().toLowerCase();
  let mode: ApiMode = 'mock';
  if (rawMode === 'real' || rawMode === 'mock') mode = rawMode;
  else warnings.push(`未知的 VITE_NOVA_API_MODE="${rawMode}"，已使用 mock。`);

  let apiBaseUrl = String(env.VITE_NOVA_API_BASE_URL ?? '/api').trim();
  if (!apiBaseUrl) {
    apiBaseUrl = '/api';
    warnings.push('VITE_NOVA_API_BASE_URL 为空，已使用 /api。');
  }
  apiBaseUrl = apiBaseUrl.replace(/\/+$/, '');

  const rawStream = String(env.VITE_NOVA_STREAM_PROTOCOL ?? 'none').trim().toLowerCase();
  let streamProtocol: StreamProtocol = 'none';
  if (rawStream === 'sse' || rawStream === 'ndjson' || rawStream === 'none') streamProtocol = rawStream;
  else warnings.push(`未知的 VITE_NOVA_STREAM_PROTOCOL="${rawStream}"，已按 none（普通响应）处理。`);

  return {
    mode,
    apiBaseUrl,
    streamProtocol,
    requestTimeoutMs: toNumber(env.VITE_NOVA_REQUEST_TIMEOUT_MS, 20000, 1000),
    healthPollMs: toNumber(env.VITE_NOVA_HEALTH_POLL_MS, 15000, 3000),
    appVersion,
    warnings,
  };
}

declare const __APP_VERSION__: string;
const version = typeof __APP_VERSION__ === 'string' ? __APP_VERSION__ : '0.0.0';

export const novaConfig: NovaConfig = parseConfig(import.meta.env as EnvLike, version);
