# 第三方组件与模板来源

Nova 前端使用 Next.js、React 和 assistant-ui 官方组件库。官方 CLI 首次创建因 GitHub 下载超时失败后，本目录按官方 minimal 模板的 Next.js/TypeScript 结构建立，并使用官方 LocalRuntime 与 primitives；没有复制整个仓库，没有保留默认 OpenAI API 后端。

参考版本：assistant-ui 提交 `ff654d8156680b839774296ef644b57ab1078425`，`templates/minimal`、README、LICENSE 与 LocalRuntime/线程/历史适配器源码。官方最新 CLI 会下载 release 模板，不能把 main 源码版本冒充一次成功的 CLI 生成结果。

- 官方仓库：https://github.com/assistant-ui/assistant-ui
- 官方文档：https://www.assistant-ui.com/docs/runtimes/custom/local-runtime
- 包的实际版本及完整依赖由 package-lock.json 固定。npm 依赖自带各自许可证。

assistant-ui 官方 MIT 许可通知保留于 `LICENSE.assistant-ui`。
