import { useMemo, useState } from 'react';
import { NavLink } from 'react-router';
import { MoreHorizontal, Pencil, Search, Trash2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { ErrorNotice, Skeleton } from '@/components/common/Feedback';
import {
  useCapabilities,
  useConversations,
  useDeleteConversation,
  useRenameConversation,
} from '@/hooks/queries';
import type { Conversation } from '@/types/nova';
import { cn } from '@/utils/cn';
import { formatRelative } from '@/utils/time';

export function ConversationList({
  activeId,
  onNavigate,
  onDeleted,
}: {
  activeId?: string;
  onNavigate?: () => void;
  onDeleted?: (id: string) => void;
}) {
  const conversations = useConversations();
  const caps = useCapabilities().data;
  const [search, setSearch] = useState('');
  const [renaming, setRenaming] = useState<Conversation | null>(null);
  const [deleting, setDeleting] = useState<Conversation | null>(null);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const list = conversations.data ?? [];
    return q ? list.filter((c) => c.title.toLowerCase().includes(q) || c.preview?.toLowerCase().includes(q)) : list;
  }, [conversations.data, search]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="relative px-3 pb-2">
        <Search className="pointer-events-none absolute top-1/2 left-6 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="搜索夜航记录"
          aria-label="搜索夜航记录"
          className="h-9 pl-9"
        />
      </div>

      <nav aria-label="夜航记录" className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {conversations.isPending && (
          <div className="space-y-2 px-1 pt-1">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-12" />
            ))}
          </div>
        )}
        {conversations.isError && (
          <ErrorNotice error={conversations.error} onRetry={() => conversations.refetch()} className="mx-1" />
        )}
        {conversations.isSuccess && filtered.length === 0 && (
          <p className="px-3 py-6 text-center text-xs text-muted-foreground">
            {search ? '没有找到匹配的记录' : '还没有夜航记录，点击上方「新的夜航」开始吧'}
          </p>
        )}
        <ul className="space-y-1">
          {filtered.map((c) => (
            <li key={c.conversationId} className="group relative">
              <NavLink
                to={`/chat/${encodeURIComponent(c.conversationId)}`}
                onClick={onNavigate}
                className={({ isActive }) =>
                  cn(
                    'nova-hover-glow block rounded-lg border border-transparent px-3 py-2 pr-9 text-sm transition-colors',
                    isActive || activeId === c.conversationId
                      ? 'border-primary/25 bg-primary-soft text-primary'
                      : 'hover:bg-surface-strong',
                  )
                }
              >
                <div className="flex items-center gap-1.5">
                  <span className="truncate font-medium">{c.title}</span>
                  {c.localOnly && (
                    <Badge tone="warning" className="px-1.5 py-0 text-[0.62rem]">
                      仅本页
                    </Badge>
                  )}
                </div>
                <div className="mt-0.5 flex items-center gap-2 text-[0.72rem] text-muted-foreground">
                  <span className="shrink-0">{formatRelative(c.updatedAt)}</span>
                  {c.preview && <span className="truncate">{c.preview}</span>}
                </div>
              </NavLink>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`「${c.title}」的更多操作`}
                    className="absolute top-1/2 right-1 -translate-y-1/2 opacity-60 group-hover:opacity-100 focus-visible:opacity-100"
                  >
                    <MoreHorizontal />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem
                    disabled={!c.localOnly && !caps?.renameConversation}
                    onSelect={() => setRenaming(c)}
                  >
                    <Pencil /> 重命名{!c.localOnly && !caps?.renameConversation && '（暂不可用）'}
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    tone="danger"
                    disabled={!c.localOnly && !caps?.deleteConversation}
                    onSelect={() => setDeleting(c)}
                  >
                    <Trash2 /> 删除{!c.localOnly && !caps?.deleteConversation && '（暂不可用）'}
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </li>
          ))}
        </ul>
      </nav>

      <RenameDialog conversation={renaming} onClose={() => setRenaming(null)} />
      <DeleteDialog
        conversation={deleting}
        onClose={() => setDeleting(null)}
        onDeleted={(id) => onDeleted?.(id)}
      />
    </div>
  );
}

function RenameDialog({ conversation, onClose }: { conversation: Conversation | null; onClose: () => void }) {
  const rename = useRenameConversation();
  const [title, setTitle] = useState('');
  const [lastId, setLastId] = useState<string | null>(null);
  if (conversation && conversation.conversationId !== lastId) {
    setLastId(conversation.conversationId);
    setTitle(conversation.title);
    rename.reset();
  }
  const submit = () => {
    if (!conversation || !title.trim()) return;
    rename.mutate({ id: conversation.conversationId, title: title.trim() }, { onSuccess: onClose });
  };
  return (
    <Dialog
      open={!!conversation}
      onOpenChange={(open) => {
        if (!open) {
          setLastId(null);
          onClose();
        }
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>重命名夜航记录</DialogTitle>
          <DialogDescription>为这段对话取一个好记的名字。</DialogDescription>
        </DialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
          className="space-y-3"
        >
          <Input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={60} autoFocus aria-label="新名称" />
          {rename.isError && <ErrorNotice error={rename.error} compact />}
          <DialogFooter>
            <Button variant="ghost" onClick={onClose}>
              取消
            </Button>
            <Button type="submit" disabled={!title.trim() || rename.isPending}>
              {rename.isPending ? '保存中…' : '保存'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DeleteDialog({
  conversation,
  onClose,
  onDeleted,
}: {
  conversation: Conversation | null;
  onClose: () => void;
  onDeleted: (id: string) => void;
}) {
  const del = useDeleteConversation();
  return (
    <Dialog
      open={!!conversation}
      onOpenChange={(open) => {
        if (!open) {
          del.reset();
          onClose();
        }
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>删除夜航记录？</DialogTitle>
          <DialogDescription>
            「{conversation?.title}」将从{conversation?.localOnly ? '当前页面' : ' Nova 后端'}删除。此操作由后端执行，可能无法撤销。
          </DialogDescription>
        </DialogHeader>
        {del.isError && <ErrorNotice error={del.error} compact />}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button
            variant="danger"
            disabled={del.isPending}
            onClick={() =>
              conversation &&
              del.mutate(conversation.conversationId, {
                onSuccess: () => {
                  onDeleted(conversation.conversationId);
                  onClose();
                },
              })
            }
          >
            {del.isPending ? '删除中…' : '删除'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
