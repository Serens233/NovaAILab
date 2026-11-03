import { useEffect, useState, type ReactNode } from 'react';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { TooltipProvider } from '@/components/ui/tooltip';
import { NovaProvider } from '@/services/NovaProvider';
import type { NovaAdapter } from '@/services/adapters/types';
import { createQueryClient } from '@/services/queryClient';
import { useSettingsStore } from '@/stores/settingsStore';

/** 把偏好设置同步到 <html>：主题 class、字号、动画开关 */
function DocumentPreferences() {
  const theme = useSettingsStore((s) => s.theme);
  const fontSize = useSettingsStore((s) => s.fontSize);
  const motion = useSettingsStore((s) => s.motion);
  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle('dark', theme === 'dark');
    root.dataset.fontSize = fontSize;
    root.dataset.motion = motion ? 'on' : 'off';
    document
      .querySelector('meta[name="theme-color"]')
      ?.setAttribute('content', getComputedStyle(root).getPropertyValue('--nova-bg').trim() || '#7856D7');
  }, [theme, fontSize, motion]);
  return null;
}

/** 应用级 Provider（测试中可注入 adapter 与 queryClient） */
export function AppProviders({
  children,
  adapter,
  queryClient,
}: {
  children: ReactNode;
  adapter?: NovaAdapter;
  queryClient?: QueryClient;
}) {
  const [client] = useState(() => queryClient ?? createQueryClient());
  return (
    <QueryClientProvider client={client}>
      <NovaProvider adapter={adapter}>
        <TooltipProvider>
          <DocumentPreferences />
          {children}
        </TooltipProvider>
      </NovaProvider>
    </QueryClientProvider>
  );
}
