import { useEffect } from 'react';
import { Outlet, useLocation } from 'react-router';
import { Dialog, SheetContent } from '@/components/ui/dialog';
import { StarfieldBackground } from '@/components/character/StarfieldBackground';
import { BREAKPOINTS, useMediaQuery } from '@/hooks/useMediaQuery';
import { useUiStore } from '@/stores/uiStore';
import { Sidebar } from './Sidebar';

/**
 * 页面骨架：
 *  - ≥ 768px：左侧导航常驻
 *  - < 768px：左侧导航变为抽屉
 * 右侧角色面板由聊天页自行决定（≥ 1280px 常驻，否则为独立面板）。
 */
export function AppShell() {
  const isTablet = useMediaQuery(BREAKPOINTS.tablet);
  const navOpen = useUiStore((s) => s.navOpen);
  const setNavOpen = useUiStore((s) => s.setNavOpen);
  const location = useLocation();

  // 路由变化后关闭抽屉
  useEffect(() => {
    setNavOpen(false);
  }, [location.pathname, setNavOpen]);

  return (
    <div className="relative flex h-dvh w-full overflow-hidden" data-layout={isTablet ? 'sidebar' : 'drawer'}>
      <a
        href="#main"
        className="sr-only z-[60] rounded-md bg-primary px-3 py-2 text-primary-foreground focus:not-sr-only focus:absolute focus:top-2 focus:left-2"
      >
        跳到主要内容
      </a>
      <StarfieldBackground />
      {isTablet ? (
        <aside
          aria-label="星图档案导航"
          className="relative z-[10] h-full w-[var(--nova-sidebar-width)] shrink-0 border-r bg-surface/60 backdrop-blur-md"
          data-testid="sidebar"
        >
          <Sidebar />
        </aside>
      ) : (
        <Dialog open={navOpen} onOpenChange={setNavOpen}>
          <SheetContent side="left" title="星图档案导航" data-testid="nav-drawer">
            <Sidebar onNavigate={() => setNavOpen(false)} />
          </SheetContent>
        </Dialog>
      )}
      <main id="main" className="relative z-[10] flex min-w-0 flex-1 flex-col">
        <Outlet />
      </main>
    </div>
  );
}
