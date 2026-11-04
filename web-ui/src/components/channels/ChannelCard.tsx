import { useState } from 'react';
import { CheckCircle2, Globe, MessageCircle, MessagesSquare, ScrollText, Settings2, ShieldQuestion, TriangleAlert } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ErrorNotice } from '@/components/common/Feedback';
import { useNovaServices } from '@/services/novaContext';
import { CHANNEL_META } from '@/services/channels/channelService';
import type { ChannelInfo, ChannelStatus, ChannelType } from '@/types/nova';
import { formatFullDate } from '@/utils/time';

const ICONS: Record<ChannelType, typeof Globe> = { web: Globe, qq: MessageCircle, wechat: MessagesSquare };

/** 'unknown'：前端没有拿到后端真实状态 */
export type DisplayChannelStatus = ChannelStatus | 'unknown';

const STATUS: Record<DisplayChannelStatus, { label: string; tone: 'success' | 'warning' | 'danger' | 'neutral' | 'primary' }> = {
  connected: { label: '已连接', tone: 'success' },
  connecting: { label: '连接中', tone: 'primary' },
  disconnected: { label: '已断开', tone: 'warning' },
  not_configured: { label: '未配置', tone: 'neutral' },
  error: { label: '连接失败', tone: 'danger' },
  unknown: { label: '状态未知', tone: 'neutral' },
};

const PROACTIVE = { granted: '已授权', denied: '未授权', unknown: '未知' } as const;

export function ChannelCard({
  channelType,
  info,
  unknownReason,
  isMock,
}: {
  channelType: ChannelType;
  info?: ChannelInfo;
  unknownReason?: string;
  isMock: boolean;
}) {
  const { channels } = useNovaServices();
  const [actionError, setActionError] = useState<unknown>(null);
  const meta = CHANNEL_META[channelType];
  const Icon = ICONS[channelType];
  const status: DisplayChannelStatus = info?.status ?? 'unknown';
  const s = STATUS[status];

  const run = async (fn: () => Promise<unknown>) => {
    setActionError(null);
    try {
      await fn();
    } catch (err) {
      setActionError(err);
    }
  };

  return (
    <article className="nova-panel nova-frame flex flex-col p-5" data-testid={`channel-${channelType}`} data-status={status}>
      <div className="flex items-start gap-3">
        <span className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-primary-soft text-primary">
          <Icon className="size-5" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold">{info?.displayName ?? meta.name}</h3>
          <p className="text-xs text-muted-foreground">{meta.description}</p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <Badge tone={s.tone} data-testid={`channel-${channelType}-status`}>
            {status === 'connected' ? <CheckCircle2 /> : status === 'error' ? <TriangleAlert /> : null}
            {s.label}
          </Badge>
          {isMock && info && <Badge tone="gold">演示状态</Badge>}
        </div>
      </div>

      {status === 'unknown' && unknownReason && <p className="mt-3 rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">{unknownReason}</p>}
      {info?.lastError && (
        <p className="mt-3 rounded-md border border-danger/30 bg-danger/8 px-3 py-2 text-xs">最近错误：{info.lastError}</p>
      )}

      <dl className="mt-4 grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
        <div>
          <dt className="text-muted-foreground">最后同步</dt>
          <dd className="mt-0.5">{info?.lastSyncAt ? formatFullDate(info.lastSyncAt) : '—'}</dd>
        </div>
        <div>
          <dt className="flex items-center gap-1 text-muted-foreground">
            <ShieldQuestion className="size-3" /> 主动消息授权
          </dt>
          <dd className="mt-0.5">{info ? PROACTIVE[info.proactiveMessaging ?? 'unknown'] : '—'}</dd>
        </div>
      </dl>

      <div className="mt-4 grid gap-3 text-xs sm:grid-cols-2">
        <div>
          <p className="mb-1 font-medium">收发能力</p>
          <ul className="space-y-0.5 text-muted-foreground">
            {meta.abilities.map((a) => (
              <li key={a}>· {a}</li>
            ))}
            {info?.capabilities && info.capabilities.length > 0 && <li>· 后端声明：{info.capabilities.join('、')}</li>}
          </ul>
        </div>
        <div>
          <p className="mb-1 font-medium">限制与注意</p>
          <ul className="space-y-0.5 text-muted-foreground">
            {meta.caveats.map((c) => (
              <li key={c}>· {c}</li>
            ))}
          </ul>
        </div>
      </div>

      <div className="mt-auto flex flex-wrap gap-2 pt-4">
        <Button size="sm" variant="outline" onClick={() => run(() => channels.getConfigEntry(channelType))}>
          <Settings2 /> 配置入口
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => run(() => channels.getChannelLogs(info?.channelId ?? channelType))}
        >
          <ScrollText /> 连接日志
        </Button>
      </div>
      {actionError !== null && <ErrorNotice error={actionError} compact className="mt-3" />}
    </article>
  );
}
