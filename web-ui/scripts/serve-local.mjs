// 本机生产入口。浏览器只访问本站，管理令牌仅保留在进程内。
import { createReadStream } from 'node:fs';
import { realpath, stat } from 'node:fs/promises';
import { createServer, request } from 'node:http';
import { isAbsolute, relative, resolve, sep } from 'node:path';
import { createInterface } from 'node:readline';
import { fileURLToPath, pathToFileURL } from 'node:url';

const LOOPBACK = '127.0.0.1';
const MAX_BODY = 64 * 1024;
const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
const ROUTES = new Map([
  ['/api/health', ['GET']],
  ['/api/v1/models', ['GET']],
  ['/api/v1/threads', ['GET', 'POST']],
  ['/api/v1/chat', ['POST']],
  ['/api/v1/chat/cancel', ['POST']],
]);
const TYPES = new Map([
  ['html', 'text/html; charset=utf-8'],
  ['js', 'text/javascript; charset=utf-8'],
  ['css', 'text/css; charset=utf-8'],
  ['svg', 'image/svg+xml'],
  ['webp', 'image/webp'],
  ['woff2', 'font/woff2'],
]);
const CSP = [
  "default-src 'self'", "connect-src 'self'", "script-src 'self'",
  "style-src 'self' 'unsafe-inline'", "img-src 'self' data:", "base-uri 'none'",
  "object-src 'none'", "frame-ancestors 'none'", "form-action 'self'",
].join('; ');

function error(res, status, code, message, close = false) {
  if (res.destroyed || res.writableEnded) return;
  if (res.headersSent) {
    res.destroy();
    return;
  }
  if (close) res.setHeader('Connection', 'close');
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify({ error: { code, message } }));
}

function upstreamPath(path, method) {
  if (ROUTES.get(path)?.includes(method)) return path === '/api/health' ? '/health' : path;
  const thread = path.match(/^\/api\/v1\/threads\/([^/]+)$/)?.[1];
  if (thread && UUID.test(thread) && ['GET', 'PATCH', 'DELETE'].includes(method)) return path;
  return null;
}

function sameOrigin(req, authority) {
  const site = req.headers['sec-fetch-site'];
  if (site !== undefined && site !== 'same-origin') return false;
  const origin = req.headers.origin;
  if (origin !== undefined) return origin === `http://${authority}`;
  return req.method === 'GET';
}

async function proxy(req, res, path, backendPort, token) {
  const needsBody = req.method === 'POST' || req.method === 'PATCH';
  const hasBody = Number(req.headers['content-length'] || 0) > 0 || req.headers['transfer-encoding'];
  if ((!needsBody && hasBody) || Number(req.headers['content-length']) > MAX_BODY) {
    error(res, needsBody ? 413 : 400, 'NOVA_PROXY_BODY_LIMIT', '请求正文无效或过长', true);
    return;
  }
  let body;
  if (needsBody) {
    if (!/^application\/json(?:\s*;|$)/i.test(req.headers['content-type'] || '')) {
      error(res, 415, 'NOVA_PROXY_JSON_REQUIRED', '只接受 JSON 请求', true);
      return;
    }
    const chunks = [];
    let size = 0;
    for await (const chunk of req.iterator({ destroyOnReturn: false })) {
      size += chunk.length;
      if (size > MAX_BODY) {
        error(res, 413, 'NOVA_PROXY_BODY_LIMIT', '请求正文过长', true);
        return;
      }
      chunks.push(chunk);
    }
    body = Buffer.concat(chunks, size);
  }
  if (res.destroyed || req.aborted) return;

  // 重新构造请求头，浏览器的 Origin、Cookie、认证头及转发头不进入后端。
  const headers = { Accept: path === '/api/v1/chat' ? 'text/event-stream, application/json' : 'application/json' };
  if (path !== '/health') headers.Authorization = `Bearer ${token}`;
  if (body) {
    headers['Content-Type'] = 'application/json';
    headers['Content-Length'] = body.length;
  }
  const upstream = request({ hostname: LOOPBACK, port: backendPort, path, method: req.method, headers });
  res.once('close', () => {
    if (!res.writableFinished) upstream.destroy();
  });
  upstream.setTimeout(310_000, () => {
    error(res, 504, 'NOVA_PROXY_TIMEOUT', 'Nova 后端响应超时');
    upstream.destroy();
  });
  upstream.once('error', () => error(res, 502, 'NOVA_PROXY_OFFLINE', 'Nova 后端暂不可用'));
  upstream.once('response', (incoming) => {
    if (res.destroyed || res.writableEnded) {
      incoming.destroy();
      return;
    }
    const status = incoming.statusCode ?? 502;
    const mime = (incoming.headers['content-type'] || '').split(';')[0].trim().toLowerCase();
    if (status >= 300 && status < 400) {
      error(res, 502, 'NOVA_PROXY_REDIRECT', 'Nova 后端返回了意外的重定向');
      incoming.destroy();
      return;
    }
    if (status === 204) {
      res.writeHead(204);
      res.end();
      incoming.resume();
      return;
    }
    if (mime !== 'application/json' && !(mime === 'text/event-stream' && path === '/api/v1/chat')) {
      error(res, 502, 'NOVA_PROXY_RESPONSE_INVALID', 'Nova 后端返回了无法读取的响应');
      incoming.destroy();
      return;
    }
    // 只转发接口所需的响应头。SSE 从收到头开始直通，不等待完整正文。
    res.writeHead(status, { 'Content-Type': `${mime}; charset=utf-8`, 'X-Accel-Buffering': 'no' });
    res.flushHeaders();
    incoming.once('error', () => res.destroy());
    incoming.pipe(res);
  });
  upstream.end(body);
}

