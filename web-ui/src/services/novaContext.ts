import { createContext, useContext } from 'react';
import type { NovaConfig } from '@/config/env';
import type { NovaAdapter } from './adapters/types';
import type { ChannelService } from './channels/channelService';

export interface NovaServices {
  adapter: NovaAdapter;
  channels: ChannelService;
  config: NovaConfig;
}

export const NovaServicesContext = createContext<NovaServices | null>(null);

export function useNovaServices(): NovaServices {
  const ctx = useContext(NovaServicesContext);
  if (!ctx) throw new Error('useNovaServices 必须在 <NovaProvider> 内使用');
  return ctx;
}

export function useNovaAdapter(): NovaAdapter {
  return useNovaServices().adapter;
}
