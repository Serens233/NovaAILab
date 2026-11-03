import { useCallback, useEffect, useRef } from 'react';
import { useNavigate, useParams } from 'react-router';
import { Info, PanelRightOpen } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, SheetContent } from '@/components/ui/dialog';
import { Tip } from '@/components/ui/tooltip';
import { NovaAvatar } from '@/components/character/NovaAvatar';
import { NovaStatusPanel } from '@/components/character/NovaStatusPanel';
import { Composer } from '@/components/chat/Composer';
import { ConnectionBanner } from '@/components/chat/ConnectionBanner';
import { MessageList } from '@/components/chat/MessageList';
import { WelcomeScreen } from '@/components/chat/WelcomeScreen';
import { EmptyState, ErrorNotice, Skeleton } from '@/components/common/Feedback';
import { PageHeader } from '@/components/common/PageHeader';
import { useCapabilities, useConversations, useCreateConversation, useMessages } from '@/hooks/queries';
import { useChatSession } from '@/hooks/useChatSession';
import { useConnectionState } from '@/hooks/useConnectionState';
import { BREAKPOINTS, useMediaQuery } from '@/hooks/useMediaQuery';
import { isNovaApiError } from '@/services/api/errors';
import { useSettingsStore } from '@/stores/settingsStore';
import { useUiStore } from '@/stores/uiStore';

