export type Model = {
    id: string;
    label: string;
    provider: "ollama" | "deepseek";
    available: boolean;
    installed?: boolean;
    reason?: string;
    requires_cloud_consent?: boolean;
};
export type Thread = {
    id: string;
    title: string;
    model: string;
    created_at: string;
    updated_at: string;
};
export type StoredMessage = {
    id: string;
    thread_id: string;
    role: "user" | "assistant";
    content: string;
    model: string;
    created_at: string;
    status: string;
    position: number;
};

export class NovaError extends Error {
    constructor(
        message: string,
        public code = "NOVA_ERROR",
        public status = 0,
    ) {
        super(message);
        this.name = "NovaError";
    }
}
export async function request(path: string, init?: RequestInit): Promise<Response> {
    let response;
    try {
        response = await fetch(`/api/nova/${path}`, {
            ...init,
            cache: "no-store",
            headers: { "Content-Type": "application/json", ...init?.headers },
        });
    } catch (error) {
        if (error instanceof Error && error.name === "AbortError") throw error;
        throw new NovaError("连接中断，请检查 Nova 是否已启动。", "NETWORK_ERROR");
    }
    if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new NovaError(
            body?.error?.message || `请求失败（${response.status}），请稍后重试。`,
            body?.error?.code,
            response.status,
        );
    }
    return response;
}
export async function json<T>(path: string, init?: RequestInit): Promise<T> {
    return (await request(path, init)).json();
}

export function messageText(message: { content: readonly { type: string; text?: string }[] }) {
    return message.content
        .filter((part) => part.type === "text")
        .map((part) => part.text || "")
        .join("\n");
}
