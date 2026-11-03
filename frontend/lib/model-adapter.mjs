import { readNovaEvents } from "./stream.mjs";

/**
 * settingsRef 由 NovaRuntime 持有；远程线程宿主即使保留首次 hook，也读取同一个 ref。
 * @param {{current:{model:string,confirmCloud:()=>boolean,onError:(message:string|null)=>void}}} settingsRef
 * @param {{initialize:()=>Promise<{remoteId:string}>,send:(body:{thread_id:string,model:string,content:string,request_id:string,cloud_consent:boolean},signal:AbortSignal)=>Promise<Response>,cancel:(body:{thread_id:string,request_id:string})=>Promise<unknown>,reload:()=>void|Promise<void>}} transport
 * @returns {import("@assistant-ui/react").ChatModelAdapter}
 */
export function createNovaModelAdapter(settingsRef, transport) {
    return {
        async *run({ messages, abortSignal }) {
            // 显式选择在点击发送时固定；等待创建会话时的后续选择只影响下一次请求。
            const selection = { ...settingsRef.current };
            selection.onError(null);
            const input = [...messages].reverse().find((message) => message.role === "user");
            if (!input) throw new Error("请输入消息后再发送。");
            let sent = false;
            let completed = false;
            let cancelRequested = false;
            let onAbort;
            const checkAbort = () => {
                if (abortSignal.aborted) throw new DOMException("已停止生成。", "AbortError");
            };
            try {
                checkAbort();
                const cloudConsent = selection.model === "deepseek" && selection.confirmCloud();
                if (selection.model === "deepseek" && !cloudConsent)
                    throw new Error("本次云端发送未获同意。");
                const { remoteId } = await transport.initialize();
                checkAbort();
                const body = {
                    thread_id: remoteId,
                    model: selection.model,
                    content: input.content
                        .filter((part) => part.type === "text")
                        .map((part) => part.text)
                        .join("\n"),
                    request_id: crypto.randomUUID(),
                    cloud_consent: cloudConsent,
                };
                onAbort = () => {
                    if (!sent || completed || cancelRequested) return;
                    cancelRequested = true;
                    // 断开原 fetch 只会停止读取；独立控制请求在 prepare/等待响应头时也能取消任务。
                    // 后端按 request_id 处理先于 chat 到达的取消，不能复用已中止的 signal。
                    void Promise.resolve()
                        .then(() =>
                            transport.cancel({
                                thread_id: body.thread_id,
                                request_id: body.request_id,
                            }),
                        )
                        .catch(() => {
                            settingsRef.current.onError(
                                "停止请求未获服务器确认，任务可能仍在结束中；请检查 GPU 状态。",
                            );
                        });
                };
                abortSignal.addEventListener("abort", onAbort, { once: true });
                checkAbort();
                sent = true;
                const response = await transport.send(body, abortSignal);
                checkAbort();
                let text = "";
                for await (const event of readNovaEvents(response.body)) {
                    checkAbort();
                    if (event.type === "error")
                        throw new Error(event.message || "回复失败，请重试。");
                    if (event.type === "delta") {
                        if (typeof event.text !== "string") throw new Error("消息流格式不正确。");
                        text += event.text;
                        yield { content: [{ type: "text", text }] };
                    }
                    if (event.type === "done") {
                        completed = true;
                        abortSignal.removeEventListener("abort", onAbort);
                        if (typeof event.message?.content === "string") {
                            text = event.message.content;
                            yield { content: [{ type: "text", text }] };
                        }
                    }
                }
                void transport.reload();
            } catch (error) {
                if (!(error instanceof Error && error.name === "AbortError")) {
                    settingsRef.current.onError(
                        error instanceof Error ? error.message : "回复失败，请稍后重试。",
                    );
                }
                throw error;
            } finally {
                if (onAbort) abortSignal.removeEventListener("abort", onAbort);
            }
        },
    };
}
