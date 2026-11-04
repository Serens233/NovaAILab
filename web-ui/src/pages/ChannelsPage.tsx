import { RefreshCw, ShieldAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ChannelCard } from '@/components/channels/ChannelCard';
import { ErrorNotice } from '@/components/common/Feedback';
import { PageHeader } from '@/components/common/PageHeader';
import { useChannelStatuses } from '@/hooks/queries';
import { useConnectionState } from '@/hooks/useConnectionState';
import { describeError, isNovaApiError } from '@/services/api/errors';
import type { ChannelType } from '@/types/nova';

const ORDER: ChannelType[] = ['web', 'qq', 'wechat'];

/** 渠道管理：Web / QQ Bot / 微信 Bot。没有后端真实状态时，一律显示「状态未知」。 */
export function ChannelsPage() {
  const conn = useConnectionState();
  const channels = useChannelStatuses();

  let unknownReason: string | undefined;
  if (channels.isPending) unknownReason = '正在向后端查询渠道状态…';
  else if (channels.isError) {
    unknownReason =
      isNovaApiError(channels.error) && channels.error.kind === 'not_implemented'
        ? '后端暂未提供渠道状态接口，等待对接。'
        : `无法获取渠道状态：${describeError(channels.error).title}`;
  } else unknownReason = '后端未返回该渠道的信息。';

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader
        title="渠道管理"
        subtitle="Web · QQ Bot · 微信 Bot"
        actions={
          <Button variant="ghost" size="sm" onClick={() => channels.refetch()} disabled={channels.isFetching}>
            <RefreshCw className={channels.isFetching ? 'animate-spin' : undefined} /> 刷新
          </Button>
        }
      />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-6xl space-y-4 px-4 py-5 sm:px-6">
          <div className="flex gap-3 rounded-lg border border-star/40 bg-star/10 px-4 py-3 text-xs leading-relaxed" role="note">
            <ShieldAlert className="mt-0.5 size-4 shrink-0 text-primary" />
            <div>
              <p className="font-medium text-foreground">渠道接入由 Nova 后端负责</p>
              <p className="text-muted-foreground">
                平台身份映射、Token / 密钥、回调验证、账号授权、消息收发、去重与限频都在后端处理；
                浏览器不保存任何平台凭据，也不直接控制 QQ / 微信客户端。本页只展示后端报告的状态。
              </p>
            </div>
          </div>

          {channels.isError && !(isNovaApiError(channels.error) && channels.error.kind === 'not_implemented') && (
            <ErrorNotice error={channels.error} onRetry={() => channels.refetch()} />
          )}

          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {ORDER.map((type) => (
              <ChannelCard
                key={type}
                channelType={type}
                info={channels.data?.find((c) => c.channelType === type)}
                unknownReason={unknownReason}
                isMock={conn.isMock}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
