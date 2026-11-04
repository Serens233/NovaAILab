import { useMemo, type ReactNode } from 'react';
import { novaConfig, type NovaConfig } from '@/config/env';
import { createAdapter } from './adapters/createAdapter';
import type { NovaAdapter } from './adapters/types';
import { AdapterChannelService } from './channels/channelService';
import { NovaServicesContext, type NovaServices } from './novaContext';

/** 注入适配器。测试中可传入自定义 adapter（如带故障场景的 MockAdapter 或带假 fetch 的 RealAdapter）。 */
export function NovaProvider({
  children,
  adapter,
  config = novaConfig,
}: {
  children: ReactNode;
  adapter?: NovaAdapter;
  config?: NovaConfig;
}) {
  const value = useMemo<NovaServices>(() => {
    const a = adapter ?? createAdapter(config);
    return { adapter: a, channels: new AdapterChannelService(a), config };
  }, [adapter, config]);
  return <NovaServicesContext.Provider value={value}>{children}</NovaServicesContext.Provider>;
}