async function serveAsset(req, res, path, dist) {
  if (!['GET', 'HEAD'].includes(req.method)) {
    error(res, 405, 'NOVA_LOCAL_METHOD', '不支持此请求方法');
    return;
  }
  const isAsset = /^\/(?:assets|nova)\/[\w.-]+\.(?:js|css|svg|webp|woff2)$/.test(path)
    || path === '/nova-mark.svg';
  if (!isAsset && path !== '/index.html' && !/^\/(?:[\w-]+\/)*[\w-]*$/.test(path)) {
    error(res, 404, 'NOVA_LOCAL_NOT_FOUND', '未找到该页面或文件');
    return;
  }
  const target = await realpath(resolve(dist, isAsset ? path.slice(1) : 'index.html')).catch(() => null);
  const location = target && relative(dist, target);
  if (!location || isAbsolute(location) || location === '..' || location.startsWith(`..${sep}`)) {
    error(res, 404, 'NOVA_LOCAL_NOT_FOUND', '未找到该页面或文件');
    return;
  }
  const entry = await stat(target).catch(() => null);
  if (!entry?.isFile()) {
    error(res, 404, 'NOVA_LOCAL_NOT_FOUND', '未找到该页面或文件');
    return;
  }
  const extension = (isAsset ? path : 'index.html').split('.').at(-1);
  res.writeHead(200, { 'Content-Type': TYPES.get(extension), 'Content-Length': entry.size });
  if (req.method === 'HEAD') {
    res.end();
    return;
  }
  const stream = createReadStream(target);
  stream.once('error', () => res.destroy());
  res.once('close', () => stream.destroy());
  stream.pipe(res);
}

function validPort(value) {
  return Number.isInteger(value) && value > 0 && value <= 65535;
}

export async function createLocalGateway({ backendPort, token, dist = fileURLToPath(new URL('../dist', import.meta.url)) }) {
  if (!validPort(backendPort) || !/^[A-Za-z0-9_-]{32,256}$/.test(token || '')) {
    throw new Error('本机后端端口或进程令牌未配置');
  }
  const root = await realpath(dist);
  if (!(await stat(resolve(root, 'index.html'))).isFile()) throw new Error('前端尚未构建');
  const server = createServer({ requestTimeout: 30_000 }, async (req, res) => {
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('Content-Security-Policy', CSP);
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('X-Frame-Options', 'DENY');
    res.setHeader('Referrer-Policy', 'no-referrer');
    try {
      const authority = `${LOOPBACK}:${server.address()?.port}`;
      if (req.headersDistinct.host?.length !== 1 || req.headers.host !== authority) {
        error(res, 403, 'NOVA_PROXY_HOST', '请通过本机回环地址访问 Nova', true);
        return;
      }
      if (!req.url?.startsWith('/') || req.url.startsWith('//') || req.url.includes('\\')) {
        error(res, 400, 'NOVA_LOCAL_URL', '请求地址无效', true);
        return;
      }
      const path = req.url.split('?')[0];
      if (path === '/api' || path.startsWith('/api/')) {
        if (!sameOrigin(req, authority)) {
          error(res, 403, 'NOVA_PROXY_ORIGIN', '只接受同源 API 请求', true);
          return;
        }
        const endpoint = upstreamPath(req.url, req.method);
        if (!endpoint) {
          error(res, 404, 'NOVA_PROXY_ROUTE', '该接口未向网页开放', true);
          return;
        }
        await proxy(req, res, endpoint, backendPort, token);
      } else {
        await serveAsset(req, res, path, root);
      }
    } catch {
      error(res, 502, 'NOVA_LOCAL_FAILURE', '本机服务无法完成请求', true);
    }
  });
  return server;
}

async function main() {
  const frontendPort = Number(process.argv[2]);
  const backendPort = Number(process.argv[3]);
  if (!validPort(frontendPort) || frontendPort === backendPort) throw new Error('前后端端口配置无效');
  const server = await createLocalGateway({ backendPort, token: process.env.NOVA_UI_BACKEND_TOKEN });
  server.on('error', () => {
    console.error('本机网页无法监听指定端口');
    process.exitCode = 1;
    input.close();
  });
  const input = createInterface({ input: process.stdin });
  input.on('line', (line) => { if (line.trim() === 'stop') input.close(); });
  input.once('close', () => {
    process.stdin.destroy();
    server.close();
  });
  server.listen(frontendPort, LOOPBACK, () => {
    console.log(`Nova UI: http://${LOOPBACK}:${frontendPort}`);
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch(() => {
    console.error('Nova 网页未启动，请检查构建、端口及进程配置');
    process.exitCode = 1;
  });
}
