import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

/**
 * 前端偏好设置。只保存非敏感偏好（主题、动画、字号、面板折叠），
 * 不保存聊天内容、Token、密钥或任何平台凭据。
 */
export type ThemeMode = 'light' | 'dark';
export type FontSize = 'sm' | 'md' | 'lg';

interface SettingsState {
  theme: ThemeMode;
  motion: boolean;
  fontSize: FontSize;
  statusPanelOpen: boolean;
  setTheme: (theme: ThemeMode) => void;
  toggleTheme: () => void;
  setMotion: (motion: boolean) => void;
  setFontSize: (size: FontSize) => void;
  setStatusPanelOpen: (open: boolean) => void;
}

function safeStorage(): Storage {
  try {
    const k = '__nova_probe__';
    window.localStorage.setItem(k, '1');
    window.localStorage.removeItem(k);
    return window.localStorage;
  } catch {
    // 隐私模式等不可用时，退回内存存储
    const mem = new Map<string, string>();
    return {
      get length() {
        return mem.size;
      },
      clear: () => mem.clear(),
      getItem: (key) => mem.get(key) ?? null,
      key: (i) => [...mem.keys()][i] ?? null,
      removeItem: (key) => void mem.delete(key),
      setItem: (key, value) => void mem.set(key, value),
    };
  }
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set, get) => ({
      theme: 'light',
      motion: true,
      fontSize: 'md',
      statusPanelOpen: true,
      setTheme: (theme) => set({ theme }),
      toggleTheme: () => set({ theme: get().theme === 'dark' ? 'light' : 'dark' }),
      setMotion: (motion) => set({ motion }),
      setFontSize: (fontSize) => set({ fontSize }),
      setStatusPanelOpen: (statusPanelOpen) => set({ statusPanelOpen }),
    }),
    {
      name: 'nova-ui-preferences',
      version: 1,
      storage: createJSONStorage(safeStorage),
      partialize: ({ theme, motion, fontSize, statusPanelOpen }) => ({ theme, motion, fontSize, statusPanelOpen }),
    },
  ),
);
