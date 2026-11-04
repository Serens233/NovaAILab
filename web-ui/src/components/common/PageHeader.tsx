import type { ReactNode } from 'react';
import { Menu } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { BREAKPOINTS, useMediaQuery } from '@/hooks/useMediaQuery';
import { useUiStore } from '@/stores/uiStore';
import { cn } from '@/utils/cn';

/** 页面顶部栏：窄屏时显示打开导航抽屉的按钮 */
export function PageHeader({
  title,
  subtitle,
  leading,
  actions,
  className,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  leading?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  const isTablet = useMediaQuery(BREAKPOINTS.tablet);
  const setNavOpen = useUiStore((s) => s.setNavOpen);
  return (
    <header
      className={cn(
        'relative z-[20] flex min-h-14 shrink-0 items-center gap-2 border-b bg-surface/70 px-3 backdrop-blur-md sm:px-4',
        className,
      )}
    >
      {!isTablet && (
        <Button variant="ghost" size="icon" aria-label="打开导航" onClick={() => setNavOpen(true)} data-testid="open-nav">
          <Menu />
        </Button>
      )}
      {leading}
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-base font-semibold">{title}</h1>
        {subtitle && <div className="truncate text-xs text-muted-foreground">{subtitle}</div>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-1">{actions}</div>}
    </header>
  );
}
