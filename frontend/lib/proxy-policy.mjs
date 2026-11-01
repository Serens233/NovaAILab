const methods = {
    models: ["GET"],
    threads: ["GET", "POST"],
    chat: ["POST"],
    "chat/cancel": ["POST"],
    "gpu/status": ["GET"],
    "gpu/mode": ["POST"],
};

export function backendPath(segments, method) {
    if (!Array.isArray(segments) || segments.some((s) => !/^[a-zA-Z0-9_-]{1,128}$/.test(s)))
        return null;
    const path = segments.join("/");
    if (methods[path]?.includes(method))
        return path.startsWith("gpu/") ? `/api/${path}` : `/api/v1/${path}`;
    if (
        segments.length === 2 &&
        segments[0] === "threads" &&
        ["GET", "PATCH", "DELETE"].includes(method)
    )
        return `/api/v1/threads/${segments[1]}`;
    return null;
}

export function safeBackendBase(value) {
    try {
        const url = new URL(value || "http://127.0.0.1:8000");
        if (
            url.protocol !== "http:" ||
            url.hostname !== "127.0.0.1" ||
            url.username ||
            url.password ||
            url.search ||
            url.hash ||
            url.pathname !== "/"
        )
            return null;
        return url.origin;
    } catch {
        return null;
    }
}

export function sameOriginRequest(request, allowedHosts = "localhost,127.0.0.1") {
    const host = request.headers.get("host");
    if (!host || /[\s\\/@]/.test(host)) return false;
    let authority;
    try {
        authority = new URL(`http://${host}`);
    } catch {
        return false;
    }
    if (
        !allowedHosts
            .split(",")
            .map((s) => s.trim().toLowerCase())
            .includes(authority.hostname)
    )
        return false;
    if (["cross-site", "none"].includes(request.headers.get("sec-fetch-site") || "")) return false;
    const origin = request.headers.get("origin");
    if (origin) {
        try {
            const parsed = new URL(origin);
            if (
                !["http:", "https:"].includes(parsed.protocol) ||
                parsed.protocol !== new URL(request.url).protocol ||
                parsed.host.toLowerCase() !== host.toLowerCase() ||
                parsed.origin !== origin
            )
                return false;
        } catch {
            return false;
        }
    }
    // 浏览器修改请求必须携带 Origin；命令行测试也应显式提供同源 Origin。
    if (!["GET", "HEAD"].includes(request.method) && !origin) return false;
    return true;
}
