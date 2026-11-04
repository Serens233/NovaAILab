import { useDeferredValue, useState, type ReactNode } from 'react';
import { BookMarked, Clock3, FlaskConical, GitCommitVertical, History, Hourglass, Link2, Search, X } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NovaStar } from '@/components/character/NovaStar';
import { EmptyState, ErrorNotice, SectionTitle, Skeleton } from '@/components/common/Feedback';
import { PageHeader } from '@/components/common/PageHeader';
import { useCapabilities, useMemories } from '@/hooks/queries';
import { useConnectionState } from '@/hooks/useConnectionState';
import { isNovaApiError } from '@/services/api/errors';
import type { MemoryCategory, MemoryRecord } from '@/types/nova';
import { cn } from '@/utils/cn';
import { formatFullDate, formatRelative } from '@/utils/time';

const CATEGORIES: { key: MemoryCategory | 'all'; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'preference', label: '喜好' },
  { key: 'event', label: '事件' },
  { key: 'knowledge', label: '知识' },
  { key: 'relationship', label: '约定与关系' },
  { key: 'habit', label: '习惯' },
  { key: 'other', label: '其它' },
];
const CATEGORY_LABEL = Object.fromEntries(CATEGORIES.map((c) => [c.key, c.label])) as Record<string, string>;
const CHANNEL_LABEL = { web: 'Web', qq: 'QQ', wechat: '微信' } as const;

function WaitingForMemory({ reason }: { reason: string }) {
  return (
    <EmptyState
      icon={<Hourglass className="size-10 text-warning" />}
      title="等待记忆服务接入"
      description={
        <>
          <p>{reason}</p>
          <p className="mt-2">记忆档案将在 Nova 后端提供记忆查询接口后显示。前端不会伪造任何记忆记录。</p>
        </>
      }
    />
  );
}

