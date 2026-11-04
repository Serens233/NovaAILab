# Nova UI-01 · 星图档案馆交互终端

本机新版前端，位于 `web-ui/`，使用 React、TypeScript、Vite 与 Tailwind。与仓库原 Next.js 前端并存。实际联调状态见 [功能矩阵](docs/FEATURE_MATRIX.md)。

## 日常使用

双击 Nova AI Lab 目录中的 **打开 Nova.cmd**，浏览器打开 [Nova](http://127.0.0.1:5173/)。左侧「新的夜航」用于创建会话，输入框发送消息，生成中可点「停止生成」。SQLite 负责保存历史。关掉网页后仍可继续打开。要关闭新版服务时双击 **停止 Nova.cmd**。

首次回复或模型空闲卸载后的首次回复可能需要几十秒。正常聊天选择本机已有的 9B 或 4B 模型。记忆、QQ 和微信页用于显示接入状态。

## 开发与复现

环境要求为 Node ≥20.19 与 Nova 已安装的 Python 3.14 虚拟环境。根目录的 FastAPI 和 GPU Manager 由启动器原样加载。先启动现有 Ollama。

在本目录执行：

```powershell
npm ci --ignore-scripts
Copy-Item .env.example .env.local
npm run typecheck
npm run lint
npm run test
node --test scripts/serve-local.test.mjs
npm run build
```

使用 Nova 虚拟环境中的 Python 调用：

```powershell
python -B scripts/run_local.py --node <node.exe路径> --data-dir <独立数据目录> --open
```

默认前端端口 5173、后端端口 8000，均为 IPv4 回环地址。可用 `--frontend-port` 和 `--backend-port` 在隔离测试目录中改端口。停止时传相同数据目录及 `--stop`。`npm run dev` 用于界面开发，若要离线演示，在启动前把 `VITE_NOVA_API_MODE` 改成 `mock`。

见 [真实接口](docs/API_CONTRACT.md) 和 [部署交接](docs/INTEGRATION_HANDOFF.md)。
