# Nova Web 前端

独立 Next.js + React + TypeScript 项目，使用 assistant-ui 官方 LocalRuntime、ChatModelAdapter、RemoteThreadListAdapter 和 ThreadHistoryAdapter。模型、SQLite 历史及 GPU 调度均由原 FastAPI 处理；前端不直连 Ollama 或任何收费模型。

## Windows 本地启动

需要 Node.js 24 LTS 和已经启动的 Nova FastAPI。首次安装在本目录执行 `npm ci`，之后设置仅服务端环境变量并运行：

```powershell
$env:NOVA_BACKEND_URL = 'http://127.0.0.1:8000'
# 填入与本机 Nova GPU/API 配置一致的管理令牌，不提交到 Git。
$env:NOVA_BACKEND_TOKEN = '<仅本机使用的令牌>'
npm run dev
```

访问 http://127.0.0.1:3000。也可将配置写入已忽略的 `.env.local`；参考 `.env.example`，不要使用 `NEXT_PUBLIC_` 放任何密钥。生产构建检查为 `npm run build`，本地启动构建产物为 `npm start`。

默认仅监听127.0.0.1，适合本机开发。响应式布局支持手机尺寸；物理手机在局域网访问需要另行明确设置监听地址、`NOVA_FRONTEND_ALLOWED_HOSTS`和Windows防火墙，不默认开放网络或声称已完成手机真机验收。

## 行为与边界

- 中文深/浅主题、响应式会话列表、创建/切换/确认删除、9B/4B/DeepSeek选择、流式Markdown、代码块、复制回复、GPU状态和AUTO/GAMING。
- FastAPI流式接口是持久化唯一来源；history adapter只读取现有SQLite历史，append/update不重复写入。首版不开放分支、消息编辑、重新生成、工具自动执行或附件。
- 停止按钮发送独立取消请求，覆盖等待响应头和部分输出阶段；后端需跟踪上游真正结束并复核卸载，不能把按钮停止当作显存已经释放。取消未确认会提示错误；重新打开会话可读取已保存的部分回复。error/interrupted/pending明确显示未完成。
- DeepSeek必须在后端配置且用户明确勾选费用/云端发送同意后才能请求；不会自动从本地切至收费云端。
- GAMING禁用本地发送，但真正门禁始终由FastAPI GPU Manager执行；模式恢复不触发前端预加载。
- 模型未安装或后端不可用时清晰提示，不从浏览器触发模型下载。
- `/api/nova/*`仅同源转发白名单路径/方法；固定本机后端URL、大小限制、无重定向、服务端令牌。它不执行模型或创建新数据库。

## 检查

```powershell
npm test
npm run typecheck
npm run lint
npm run build
```

单元测试覆盖中文/分块SSE、断流/未知协议/取消，以及代理路径、Origin、Host和上游地址限制。实际执行结果由主仓库验收记录保存，不用上述命令清单代替执行结果。
