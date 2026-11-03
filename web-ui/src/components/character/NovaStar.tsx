import { useId } from 'react';
import { cn } from '@/utils/cn';

/**
 * Nova 专属符号：四尖角、边缘内凹的四芒星，外圈细轨道环与小星球。
 * 纯 SVG，可任意缩放；颜色取自设计 Token。
 */
export function NovaStar({
  size = 32,
  orbit = false,
  animated = false,
  className,
  title,
}: {
  size?: number;
  orbit?: boolean;
  animated?: boolean;
  className?: string;
  title?: string;
}) {
  const id = useId().replace(/:/g, '');
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      className={cn('shrink-0', className)}
      role={title ? 'img' : undefined}
      aria-hidden={title ? undefined : true}
      aria-label={title}
    >
      <defs>
        <linearGradient id={`ns-g-${id}`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--nova-lavender-300)" />
          <stop offset="0.55" stopColor="var(--nova-violet-500)" />
          <stop offset="1" stopColor="var(--nova-sky-400)" />
        </linearGradient>
      </defs>
      {orbit && (
        <g className={animated ? 'nova-anim-orbit' : undefined}>
          <ellipse
            cx="32"
            cy="32"
            rx="27"
            ry="10"
            fill="none"
            stroke="var(--nova-lavender-300)"
            strokeWidth="1.6"
            opacity="0.85"
            transform="rotate(-24 32 32)"
          />
          <circle cx="56" cy="22" r="3.4" fill="var(--nova-sky-400)" />
        </g>
      )}
      <path
        d="M32 6 C34 23 41 30 58 32 C41 34 34 41 32 58 C30 41 23 34 6 32 C23 30 30 23 32 6Z"
        fill={`url(#ns-g-${id})`}
      />
      <path
        d="M32 21 C33 28 36 31 43 32 C36 33 33 36 32 43 C31 36 28 33 21 32 C28 31 31 28 32 21Z"
        fill="#fff"
        opacity="0.85"
      />
    </svg>
  );
}
