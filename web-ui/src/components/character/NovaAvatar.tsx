import { useState } from 'react';
import { characterAssets, type CharacterAssetSlot } from '@/assets/characterAssets';
import { cn } from '@/utils/cn';
import { NovaStar } from './NovaStar';

const SIZE = { xs: 'size-7', sm: 'size-9', md: 'size-11', lg: 'size-16', xl: 'size-24' } as const;

/** 角色图片：加载失败或未配置时回退到四芒星徽记 */
export function CharacterImage({
  slot,
  className,
  fallbackSize = 40,
  loading = 'lazy',
}: {
  slot: CharacterAssetSlot;
  className?: string;
  fallbackSize?: number;
  loading?: 'lazy' | 'eager';
}) {
  const [failed, setFailed] = useState(false);
  if (!slot.src || failed) {
    return (
      <div
        className={cn('flex items-center justify-center bg-primary-soft', className)}
        role="img"
        aria-label={`${slot.alt}（素材占位）`}
      >
        <NovaStar size={fallbackSize} orbit />
      </div>
    );
  }
  return (
    <img
      src={slot.src}
      alt={slot.alt}
      loading={loading}
      decoding="async"
      draggable={false}
      onError={() => setFailed(true)}
      className={cn('object-cover', className)}
    />
  );
}

export function NovaAvatar({
  size = 'md',
  className,
  status,
  slot = characterAssets.avatar,
}: {
  size?: keyof typeof SIZE;
  className?: string;
  status?: 'online' | 'degraded' | 'offline' | 'checking';
  slot?: CharacterAssetSlot;
}) {
  const dot =
    status === 'online'
      ? 'bg-success'
      : status === 'degraded'
        ? 'bg-warning'
        : status === 'offline'
          ? 'bg-danger'
          : 'bg-muted-foreground';
  return (
    <div className={cn('relative shrink-0', SIZE[size], className)}>
      <div className="size-full overflow-hidden rounded-full bg-primary-soft ring-2 ring-accent/70 ring-offset-1 ring-offset-background">
        <CharacterImage slot={slot} className="size-full" fallbackSize={size === 'xl' ? 56 : 22} loading="eager" />
      </div>
      {status && (
        <span
          className={cn('absolute right-0 bottom-0 size-3 rounded-full border-2 border-background', dot)}
          aria-hidden
        />
      )}
    </div>
  );
}
