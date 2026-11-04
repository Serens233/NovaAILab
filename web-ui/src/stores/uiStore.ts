import { create } from 'zustand';

/** 仅内存中的界面状态（不持久化） */
interface UiState {
  navOpen: boolean;
  statusSheetOpen: boolean;
  notice: string | null;
  setNavOpen: (open: boolean) => void;
  setStatusSheetOpen: (open: boolean) => void;
  setNotice: (notice: string | null) => void;
}

export const useUiStore = create<UiState>()((set) => ({
  navOpen: false,
  statusSheetOpen: false,
  notice: null,
  setNavOpen: (navOpen) => set({ navOpen }),
  setStatusSheetOpen: (statusSheetOpen) => set({ statusSheetOpen }),
  setNotice: (notice) => set({ notice }),
}));
