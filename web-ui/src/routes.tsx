import type { RouteObject } from 'react-router';
import { AppShell } from '@/components/layout/AppShell';
import { ArchivePage } from '@/pages/ArchivePage';
import { ChannelsPage } from '@/pages/ChannelsPage';
import { ChatPage } from '@/pages/ChatPage';
import { NotFoundPage } from '@/pages/NotFoundPage';
import { SettingsPage } from '@/pages/SettingsPage';

export const routes: RouteObject[] = [
  {
    element: <AppShell />,
    children: [
      { index: true, element: <ChatPage /> },
      { path: 'chat/:conversationId', element: <ChatPage /> },
      { path: 'archive', element: <ArchivePage /> },
      { path: 'channels', element: <ChannelsPage /> },
      { path: 'settings', element: <SettingsPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
];
