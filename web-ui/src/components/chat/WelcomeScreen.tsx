import { BookOpen, Feather, Orbit, ScrollText } from 'lucide-react';
import { characterAssets, novaProfile } from '@/assets/characterAssets';
import { CharacterImage } from '@/components/character/NovaAvatar';
import { NovaStar } from '@/components/character/NovaStar';

const SUGGESTIONS = [
  { icon: Orbit, text: '今晚适合观星吗？' },
  { icon: BookOpen, text: '给我讲一个关于星座的小故事' },
  { icon: ScrollText, text: '你会怎样整理我们的记忆档案？' },
  { icon: Feather, text: '帮我写一段代码示例' },
];

/** 空会话时的角色欢迎界面 */
export function WelcomeScreen({ onPick, disabled }: { onPick: (text: string) => void; disabled?: boolean }) {
  return (
    <div className="mx-auto flex w-full max-w-[var(--nova-chat-max-width)] flex-col items-center px-4 py-8 text-center sm:py-12" data-testid="welcome">
      <div className="relative">
        <div className="absolute -inset-6 rounded-full opacity-60 blur-2xl nova-hair-gradient" aria-hidden />
        <div className="relative size-28 overflow-hidden rounded-full border-4 border-surface-strong shadow-panel sm:size-32">
          <CharacterImage slot={characterAssets.expressions.happy} className="size-full" fallbackSize={64} loading="eager" />
        </div>
        <span className="absolute -top-2 -right-3">
          <NovaStar size={34} orbit animated />
        </span>
      </div>
      <h2 className="mt-6 font-display text-4xl font-semibold italic">
        <span className="nova-gradient-text">Nova</span>
      </h2>
      <p className="mt-1 text-xs tracking-[0.2em] text-muted-foreground uppercase">{novaProfile.subtitle}</p>
      <div className="mt-3 inline-flex items-center gap-2 rounded-full border bg-surface px-4 py-1 text-sm">
        <NovaStar size={14} /> {novaProfile.identity}
      </div>
      <p className="mt-5 max-w-md text-[0.95rem] leading-relaxed">「{novaProfile.greeting}」</p>

      <div className="mt-8 grid w-full max-w-lg grid-cols-1 gap-2 sm:grid-cols-2">
        {SUGGESTIONS.map(({ icon: Icon, text }) => (
          <button
            key={text}
            type="button"
            disabled={disabled}
            onClick={() => onPick(text)}
            className="nova-hover-glow flex items-center gap-2.5 rounded-xl border bg-surface px-3.5 py-3 text-left text-sm hover:bg-surface-strong disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Icon className="size-4 shrink-0 text-primary" />
            <span>{text}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
