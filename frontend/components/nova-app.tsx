"use client";

import {
    AuiIf,
    ComposerPrimitive,
    MessagePrimitive,
    ThreadPrimitive,
    ThreadListPrimitive,
    ThreadListItemPrimitive,
    useAui,
    useAuiState,
    ErrorPrimitive,
} from "@assistant-ui/react";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import {
    Activity,
    ArrowDown,
    ArrowUp,
    Check,
    ChevronDown,
    Cloud,
    Code2,
    Copy,
    Gamepad2,
    HardDrive,
    Leaf,
    Menu,
    MessageSquare,
    Moon,
    PanelLeftClose,
    Plus,
    ShieldCheck,
    Sparkles,
    Square,
    Sun,
    Trash2,
    X,
    Zap,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import remarkGfm from "remark-gfm";
import { json, request, type Model } from "@/lib/api";
import { NovaRuntime, useNovaSettings } from "@/components/nova-runtime";

type Gpu = {
    mode: string;
    mode_source?: string;
    local_requests_blocked: boolean;
    backend: { available: boolean; models: { name?: string; model?: string }[] };
    gpu_memory: {
        available: boolean;
        gpus?: { total_mib: number; used_mib: number; name: string }[];
    };
    game_detection?: { state?: string };
    fault?: string | null;
};
const initialModel = "qwen3.5:9b";

function NovaMark({ small = false }: { small?: boolean }) {
    return (
        <span className={`nova-mark ${small ? "small" : ""}`} aria-hidden="true">
            <Sparkles size={small ? 17 : 23} strokeWidth={1.7} />
        </span>
    );
}

export function NovaApp() {
    const [models, setModels] = useState<Model[]>([]);
    const [model, setModel] = useState(initialModel);
    const [error, setError] = useState<string | null>(null);
    const [cloudConsent, setCloudConsent] = useState(false);
    const [theme, setTheme] = useState("dark");
    const [mobileOpen, setMobileOpen] = useState(false);
    const [gpu, setGpu] = useState<Gpu | null>(null);
    const [gpuError, setGpuError] = useState<string | null>(null);
    const [connecting, setConnecting] = useState(true);
    const refreshGpu = useCallback(async () => {
        try {
            setGpu(await json<Gpu>("gpu/status"));
            setGpuError(null);
        } catch (failure) {
            setGpuError(failure instanceof Error ? failure.message : "GPU 状态暂不可用");
        }
    }, []);
    useEffect(() => {
        const saved = localStorage.getItem("nova-theme");
        if (saved === "light" || saved === "dark") setTheme(saved);
        json<{ models: Model[]; default_model: string }>("models")
            .then((data) => {
                setModels(data.models);
                setConnecting(false);
            })
            .catch((failure) => {
                setError(failure.message);
                setConnecting(false);
            });
        void refreshGpu();
        const timer = setInterval(() => {
            void refreshGpu();
        }, 10_000);
        return () => clearInterval(timer);
    }, [refreshGpu]);
    useEffect(() => {
        document.documentElement.dataset.theme = theme;
        localStorage.setItem("nova-theme", theme);
    }, [theme]);
    useEffect(() => {
        if (!mobileOpen) return;
        const onKey = (event: KeyboardEvent) => {
            if (event.key === "Escape") setMobileOpen(false);
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [mobileOpen]);
    const selected = models.find((item) => item.id === model);
    const changeModel = useCallback((id: string) => {
        setModel(id);
        setCloudConsent(false);
        setError(null);
    }, []);
    return (
        <NovaRuntime
            model={model}
            cloudConsent={cloudConsent}
            onError={setError}
            onModel={changeModel}
        >
            <div className="app-shell">
                {mobileOpen && (
                    <button
                        className="mobile-overlay"
                        aria-label="关闭会话列表"
                        onClick={() => setMobileOpen(false)}
                    />
                )}
                <aside
                    className={`sidebar ${mobileOpen ? "open" : ""}`}
                    aria-label="会话与系统状态"
                >
                    <div className="brand">
                        <NovaMark />
                        <div>
                            <strong>
                                Nova<span className="brand-dot">.</span>
                            </strong>
                            <span>你的 AI 工作空间</span>
                        </div>
                        <button
                            className="icon-button sidebar-close"
                            aria-label="关闭侧边栏"
                            onClick={() => setMobileOpen(false)}
                        >
                            <PanelLeftClose size={18} />
                        </button>
                    </div>
                    <ThreadListPrimitive.New
                        className="new-chat"
                        onClick={() => {
                            setMobileOpen(false);
                            setError(null);
                        }}
                    >
                        <Plus size={18} />
                        <span>开启新对话</span>
                        <kbd>＋</kbd>
                    </ThreadListPrimitive.New>
                    <div className="section-label">
                        <span>最近的对话</span>
                        <MessageSquare size={12} />
                    </div>
                    <ThreadListPrimitive.Root className="thread-list">
                        <ThreadListPrimitive.Items>
                            {() => (
                                <ThreadItem
                                    onSelect={() => {
                                        setMobileOpen(false);
                                        setError(null);
                                    }}
                                />
                            )}
                        </ThreadListPrimitive.Items>
                        <ThreadListEmpty />
                    </ThreadListPrimitive.Root>
                    <GpuCard gpu={gpu} error={gpuError} refresh={refreshGpu} onError={setError} />
                    <div className="sidebar-bottom">
                        <span className="avatar">我</span>
                        <div>
                            <strong>本地工作空间</strong>
                            <span>思考与创造，从这里开始</span>
                        </div>
                        <button
                            className="icon-button"
                            aria-label={theme === "dark" ? "切换浅色主题" : "切换深色主题"}
                            title={theme === "dark" ? "浅色模式" : "深色模式"}
                            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
                        >
                            {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
                        </button>
                    </div>
                </aside>
                <main className="main-panel">
                    <header className="topbar">
                        <div className="topbar-left">
                            <button
                                className="icon-button mobile-menu"
                                aria-label="打开会话列表"
                                onClick={() => setMobileOpen(true)}
                            >
                                <Menu size={20} />
                            </button>
                            <ModelPicker
                                models={models}
                                selected={model}
                                onChange={changeModel}
                                loading={connecting}
                            />
                            <span className="topbar-divider" />
                            <span className="workspace-note">陪伴 · 成长 · 助手</span>
                        </div>
                        <span
                            className={`connection-badge ${gpu?.local_requests_blocked ? "gaming" : ""}`}
                        >
                            <span className="status-dot" />
                            {gpu?.local_requests_blocked
                                ? "游戏保护中"
                                : connecting
                                  ? "连接中"
                                  : "本地优先"}
                        </span>
                    </header>
                    {error && (
                        <div className="error-banner" role="alert">
                            <ShieldCheck size={17} />
                            <span>{error}</span>
                            <button
                                className="icon-button"
                                aria-label="关闭错误提示"
                                onClick={() => setError(null)}
                            >
                                <X size={15} />
                            </button>
                        </div>
                    )}
                    {selected?.provider === "deepseek" && (
                        <div className="cloud-notice">
                            <Cloud size={16} />
                            <span>DeepSeek 会将本次对话发送至云端，并可能产生 API 费用。</span>
                            <label>
                                <input
                                    type="checkbox"
                                    checked={cloudConsent}
                                    onChange={(event) => setCloudConsent(event.target.checked)}
                                />
                                我同意发送并使用收费 API
                            </label>
                        </div>
                    )}
                    <ChatThread
                        model={selected}
                        blocked={Boolean(
                            gpu?.local_requests_blocked && selected?.provider !== "deepseek",
                        )}
                    />
                    <div className="page-footer">
                        <span className="footer-orbit" />
                        Nova 可能会出错，请核实重要信息。
                        <span className="footer-local">
                            <HardDrive size={11} />
                            对话保存在本机
                        </span>
                    </div>
                </main>
            </div>
        </NovaRuntime>
    );
}

function ThreadListEmpty() {
    const count = useAuiState((s) => s.threads.threadIds.length);
    const loading = useAuiState((s) => s.threads.isLoading);
    return count === 0 ? (
        <p className="list-empty">
            {loading ? "正在读取对话…" : "还没有对话\n每个好想法都值得被记住"}
        </p>
    ) : null;
}

function ThreadItem({ onSelect }: { onSelect: () => void }) {
    const aui = useAui();
    const settings = useNovaSettings();
    const title = useAuiState((s) => s.threadListItem.title || "新对话");
    const [deleting, setDeleting] = useState(false);
    return (
        <ThreadListItemPrimitive.Root className="thread-item">
            <ThreadListItemPrimitive.Trigger
                className="thread-trigger"
                onClick={() => {
                    const model = aui.threadListItem.getState().custom?.model;
                    if (typeof model === "string") settings.onModel(model);
                    onSelect();
                }}
            >
                <MessageSquare size={15} />
                <span>{title}</span>
            </ThreadListItemPrimitive.Trigger>
            <button
                className="thread-delete icon-button"
                disabled={deleting}
                aria-label={`删除对话：${title}`}
                title="删除对话"
                onClick={async () => {
                    if (!window.confirm(`删除“${title}”？此操作会删除这段聊天历史。`)) return;
                    setDeleting(true);
                    try {
                        await aui.threadListItem.delete();
                    } catch (error) {
                        settings.onError(
                            error instanceof Error ? error.message : "删除失败，请重试。",
                        );
                    } finally {
                        setDeleting(false);
                    }
                }}
            >
                <Trash2 size={14} />
            </button>
        </ThreadListItemPrimitive.Root>
    );
}

function ModelPicker({
    models,
    selected,
    onChange,
    loading,
}: {
    models: Model[];
    selected: string;
    onChange: (id: string) => void;
    loading: boolean;
}) {
    return (
        <label className="model-picker">
            <span className="model-glyph">
                <Sparkles size={15} />
            </span>
            <select
                aria-label="选择聊天模型"
                value={selected}
                onChange={(event) => onChange(event.target.value)}
                disabled={loading}
            >
                {models.length ? (
                    models.map((model) => (
                        <option key={model.id} value={model.id} disabled={!model.available}>
                            {model.label}
                            {!model.available ? ` · ${model.reason || "不可用"}` : ""}
                        </option>
                    ))
                ) : (
                    <option value={initialModel}>Qwen3.5 · 9B</option>
                )}
            </select>
            <ChevronDown size={13} />
        </label>
    );
}

function ChatThread({ model, blocked }: { model?: Model; blocked: boolean }) {
    const settings = useNovaSettings();
    const canSend =
        model?.available && !blocked && (model.provider !== "deepseek" || settings.cloudConsent);
    return (
        <ThreadPrimitive.Root className="chat-thread">
            <ThreadPrimitive.Viewport className="chat-viewport">
                <div className="conversation">
                    <AuiIf condition={(s) => s.thread.messages.length === 0 && !s.thread.isLoading}>
                        <Welcome />
                    </AuiIf>
                    <AuiIf condition={(s) => s.thread.isLoading}>
                        <div className="history-loading">正在读取本机会话…</div>
                    </AuiIf>
                    <ThreadPrimitive.Messages>{() => <ChatMessage />}</ThreadPrimitive.Messages>
                </div>
            </ThreadPrimitive.Viewport>
            <div className="composer-area">
                <ThreadPrimitive.ScrollToBottom
                    className="scroll-bottom icon-button"
                    aria-label="滚动到最新消息"
                >
                    <ArrowDown size={16} />
                </ThreadPrimitive.ScrollToBottom>
                <ComposerPrimitive.Root className="composer">
                    <ComposerPrimitive.Input
                        className="composer-input"
                        aria-label="发送给 Nova 的消息"
                        placeholder={
                            blocked
                                ? "游戏保护中，本地模型暂时休息。"
                                : "给 Nova 发消息，聊聊你的想法…"
                        }
                        rows={2}
                        disabled={!canSend}
                    />
                    <div className="composer-bottom">
                        <span className="composer-hint">
                            <span className="tiny-star">✦</span>
                            {blocked
                                ? "退出游戏后按需恢复"
                                : model?.provider === "deepseek"
                                  ? "云端推理"
                                  : "本地推理 · 只在需要时加载"}
                        </span>
                        <div className="composer-controls">
                            <span className="keyboard-hint">Enter 发送 · Shift + Enter 换行</span>
                            <AuiIf condition={(s) => !s.thread.isRunning}>
                                <ComposerPrimitive.Send
                                    className="send-button"
                                    aria-label="发送消息"
                                    disabled={!canSend}
                                >
                                    <ArrowUp size={19} />
                                </ComposerPrimitive.Send>
                            </AuiIf>
                            <AuiIf condition={(s) => s.thread.isRunning}>
                                <ComposerPrimitive.Cancel
                                    className="send-button cancel"
                                    aria-label="停止显示回复"
                                    title="只停止前端接收；后台会安全完成当前推理并保存历史，不会立即停止 GPU"
                                    onClick={() =>
                                        settings.onError(
                                            "已停止接收回复。后台会安全完成当前推理并保存历史；稍后重新打开此对话可查看结果。",
                                        )
                                    }
                                >
                                    <Square size={13} fill="currentColor" />
                                </ComposerPrimitive.Cancel>
                            </AuiIf>
                        </div>
                    </div>
                </ComposerPrimitive.Root>
            </div>
        </ThreadPrimitive.Root>
    );
}

function Welcome() {
    return (
        <div className="welcome">
            <div className="welcome-emblem">
                <NovaMark />
                <span className="emblem-dot" />
            </div>
            <div className="eyebrow">很高兴见到你</div>
            <h1>
                给想法一点空间<span>，</span>
                <br />
                让可能慢慢发生<span>。</span>
            </h1>
            <p className="welcome-description">我是 Nova，陪你思考、学习，也一起把事情做好。</p>
            <div className="prompt-grid">
                {[
                    {
                        icon: <Sparkles size={18} />,
                        label: "理清思路",
                        text: "帮我把一个模糊的想法变成可执行的计划。",
                    },
                    {
                        icon: <Code2 size={18} />,
                        label: "一起写代码",
                        text: "我是 Python 初学者，请带我理解一个实用的小例子。",
                    },
                    {
                        icon: <Leaf size={18} />,
                        label: "学点新东西",
                        text: "帮我制定一个循序渐进的 AI 学习计划。",
                    },
                    {
                        icon: <MessageSquare size={18} />,
                        label: "随便聊聊",
                        text: "今天想和你随便聊聊。",
                    },
                ].map((item) => (
                    <ThreadPrimitive.Suggestion
                        key={item.label}
                        prompt={item.text}
                        method="replace"
                        autoSend={false}
                        className="prompt-card"
                    >
                        <span className="prompt-icon">{item.icon}</span>
                        <strong>{item.label}</strong>
                        <span className="prompt-arrow">↗</span>
                    </ThreadPrimitive.Suggestion>
                ))}
            </div>
            <span className="welcome-footnote">
                <ShieldCheck size={12} />
                你的对话留在这台电脑，云端模型由你选择。
            </span>
        </div>
    );
}

function ChatMessage() {
    const role = useAuiState((s) => s.message.role);
    const running = useAuiState((s) => s.message.status?.type === "running");
    const incomplete = useAuiState((s) => s.message.status?.type === "incomplete");
    const storedStatus = useAuiState((s) => s.message.metadata.custom?.novaStoredStatus);
    const content = useAuiState((s) =>
        s.message.content
            .filter((p) => p.type === "text")
            .map((p) => p.text)
            .join("\n"),
    );
    const [copied, setCopied] = useState(false);
    return (
        <MessagePrimitive.Root
            className={`message ${role === "user" ? "user-message" : "assistant-message"}`}
        >
            {role !== "user" && (
                <div className="message-avatar">
                    <NovaMark small />
                </div>
            )}
            <div className="message-body">
                {role !== "user" && (
                    <div className="message-label">
                        Nova <span>{running ? "正在思考与回复" : ""}</span>
                    </div>
                )}
                <MessagePrimitive.Parts
                    components={{
                        Text: () => (
                            <MarkdownTextPrimitive
                                className="markdown"
                                remarkPlugins={[remarkGfm]}
                            />
                        ),
                    }}
                />
                {role !== "user" && !content && running && (
                    <span className="thinking-dots">
                        <i />
                        <i />
                        <i />
                    </span>
                )}
                <MessagePrimitive.Error>
                    <ErrorPrimitive.Root className="message-error">
                        <ErrorPrimitive.Message />
                    </ErrorPrimitive.Root>
                </MessagePrimitive.Error>
                {role !== "user" && incomplete && (
                    <p className="incomplete-notice">
                        {storedStatus === "pending"
                            ? "后台任务可能仍在完成；稍后重新打开此对话查看已保存结果。"
                            : storedStatus === "interrupted"
                              ? "这次回复曾中断；已保留当前内容，未标记为完整回复。"
                              : storedStatus === "error"
                                ? "这次回复未完成；已保留当前内容，请检查错误提示。"
                                : "已停止显示或连接中断；后台可能继续完成并保存，请稍后重新打开对话。"}
                    </p>
                )}
                {role !== "user" && content && !running && (
                    <button
                        className="copy-button"
                        aria-label="复制回复"
                        onClick={async () => {
                            try {
                                await navigator.clipboard.writeText(content);
                                setCopied(true);
                                setTimeout(() => setCopied(false), 1600);
                            } catch {
                                setCopied(false);
                            }
                        }}
                    >
                        {copied ? <Check size={13} /> : <Copy size={13} />}
                        <span>{copied ? "已复制" : "复制"}</span>
                    </button>
                )}
            </div>
        </MessagePrimitive.Root>
    );
}

function GpuCard({
    gpu,
    error,
    refresh,
    onError,
}: {
    gpu: Gpu | null;
    error: string | null;
    refresh: () => Promise<void>;
    onError: (error: string | null) => void;
}) {
    const [busy, setBusy] = useState(false);
    const device = gpu?.gpu_memory?.gpus?.[0];
    const gaming = gpu?.mode === "GAMING";
    const used = device ? (device.used_mib / 1024).toFixed(1) : "—";
    const total = device ? (device.total_mib / 1024).toFixed(1) : "—";
    return (
        <div className="gpu-card">
            <div className="gpu-card-title">
                <span>
                    <Activity size={14} />
                    GPU 工作状态
                </span>
                <span className={`mode-pill ${gaming ? "gaming" : ""}`}>
                    {gpu ? (gaming ? "游戏保护" : "AUTO") : "未连接"}
                </span>
            </div>
            <div className="memory-row">
                <span>独立显存</span>
                <strong>
                    {used}
                    <small> / {total} GB</small>
                </strong>
            </div>
            <div className="memory-track">
                <span
                    style={{
                        width: device
                            ? `${Math.min(100, (device.used_mib / device.total_mib) * 100)}%`
                            : "0%",
                    }}
                />
            </div>
            <p className="gpu-card-note">
                {error
                    ? "状态暂不可用，请检查后端连接"
                    : gaming
                      ? "本地模型已锁定，游戏优先"
                      : gpu?.backend?.models?.length
                        ? "本地模型正在工作"
                        : "模型未驻留，显存留给需要的任务"}
            </p>
            <button
                className="gpu-mode-button"
                disabled={!gpu || busy}
                title={error || undefined}
                onClick={async () => {
                    if (
                        !gaming &&
                        !window.confirm(
                            "进入游戏模式后将安全卸载本地模型，并阻止新的本地请求。继续？",
                        )
                    )
                        return;
                    setBusy(true);
                    try {
                        await request("gpu/mode", {
                            method: "POST",
                            body: JSON.stringify({ mode: gaming ? "AUTO" : "GAMING" }),
                        });
                        await refresh();
                    } catch (failure) {
                        onError(failure instanceof Error ? failure.message : "模式切换失败。");
                    } finally {
                        setBusy(false);
                    }
                }}
            >
                {gaming ? <Zap size={13} /> : <Gamepad2 size={13} />}
                <span>{busy ? "正在切换…" : gaming ? "恢复智能模式" : "进入游戏模式"}</span>
            </button>
        </div>
    );
}
