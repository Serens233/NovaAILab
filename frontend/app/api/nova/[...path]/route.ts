import { backendPath, safeBackendBase, sameOriginRequest } from "@/lib/proxy-policy.mjs";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function failure(message: string, status: number, code = "NOVA_PROXY_ERROR") {
    return Response.json(
        { error: { code, message } },
        { status, headers: { "Cache-Control": "no-store" } },
    );
}

async function forward(request: Request, context: { params: Promise<{ path: string[] }> }) {
    if (!sameOriginRequest(request, process.env.NOVA_FRONTEND_ALLOWED_HOSTS))
        return failure("请求来源不受信任。请从 Nova 本机页面访问。", 403);
    const path = backendPath((await context.params).path, request.method);
    if (!path) return failure("该操作未开放。", 404);
    const base = safeBackendBase(process.env.NOVA_BACKEND_URL);
    const token = process.env.NOVA_BACKEND_TOKEN;
    if (!base || !token) return failure("尚未连接 Nova 后端，请检查本机启动配置。", 503);
    let body: Uint8Array<ArrayBuffer> | undefined;
    if (!["GET", "HEAD"].includes(request.method)) {
        if (
            !request.headers.get("content-type")?.startsWith("application/json") &&
            request.method !== "DELETE"
        )
            return failure("请使用 JSON 请求。", 415);
        const reader = request.body?.getReader();
        const chunks: Uint8Array[] = [];
        let size = 0;
        if (reader) {
            try {
                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;
                    size += value.byteLength;
                    if (size > 65_536) {
                        await reader.cancel();
                        return failure("输入过长，请缩短后重试。", 413);
                    }
                    chunks.push(value);
                }
            } finally {
                reader.releaseLock();
            }
        }
        if (size) {
            body = new Uint8Array(size);
            let cursor = 0;
            for (const chunk of chunks) {
                body.set(chunk, cursor);
                cursor += chunk.length;
            }
        }
    }
    try {
        const upstream = await fetch(base + path, {
            method: request.method,
            headers: {
                Authorization: `Bearer ${token}`,
                "Content-Type": "application/json",
                Accept: "application/json, text/event-stream",
            },
            body,
            cache: "no-store",
            redirect: "error",
            signal: request.signal,
        });
        return new Response(upstream.body, {
            status: upstream.status,
            headers: {
                "Content-Type": upstream.headers.get("content-type") || "application/json",
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            },
        });
    } catch {
        return failure("无法连接 Nova 后端。请确认后端已经启动。", 502);
    }
}

export { forward as GET, forward as POST, forward as PATCH, forward as DELETE };
