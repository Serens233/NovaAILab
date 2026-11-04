import assert from 'node:assert/strict';
import { once } from 'node:events';
import { mkdir, mkdtemp, rmdir, unlink, writeFile } from 'node:fs/promises';
import { createServer, request } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { createLocalGateway } from './serve-local.mjs';

const host = '127.0.0.1';
const token = 'synthetic_gateway_test_0123456789_abcdef';
const thread = '/api/v1/threads/11111111-1111-4111-8111-111111111111';

async function listen(server) {
  server.listen(0, host);
  await once(server, 'listening');
  return server.address().port;
}

function open(port, path, { method = 'GET', headers = {}, body } = {}) {
  return new Promise((resolve, reject) => {
    const req = request({ hostname: host, port, path, method, headers }, resolve);
    req.once('error', reject);
    req.end(body);
  });
}

async function read(port, path, options) {
  const response = await open(port, path, options);
  let body = '';
  response.setEncoding('utf8');
  for await (const part of response) body += part;
  return { status: response.statusCode, headers: response.headers, body };
}

test('本机生产网关', { timeout: 15_000 }, async (t) => {
  const dist = await mkdtemp(join(tmpdir(), 'nova-ui01-gateway-'));
  const assets = join(dist, 'assets');
  const index = join(dist, 'index.html');
  const js = join(assets, 'index-test.js');
  await mkdir(assets);
  await writeFile(index, '<title>Nova synthetic page</title>');
  await writeFile(js, 'export {};');
  t.after(async () => {
    await unlink(js);
    await unlink(index);
    await rmdir(assets);
    await rmdir(dist);
  });

  const received = [];
  let finishStream;
  let clientClosed;
  const backend = createServer(async (req, res) => {
    let body = '';
    for await (const part of req) body += part;
    received.push({ path: req.url, method: req.method, headers: req.headers, body });
    if (req.url === '/api/v1/chat') {
      res.writeHead(200, { 'Content-Type': 'text/event-stream; charset=utf-8', 'Set-Cookie': 'synthetic=hidden' });
      res.write('data: {"type":"delta","text":"你好"}\n\n');
      finishStream = () => res.end('data: {"type":"done"}\n\n');
      res.on('close', () => clientClosed?.());
      return;
    }
    if (req.url === '/api/v1/models') {
      res.writeHead(307, { Location: 'https://example.invalid/forbidden' });
      res.end();
      return;
    }
    if (req.method === 'DELETE') {
      res.writeHead(204);
      res.end();
      return;
    }
    res.writeHead(200, { 'Content-Type': 'application/json', 'Set-Cookie': 'synthetic=hidden' });
    res.end('{"status":"ok"}');
  });
  const backendPort = await listen(backend);
  const gateway = await createLocalGateway({ backendPort, token, dist });
  const port = await listen(gateway);
  t.after(async () => {
    gateway.closeAllConnections();
    backend.closeAllConnections();
    await Promise.all([gateway, backend].map((server) => new Promise((resolve) => server.close(resolve))));
  });
  const origin = `http://${host}:${port}`;
  const json = { Origin: origin, 'Content-Type': 'application/json' };

  await t.test('提供 SPA 深链接、构建静态文件和防嵌入头', async () => {
    const page = await read(port, '/chat/11111111-1111-4111-8111-111111111111', { headers: { 'Sec-Fetch-Site': 'none' } });
    assert.equal(page.status, 200);
    assert.match(page.body, /Nova synthetic page/);
    assert.match(page.headers['content-security-policy'], /frame-ancestors 'none'/);
    assert.equal((await read(port, '/assets/index-test.js')).headers['content-type'], 'text/javascript; charset=utf-8');
    assert.equal((await read(port, '/.env.local')).status, 404);
    assert.equal((await read(port, '/assets/%2e%2e/package.json')).status, 404);
  });

  await t.test('健康路由重写，认证及上下游头按允许列表处理', async () => {
    await read(port, '/api/health');
    assert.equal(received.at(-1).path, '/health');
    assert.equal(received.at(-1).headers.authorization, undefined);
    const response = await read(port, thread, {
      headers: { Origin: origin, 'Sec-Fetch-Site': 'same-origin', Authorization: 'untrusted', Cookie: 'untrusted=1' },
    });
    assert.equal(response.status, 200);
    assert.equal(response.headers['set-cookie'], undefined);
    const upstream = received.at(-1);
    assert.equal(upstream.path, thread);
    assert.equal(upstream.headers.authorization === `Bearer ${token}`, true);
    assert.equal(upstream.headers.origin, undefined);
    assert.equal(upstream.headers.cookie, undefined);
    const removed = await read(port, thread, { method: 'DELETE', headers: { Origin: origin } });
    assert.equal(removed.status, 204);
    assert.equal(removed.body, '');
  });

  await t.test('禁止跨站、伪造 Host、非固定路由及后端重定向', async () => {
    const before = received.length;
    for (const [path, options, status] of [
      ['/api/health', { headers: { Host: `localhost:${port}` } }, 403],
      ['/api/health', { headers: { 'Sec-Fetch-Site': 'cross-site' } }, 403],
      ['/api/health', { headers: { Origin: 'null' } }, 403],
      ['/api/v1/threads', { method: 'POST' }, 403],
      ['/api/gpu/wake', { method: 'POST', headers: json }, 404],
      ['/api/v1/threads/not-a-uuid', {}, 404],
      ['/api/v1/models?redirect=1', {}, 404],
    ]) assert.equal((await read(port, path, options)).status, status);
    assert.equal(received.length, before);
    const redirected = await read(port, '/api/v1/models');
    assert.equal(redirected.status, 502);
    assert.equal(redirected.headers.location, undefined);
    assert.equal(JSON.parse(redirected.body).error.code, 'NOVA_PROXY_REDIRECT');
  });

  await t.test('限制正文类型及 Content-Length / chunked 体积', async () => {
    const before = received.length;
    const form = await read(port, '/api/v1/threads', { method: 'POST', headers: { Origin: origin, 'Content-Type': 'text/plain' }, body: 'hello' });
    assert.equal(form.status, 415);
    const tooLarge = 'x'.repeat(70_000);
    for (const lengthHeader of [{ 'Content-Length': tooLarge.length }, { 'Transfer-Encoding': 'chunked' }]) {
      const oversized = await read(port, '/api/v1/threads', { method: 'POST', headers: { ...json, ...lengthHeader }, body: tooLarge });
      assert.equal(oversized.status, 413);
    }
    assert.equal(received.length, before);
  });

  await t.test('SSE 首帧即时可读，浏览器断开会关闭上游', async () => {
    const options = { method: 'POST', headers: json, body: '{"content":"你好"}' };
    const stream = await open(port, '/api/v1/chat', options);
    assert.match(stream.headers['content-type'], /^text\/event-stream/);
    assert.equal(stream.headers['set-cookie'], undefined);
    const parts = stream.iterator({ destroyOnReturn: false });
    assert.match((await parts.next()).value.toString(), /你好/);
    assert.equal(received.at(-1).body, options.body);
    finishStream();
    assert.match((await parts.next()).value.toString(), /done/);
    assert.equal((await parts.next()).done, true);

    const interrupted = await open(port, '/api/v1/chat', options);
    const closed = new Promise((resolve) => { clientClosed = resolve; });
    interrupted.destroy();
    await closed;
  });

  await t.test('后端离线返回可识别错误', async () => {
    await new Promise((resolve) => backend.close(resolve));
    const offline = await read(port, '/api/health');
    assert.equal(offline.status, 502);
    assert.equal(JSON.parse(offline.body).error.code, 'NOVA_PROXY_OFFLINE');
  });
});
