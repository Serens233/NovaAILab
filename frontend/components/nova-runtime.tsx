"use client";

import {
    AssistantRuntimeProvider,
    ExportedMessageRepository,
    useAui,
    useLocalRuntime,
    useRemoteThreadListRuntime,
    type ChatModelAdapter,
    type RemoteThreadListAdapter,
    type ThreadHistoryAdapter,
} from "@assistant-ui/react";
import { createAssistantStream } from "assistant-stream";
import { createContext, useContext, useMemo, useRef, type ReactNode } from "react";
import { json, NovaError, request, type StoredMessage, type Thread } from "@/lib/api";
import { createNovaModelAdapter } from "@/lib/model-adapter.mjs";
import { historyMessage } from "@/lib/history";

type Settings = {
    model: string;
    confirmCloud: () => boolean;
    onError: (error: string | null) => void;
    onModel: (model: string) => void;
};
type SettingsRef = { current: Settings };
const NovaSettings = createContext<Settings | null>(null);
export function useNovaSettings() {
    const value = useContext(NovaSettings);
    if (!value) throw new Error("Nova settings unavailable");
    return value;
}

function useHistoryAdapters() {
    const aui = useAui();
    return useMemo(() => {
        const history: ThreadHistoryAdapter = {
            async load() {
                const { remoteId } = aui.threadListItem.getState();
                if (!remoteId) return { messages: [] };
                const data = await json<{ thread: Thread; messages: StoredMessage[] }>(
                    `threads/${remoteId}`,
                );
                return ExportedMessageRepository.fromArray(data.messages.map(historyMessage));
            },
            // FastAPI 在流式请求中保存 user/assistant；此处再次 append 会制造重复历史。
            async append() {},
            async update() {},
        };
        return { history };
    }, [aui]);
}

function useNovaThread(settingsRef: SettingsRef) {
    const aui = useAui();
    const adapter = useMemo<ChatModelAdapter>(
        () =>
            createNovaModelAdapter(settingsRef, {
                initialize: () => aui.threadListItem.initialize(),
                send: (body, signal) =>
                    request("chat", {
                        method: "POST",
                        body: JSON.stringify(body),
                        signal,
                    }),
                cancel: (body) =>
                    request("chat/cancel", {
                        method: "POST",
                        body: JSON.stringify(body),
                        signal: AbortSignal.timeout(10_000),
                    }),
                reload: () => aui.threads.reload(),
            }),
        [aui, settingsRef],
    );
    return useLocalRuntime(adapter);
}

function metadata(thread: Thread) {
    return {
        remoteId: thread.id,
        title: thread.title || "新对话",
        status: "regular" as const,
        lastMessageAt: new Date(thread.updated_at),
        custom: { model: thread.model },
    };
}

export function NovaRuntime({
    children,
    model,
    confirmCloud,
    onError,
    onModel,
}: Settings & { children: ReactNode }) {
    const settings = useMemo(
        () => ({ model, confirmCloud, onError, onModel }),
        [model, confirmCloud, onError, onModel],
    );
    const current = useRef(settings);
    current.current = settings;
    const adapter = useMemo<RemoteThreadListAdapter>(
        () => ({
            async list() {
                return {
                    threads: (await json<{ threads: Thread[] }>("threads")).threads.map(metadata),
                };
            },
            async initialize() {
                return {
                    remoteId: (
                        await json<Thread>("threads", {
                            method: "POST",
                            body: JSON.stringify({ model: current.current.model }),
                        })
                    ).id,
                };
            },
            async fetch(id) {
                return metadata((await json<{ thread: Thread }>(`threads/${id}`)).thread);
            },
            async rename(id, title) {
                await request(`threads/${id}`, {
                    method: "PATCH",
                    body: JSON.stringify({ title }),
                });
            },
            async delete(id) {
                await request(`threads/${id}`, { method: "DELETE" });
            },
            async archive() {
                throw new NovaError("当前版本暂未开放归档，请使用删除或切换对话。");
            },
            async generateTitle(id) {
                const { thread } = await json<{ thread: Thread }>(`threads/${id}`);
                return createAssistantStream(async (controller) => {
                    controller.appendText(thread.title || "新对话");
                });
            },
            unstable_useAdapters: useHistoryAdapters,
        }),
        [],
    );
    return (
        <NovaSettings.Provider value={settings}>
            <RuntimeBody adapter={adapter} settingsRef={current}>
                {children}
            </RuntimeBody>
        </NovaSettings.Provider>
    );
}
function RuntimeBody({
    adapter,
    children,
    settingsRef,
}: {
    adapter: RemoteThreadListAdapter;
    children: ReactNode;
    settingsRef: SettingsRef;
}) {
    // 显式传递稳定ref，避免远程线程宿主使用首次React上下文的模型快照。
    const runtime = useRemoteThreadListRuntime({
        adapter,
        runtimeHook: () => useNovaThread(settingsRef),
    });
    return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>;
}