export function ChatPage() {
  const { conversationId } = useParams();
  const navigate = useNavigate();
  const isDesktop = useMediaQuery(BREAKPOINTS.desktop);
  const statusPanelOpen = useSettingsStore((s) => s.statusPanelOpen);
  const setStatusPanelOpen = useSettingsStore((s) => s.setStatusPanelOpen);
  const statusSheetOpen = useUiStore((s) => s.statusSheetOpen);
  const setStatusSheetOpen = useUiStore((s) => s.setStatusSheetOpen);

  const conn = useConnectionState();
  const caps = useCapabilities().data;
  const conversations = useConversations();
  const conversation = conversations.data?.find((c) => c.conversationId === conversationId);
  const messages = useMessages(conversationId);
  const chat = useChatSession(conversationId, caps);
  const create = useCreateConversation();

  // 欢迎页直接发送：先创建会话，路由切换后再发送（避免切换会话时的清理逻辑取消该请求）
  const pendingFirstRef = useRef<{ id: string; text: string } | null>(null);
  const { send } = chat;
  useEffect(() => {
    const pending = pendingFirstRef.current;
    if (pending && pending.id === conversationId) {
      pendingFirstRef.current = null;
      void send(pending.text, pending.id);
    }
  }, [conversationId, send]);

  const handleSend = useCallback(
    (text: string) => {
      if (conversationId) {
        void send(text);
        return;
      }
      create.mutate(
        {},
        {
          onSuccess: (conv) => {
            pendingFirstRef.current = { id: conv.conversationId, text };
            navigate(`/chat/${encodeURIComponent(conv.conversationId)}`);
          },
        },
      );
    },
    [conversationId, create, navigate, send],
  );

  const offline = conn.state === 'offline' || conn.state === 'browser_offline' || conn.state === 'not_configured';
  const disabledReason = !offline
    ? undefined
    : conn.state === 'browser_offline'
      ? '本机网络已断开，恢复后即可发送'
      : conn.state === 'not_configured'
        ? '未识别到 Nova 后端，请在设置页检查 API 地址'
        : 'Nova 后端离线，暂时无法发送消息';

  const notFound = isNovaApiError(messages.error) && messages.error.status === 404;
  const messageList = messages.data ?? [];
  const showWelcomeInList = messages.isSuccess && messageList.length === 0 && !chat.turn;

  const statusPanel = (
    <NovaStatusPanel
      conversation={conversation}
      messageCount={messages.data?.length}
      onCollapse={isDesktop ? () => setStatusPanelOpen(false) : undefined}
    />
  );

  return (
    <div className="flex h-full min-h-0">
      <section className="flex min-w-0 flex-1 flex-col" aria-label="对话区域">
        <PageHeader
          leading={<NovaAvatar size="sm" status={conn.state === 'browser_offline' ? 'offline' : conn.state === 'not_configured' ? 'degraded' : conn.state} />}
          title={conversation?.title ?? (conversationId ? '夜航记录' : 'Nova')}
          subtitle={conversation ? `${conn.label}${conversation.localOnly ? ' · 临时会话（不会保存到后端）' : ''}` : '星图档案馆的夜航记录员'}
          actions={
            isDesktop ? (
              !statusPanelOpen && (
                <Tip content="展开角色状态">
                  <Button variant="ghost" size="icon" onClick={() => setStatusPanelOpen(true)} aria-label="展开角色状态面板">
                    <PanelRightOpen />
                  </Button>
                </Tip>
              )
            ) : (
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setStatusSheetOpen(true)}
                aria-label="查看 Nova 角色状态"
                data-testid="open-status"
              >
                <Info />
              </Button>
            )
          }
        />
        <ConnectionBanner conn={conn} />
        {conversation?.localOnly && (
          <div className="border-b bg-warning/10 px-4 py-1.5 text-center text-[0.72rem]">
            后端暂未提供会话持久化接口：这段对话仅保存在当前页面，刷新后会丢失（回复内容来自真实后端）。
          </div>
        )}

        {!conversationId ? (
          <div className="min-h-0 flex-1 overflow-y-auto">
            <WelcomeScreen onPick={handleSend} disabled={offline || create.isPending} />
            {create.isError && (
              <div className="mx-auto max-w-md px-4">
                <ErrorNotice error={create.error} onRetry={() => create.reset()} />
              </div>
            )}
          </div>
        ) : messages.isPending ? (
          <div className="mx-auto w-full max-w-[var(--nova-chat-max-width)] flex-1 space-y-4 px-6 py-8" aria-busy>
            <Skeleton className="h-14 w-2/3" />
            <Skeleton className="ml-auto h-10 w-1/2" />
            <Skeleton className="h-20 w-3/4" />
          </div>
        ) : messages.isError ? (
          <div className="flex-1 overflow-y-auto px-4 py-10">
            {notFound ? (
              <EmptyState
                title="找不到这段夜航记录"
                description="它可能已被删除，或属于一段已失效的临时会话。"
                action={
                  <Button variant="outline" onClick={() => navigate('/')}>
                    返回首页
                  </Button>
                }
              />
            ) : (
              <div className="mx-auto max-w-lg">
                <ErrorNotice error={messages.error} onRetry={() => messages.refetch()} />
              </div>
            )}
          </div>
        ) : (
          <MessageList
            conversationId={conversationId}
            messages={messageList}
            turn={chat.turn}
            onRetry={chat.retry}
            onDismiss={chat.dismiss}
            header={showWelcomeInList ? <WelcomeScreen onPick={handleSend} disabled={offline} /> : undefined}
          />
        )}

        <Composer
          onSend={handleSend}
          onStop={() => void chat.stop()}
          busy={chat.isBusy || create.isPending}
          disabled={offline || notFound}
          disabledReason={disabledReason}
        />
      </section>

      {isDesktop ? (
        statusPanelOpen && (
          <aside
            className="relative z-[10] h-full w-[var(--nova-status-width)] shrink-0 border-l bg-surface/50 backdrop-blur-md"
            aria-label="Nova 角色状态"
          >
            {statusPanel}
          </aside>
        )
      ) : (
        <Dialog open={statusSheetOpen} onOpenChange={setStatusSheetOpen}>
          <SheetContent side="right" title="Nova 角色状态" data-testid="status-sheet">
            {statusPanel}
          </SheetContent>
        </Dialog>
      )}
    </div>
  );
}
