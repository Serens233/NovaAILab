import { NavLink, useMatch, useNavigate } from 'react-router';
import { BookOpenText, Moon, Plus, RadioTower, Settings, Sun } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Tip } from '@/components/ui/tooltip';
import { NovaStar } from '@/components/character/NovaStar';
import { ConnectionBadge, ModeBadge } from '@/components/common/StatusIndicators';
import { ErrorNotice } from '@/components/common/Feedback';
import { useCreateConversation } from '@/hooks/queries';
import { useConnectionState } from '@/hooks/useConnectionState';
import { useSettingsStore } from '@/stores/settingsStore';
import { cn } from '@/utils/cn';
import { ConversationList } from './ConversationList';

const NAV = [
  { to: '/archive', label: '星图档案', hint: '记忆档案馆', icon: BookOpenText },
  { to: '/channels', label: '渠道管理', hint: 'Web / QQ / 微信', icon: RadioTower },
  { to: '/settings', label: '设置', hint: '主题与连接', icon: Settings },
];

/** 左侧：星图档案导航 */
export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const navigate = useNavigate();
  const match = useMatch('/chat/:conversationId');
  const activeId = match?.params.conversationId;
  const create = useCreateConversation();
  const conn = useConnectionState();
  const theme = useSettingsStore((s) => s.theme);
  const toggleTheme = useSettingsStore((s) => s.toggleTheme);

  const newConversation = () => {
    create.mutate(
      {},
      {
        onSuccess: (conv) => {
          navigate(`/chat/${encodeURIComponent(conv.conversationId)}`);
          onNavigate?.();
        },
      },
    );
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Logo */}
      <div className="flex items-center gap-3 px-4 pt-4 pb-3">
        <NavLink to="/" onClick={onNavigate} className="flex min-w-0 items-center gap-2.5 rounded-lg" aria-label="Nova 首页">
          <span className="flex size-10 items-center justify-center rounded-xl border bg-surface-strong shadow-soft">
            <NovaStar size={30} orbit animated />
          </span>
          <span className="min-w-0">
            <span className="nova-gradient-text block font-display text-2xl leading-none font-semibold italic">Nova</span>
            <span className="block truncate text-[0.68rem] tracking-wide text-muted-foreground">星图档案馆 · UI-01</span>
          </span>
        </NavLink>
      </div>

      <div className="flex flex-wrap items-center gap-1.5 px-4 pb-3">
        <ModeBadge isMock={conn.isMock} />
        <ConnectionBadge state={conn.state} />
      </div>

      <div className="px-3 pb-3">
        <Button className="w-full" onClick={newConversation} disabled={create.isPending} data-testid="new-conversation">
          <Plus /> {create.isPending ? '正在展开新的星图…' : '新的夜航'}
        </Button>
        {create.isError && <ErrorNotice error={create.error} compact className="mt-2" />}
      </div>

      <div className="flex items-center gap-2 px-4 pb-1.5 text-[0.7rem] font-semibold tracking-widest text-muted-foreground">
        <NovaStar size={11} /> 夜航记录
      </div>
      <ConversationList activeId={activeId} onNavigate={onNavigate} onDeleted={(id) => id === activeId && navigate('/')} />

      <div className="border-t px-2 py-2">
        <ul className="space-y-0.5">
          {NAV.map(({ to, label, hint, icon: Icon }) => (
            <li key={to}>
              <NavLink
                to={to}
                onClick={onNavigate}
                className={({ isActive }) =>
                  cn(
                    'flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors',
                    isActive ? 'bg-primary-soft text-primary' : 'hover:bg-surface-strong',
                  )
                }
              >
                <Icon className="size-4" />
                <span className="flex-1">{label}</span>
                <span className="text-[0.68rem] text-muted-foreground">{hint}</span>
              </NavLink>
            </li>
          ))}
        </ul>
        <div className="mt-1 flex justify-end px-1">
          <Tip content={theme === 'dark' ? '切换到明亮星图' : '切换到夜航深色'}>
            <Button variant="ghost" size="icon-sm" onClick={toggleTheme} aria-label="切换主题">
              {theme === 'dark' ? <Sun /> : <Moon />}
            </Button>
          </Tip>
        </div>
      </div>
    </div>
  );
}
