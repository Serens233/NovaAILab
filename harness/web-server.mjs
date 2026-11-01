// Next.js仅提供UI及同源代理；模型执行仍由FastAPI拥有。
import http from "node:http";
import { createRequire } from "node:module";
import path from "node:path";

const [folder, portText, mode] = process.argv.slice(2);
const require = createRequire(path.join(folder, "package.json"));
const next = require("next");
const app = next({ dir: folder, dev: mode === "dev", hostname: "127.0.0.1", port: Number(portText) });
await app.prepare();
const handler = app.getRequestHandler();
const server = http.createServer((request, response) => handler(request, response));
await new Promise((resolve, reject) => {
  server.once("error", reject);
  server.listen(Number(portText), "127.0.0.1", resolve);
});
console.log(`Nova Web ready at http://127.0.0.1:${portText}`);
let stopping = false;
async function stop() {
  if (stopping) return;
  stopping = true;
  await new Promise((resolve) => server.close(resolve));
  await app.close();
  process.stdin.pause();
  process.exitCode = 0;
}
process.stdin.on("data", () => void stop());
process.stdin.resume();
process.on("SIGINT", () => void stop());
process.on("SIGTERM", () => void stop());
