import { useSyncExternalStore } from 'react';

export const BREAKPOINTS = {
  /** ≥ 768px：左侧导航常驻 */
  tablet: '(min-width: 768px)',
  /** ≥ 1280px：右侧角色面板常驻，形成三栏布局 */
  desktop: '(min-width: 1280px)',
} as const;

export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      if (typeof window === 'undefined' || !window.matchMedia) return () => {};
      const mql = window.matchMedia(query);
      mql.addEventListener('change', onChange);
      return () => mql.removeEventListener('change', onChange);
    },
    () => (typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(query).matches : true),
    () => true,
  );
}
