import type { ThreadMessageLike } from "@assistant-ui/react";

type HistoryEntry = {
    id: string;
    role: "user" | "assistant";
    content: string;
    created_at: string;
    status: string;
};

/** 后端是唯一持久化来源；加载历史时保留未完成状态，不能把中断伪装成完整回复。 */
export function historyMessage(item: HistoryEntry): ThreadMessageLike {
    const message: ThreadMessageLike = {
        id: item.id,
        role: item.role,
        content: [{ type: "text", text: item.content }],
        createdAt: new Date(item.created_at),
        metadata: { custom: { novaStoredStatus: item.status } },
    };
    if (item.role !== "assistant") return message;
    return {
        ...message,
        status:
            item.status === "complete"
                ? { type: "complete", reason: "stop" }
                : { type: "incomplete", reason: item.status === "error" ? "error" : "other" },
    };
}
