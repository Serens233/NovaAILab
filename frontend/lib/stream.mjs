/** Nova 的 SSE 与 AI SDK 协议不同；此处显式解析，不依赖网络块边界。 */
export async function* readNovaEvents(body) {
    if (!body) throw new Error("服务器没有返回消息流，请稍后重试。");
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let data = [];
    let finished = false;
    function parseEvent() {
        if (!data.length) return null;
        const raw = data.join("\n");
        data = [];
        let event;
        try {
            event = JSON.parse(raw);
        } catch {
            throw new Error("服务器返回了无法解析的消息流。");
        }
        if (!event || !["meta", "delta", "done", "error"].includes(event.type))
            throw new Error("服务器返回了未知消息事件。");
        return event;
    }
    function consumeLine(line) {
        if (line === "") return parseEvent();
        if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
        return null;
    }
    try {
        while (true) {
            const chunk = await reader.read();
            buffer += decoder.decode(chunk.value, { stream: !chunk.done });
            if (buffer.length > 2_000_000) throw new Error("消息流单个事件过大。");
            while (true) {
                const end = buffer.search(/[\r\n]/);
                if (end < 0 || (buffer[end] === "\r" && end === buffer.length - 1 && !chunk.done))
                    break;
                const line = buffer.slice(0, end);
                const size = buffer[end] === "\r" && buffer[end + 1] === "\n" ? 2 : 1;
                buffer = buffer.slice(end + size);
                const event = consumeLine(line);
                if (event) {
                    if (event.type === "done") finished = true;
                    yield event;
                }
            }
            if (chunk.done) {
                if (buffer) {
                    const event = consumeLine(buffer);
                    if (event) yield event;
                }
                const event = parseEvent();
                if (event) {
                    if (event.type === "done") finished = true;
                    yield event;
                }
                if (!finished) throw new Error("回复连接提前结束；已保存的对话可以重新打开查看。");
                return;
            }
        }
    } finally {
        await reader.cancel().catch(() => {});
        reader.releaseLock();
    }
}
