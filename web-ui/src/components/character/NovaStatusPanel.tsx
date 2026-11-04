import type { ReactNode } from 'react';
import { BookHeart, Cpu, Database, MessagesSquare, PanelRightClose, Radio } from 'lucide-react';
import { characterAssets, novaProfile } from '@/assets/characterAssets';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ConnectionBadge, ModeBadge } from '@/components/common/StatusIndicators';
import { SectionTitle } from '@/components/common/Feedback';
import { useCapabilities } from '@/hooks/queries';
import { useConnectionState } from '@/hooks/useConnectionState';
import type { Conversation } from '@/types/nova';
import { formatFullDate, formatRelative } from '@/utils/time';
import { CharacterImage } from './NovaAvatar';
import { NovaStar } from './NovaStar';

function Row({ icon, label, children }: { icon: ReactNode; label: string; children: ReactNode }) {
  return (
    <div className="flex items-start gap-2.5 py-1.5 text-sm">
      <span className="mt-0.5 text-primary [&_svg]:size-4">{icon}</span>
      <span className="w-16 shrink-0 text-muted-foreground">{label}</span>
      <span className="min-w-0 flex-1 break-words">{children}</span>
    </div>
  );
}

const Unknown = ({ text = '后端未报告' }: { text?: string }) => <span className="text-muted-foreground">{text}</span>;

/** 右侧：Nova 角色状态面板。所有状态只展示后端真实报告的值。 */
export function NovaStatusPanel({
  conversation,
  messageCount,
  onCollapse,
}: {
  conversation?: Conversation;
  messageCount?: number;
  onCollapse?: () => void;
}) {
  const conn = useConnectionState();
  const caps = useCapabilities().data;
  const health = conn.health;
  const backendReachable = conn.state === 'online' || conn.state === 'degraded';

  const memoryLabel = !backendReachable
    ? <Unknown text="无法确认（后端未连接）" />
    : health?.memoryService === 'online'
      ? <Badge tone="success">在线</Badge>
      : health?.memoryService === 'offline'
        ? <Badge tone="danger">离线</Badge>
        : <Unknown text="未能确认" />;

  const streamingLabel = !caps
    ? <Unknown text="未知" />
    : caps.streaming === 'none'
      ? '普通响应（非流式）'
      : `${caps.streaming.toUpperCase()} 流式${caps.source === 'mock' ? '（模拟）' : caps.source === 'assumed' ? '（按配置，未经后端声明）' : ''}`;

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="status-panel">
      <div className="flex items-center justify-between px-4 pt-3 pb-2">
        <SectionTitle>角色状态</SectionTitle>
        {onCollapse && (
          <Button variant="ghost" size="icon-sm" onClick={onCollapse} aria-label="收起角色状态面板">
            <PanelRightClose />
          </Button>
        )}
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 pb-4">
        {/* 立绘卡片 */}
        <div className="nova-panel nova-frame relative overflow-hidden p-0">
          <CharacterImage slot={characterAssets.portrait} className="aspect-[15/16] w-full" fallbackSize={96} />
          <div
            className="absolute inset-x-0 bottom-0 px-4 pt-10 pb-3 text-white"
            style={{ background: 'var(--nova-portrait-scrim)' }}
          >
            <div className="flex items-end justify-between gap-2">
              <div>
                <p className="font-display text-3xl leading-none font-semibold italic">Nova</p>
                <p className="mt-1 text-[0.72rem] text-white/80">{novaProfile.identity}</p>
              </div>
              <NovaStar size={30} orbit animated />
            </div>
          </div>
        </div>

        <div className="nova-panel p-3">
          <div className="mb-1 flex flex-wrap gap-1.5">
            <ModeBadge isMock={conn.isMock} />
            <ConnectionBadge state={conn.state} />
          </div>
          <Row icon={<Cpu />} label="模型">
            {backendReachable && health?.modelName ? health.modelName : <Unknown />}
          </Row>
          <Row icon={<Database />} label="记忆服务">
            {memoryLabel}
          </Row>
          <Row icon={<Radio />} label="回复方式">
            {streamingLabel}
          </Row>
          {health?.latencyMs !== undefined && backendReachable && (
            <p className="pt-1 text-[0.7rem] text-muted-foreground">
              最近检测：{formatRelative(health.checkedAt)} · 延迟 {health.latencyMs} ms
            </p>
          )}
        </div>

        <div className="nova-panel p-3">
          <SectionTitle className="mb-1">当前夜航</SectionTitle>
          {conversation ? (
            <>
              <Row icon={<MessagesSquare />} label="标题">
                {conversation.title}
                {conversation.localOnly && (
                  <Badge tone="warning" className="ml-1">
                    仅本页
                  </Badge>
                )}
              </Row>
              <Row icon={<BookHeart />} label="开始于">
                {formatFullDate(conversation.createdAt)}
              </Row>
              <Row icon={<MessagesSquare />} label="消息数">
                {messageCount ?? conversation.messageCount ?? '—'}
              </Row>
            </>
          ) : (
            <p className="py-1 text-sm text-muted-foreground">尚未选择夜航记录</p>
          )}
        </div>

        <div className="nova-panel p-3 text-sm">
          <SectionTitle className="mb-2">身份小档案</SectionTitle>
          <dl className="space-y-1.5">
            <div>
              <dt className="text-xs text-muted-foreground">身份</dt>
              <dd>{novaProfile.identity}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">性格</dt>
              <dd>{conn.isMock ? novaProfile.personality : '温柔、好奇、略带俏皮，有独立观点'}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">兴趣</dt>
              <dd className="mt-1 flex flex-wrap gap-1">
                {conn.isMock ? novaProfile.interests.map((i) => (
                  <Badge key={i} tone="primary">
                    {i}
                  </Badge>
                )) : <Unknown />}
              </dd>
            </div>
          </dl>
        </div>
      </div>
    </div>
  );
}