/** 星图档案馆：记忆列表、分类、搜索、详情 */
export function ArchivePage() {
  const conn = useConnectionState();
  const caps = useCapabilities();
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState<MemoryCategory | 'all'>('all');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const deferredSearch = useDeferredValue(search);

  const backendReachable = conn.state === 'online' || conn.state === 'degraded';
  const memoryEnabled = backendReachable && caps.data?.memory === true;
  const memories = useMemories({ search: deferredSearch, category }, memoryEnabled);
  const notImplemented = isNovaApiError(memories.error) && memories.error.kind === 'not_implemented';
  const selected = memories.data?.find((m) => m.memoryId === selectedId) ?? null;

  let body: ReactNode;
  if (!backendReachable) {
    body = conn.state === 'checking' ? (
      <Skeleton className="h-40" />
    ) : (
      <EmptyState title="无法读取星图档案" description="Nova 后端当前未连接，恢复连接后会自动重新读取。" />
    );
  } else if (caps.isPending) {
    body = <Skeleton className="h-40" />;
  } else if (!memoryEnabled) {
    body = <WaitingForMemory reason="后端没有声明（或未暴露）记忆查询能力。" />;
  } else if (notImplemented) {
    body = <WaitingForMemory reason="后端返回：记忆接口不存在。" />;
  } else if (memories.isError) {
    body = <ErrorNotice error={memories.error} onRetry={() => memories.refetch()} />;
  } else if (memories.isPending) {
    body = (
      <div className="space-y-2">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-16" />
        ))}
      </div>
    );
  } else if (memories.data.length === 0) {
    body = <EmptyState title={search || category !== 'all' ? '没有符合条件的记忆' : '档案馆还是空的'} description="随着更多夜航，Nova 会把重要的事整理进这里。" />;
  } else {
    body = (
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <ul className="space-y-2" aria-label="记忆列表">
          {memories.data.map((m) => (
            <li key={m.memoryId}>
              <button
                type="button"
                onClick={() => setSelectedId(m.memoryId)}
                aria-pressed={selectedId === m.memoryId}
                className={cn(
                  'nova-hover-glow nova-panel w-full p-3 text-left',
                  selectedId === m.memoryId && 'border-primary/40 bg-primary-soft',
                )}
              >
                <div className="flex items-center gap-2">
                  <BookMarked className="size-4 shrink-0 text-primary" />
                  <span className="min-w-0 flex-1 truncate font-medium">{m.title}</span>
                  <Badge tone="primary">{CATEGORY_LABEL[m.category]}</Badge>
                </div>
                <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{m.content}</p>
                <p className="mt-1.5 flex items-center gap-1 text-[0.7rem] text-muted-foreground">
                  <Clock3 className="size-3" /> 形成于 {formatRelative(m.createdAt)}
                </p>
              </button>
            </li>
          ))}
        </ul>
        <div className="lg:sticky lg:top-0 lg:self-start">
          {selected ? (
            <MemoryDetail memory={selected} historySupported={caps.data?.memoryHistory === true} onClose={() => setSelectedId(null)} />
          ) : (
            <div className="nova-panel hidden p-6 text-center text-sm text-muted-foreground lg:block">
              <NovaStar size={36} orbit className="mx-auto mb-2" />
              选择一条记忆，查看它的来源与修改记录
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader title="星图档案馆" subtitle="Nova 的记忆档案 · 记录形成时间、来源与修改" />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-5xl space-y-4 px-4 py-5 sm:px-6">
          {conn.isMock && memoryEnabled && (
            <div className="flex items-center gap-2 rounded-lg border border-gold/50 bg-gold/10 px-3 py-2 text-xs" role="note">
              <FlaskConical className="size-4 shrink-0" />
              以下记忆为 Mock 演示数据，用于检查界面，并非 Nova 的真实记忆记录。
            </div>
          )}

          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <div className="relative flex-1">
              <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="搜索记忆标题或内容"
                aria-label="搜索记忆"
                className="pl-9"
                disabled={!memoryEnabled}
              />
            </div>
            <Button variant="outline" disabled title="成长时间线等待后端提供成长记录接口">
              <History /> 成长时间线 · 即将开放
            </Button>
          </div>

          <div className="flex flex-wrap gap-1.5" role="group" aria-label="记忆分类">
            {CATEGORIES.map((c) => (
              <button
                key={c.key}
                type="button"
                disabled={!memoryEnabled}
                onClick={() => setCategory(c.key)}
                aria-pressed={category === c.key}
                className={cn(
                  'rounded-full border px-3 py-1 text-xs transition-colors disabled:opacity-50',
                  category === c.key ? 'border-primary/40 bg-primary text-primary-foreground' : 'bg-surface hover:bg-primary-soft',
                )}
              >
                {c.label}
              </button>
            ))}
          </div>

          {body}
        </div>
      </div>
    </div>
  );
}

function MemoryDetail({
  memory,
  historySupported,
  onClose,
}: {
  memory: MemoryRecord;
  historySupported: boolean;
  onClose: () => void;
}) {
  return (
    <article className="nova-panel nova-frame nova-anim-rise p-5" aria-label={`记忆详情：${memory.title}`}>
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <Badge tone="primary">{CATEGORY_LABEL[memory.category]}</Badge>
          <h3 className="mt-2 text-lg font-semibold">{memory.title}</h3>
        </div>
        <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="关闭详情">
          <X />
        </Button>
      </div>
      <p className="mt-3 text-[0.95rem] leading-relaxed whitespace-pre-wrap">{memory.content}</p>
      <dl className="mt-4 space-y-2 border-t pt-3 text-sm">
        <div className="flex gap-2">
          <dt className="w-20 shrink-0 text-muted-foreground">形成时间</dt>
          <dd>{formatFullDate(memory.createdAt)}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="w-20 shrink-0 text-muted-foreground">来源</dt>
          <dd className="min-w-0">
            {memory.source ? (
              <span className="flex flex-wrap items-center gap-1">
                {memory.source.channelType && <Badge tone="star">{CHANNEL_LABEL[memory.source.channelType]}</Badge>}
                {memory.source.description ?? memory.source.conversationId ?? '—'}
                {memory.source.conversationId && <Link2 className="size-3.5 text-muted-foreground" aria-label="关联夜航记录" />}
              </span>
            ) : (
              <span className="text-muted-foreground">后端未提供</span>
            )}
          </dd>
        </div>
        {memory.importance !== undefined && (
          <div className="flex gap-2">
            <dt className="w-20 shrink-0 text-muted-foreground">重要程度</dt>
            <dd aria-label={`${memory.importance} / 5`}>{'✦'.repeat(Math.max(0, Math.min(5, memory.importance)))}</dd>
          </div>
        )}
      </dl>
      <div className="mt-4">
        <SectionTitle className="mb-2">修改记录</SectionTitle>
        {!historySupported ? (
          <p className="text-xs text-muted-foreground">后端暂未提供记忆修改记录。</p>
        ) : memory.revisions?.length ? (
          <ol className="space-y-2">
            {memory.revisions.map((r, i) => (
              <li key={i} className="flex gap-2 text-sm">
                <GitCommitVertical className="mt-0.5 size-4 shrink-0 text-accent" />
                <div>
                  <p>{r.summary}</p>
                  <p className="text-[0.7rem] text-muted-foreground">{formatFullDate(r.revisedAt)}</p>
                </div>
              </li>
            ))}
          </ol>
        ) : (
          <p className="text-xs text-muted-foreground">暂无修改记录。</p>
        )}
      </div>
    </article>
  );
}
