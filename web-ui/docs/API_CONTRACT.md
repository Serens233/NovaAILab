# Nova UI-01 真实接口

浏览器请求同源 `/api`。健康检查由本机网关改写为 `/health`，其余允许路径保持原样。

| 能力 | Claude 草案 | FastAPI 实际路径 | 请求及响应 |
| --- | --- | --- | --- |
| ✅ 健康 | GET /health | GET /health | `{status,database}` |
| ✅ 可用模型 | 未列入 | GET /api/v1/models | `{models,default_model}`，按实际安装和可用状态选择 9B/4B |
| 能力声明 | GET /capabilities | — | 前端按核实的路由配置推断，并标注来源 |
| ✅ 会话列表 | GET /conversations | GET /api/v1/threads | `{threads}` |
| ✅ 创建会话 | POST /conversations | POST /api/v1/threads | `{title?,model?}` → 201，返回 thread |
| ✅ 历史 | GET /conversations/{id}/messages | GET /api/v1/threads/{uuid} | `{thread,messages}` |
| ✅ 重命名 | PATCH /conversations/{id} | PATCH /api/v1/threads/{uuid} | `{title}` → thread |
| ✅ 删除 | DELETE /conversations/{id} | DELETE /api/v1/threads/{uuid} | 204 |
| ✅ 发送及流式 | POST /conversations/{id}/messages[/stream] | POST /api/v1/chat | `thread_id,model,content,request_id,stream,think,cloud_consent`。JSON 为 `{thread_id,message}`，SSE 为 `meta/delta/done/error` |
| ✅ 停止 | POST /conversations/{id}/stop | POST /api/v1/chat/cancel | `{thread_id,request_id}`。同一请求 UUID 在元数据到达前也可取消 |
| 等待接入 | GET /memories、GET /channels | — | 显示暂不可用或状态未知 |

会话 `id`、消息 `id` 和 `thread_id` 映射为前端会话和消息 ID。SSE 的 `done.message` 是已落库的最终回复。没有 `done` 的流按错误处理，取消事件按停止处理。后端 404、离线及失败显示真实错误。

回环网关只转发上表允许的路径和方法，检查 Host 与 Origin，并在服务器端添加临时 Bearer。浏览器不取得令牌。OpenAPI、GPU 管理及其他路由不通过此网关提供。
