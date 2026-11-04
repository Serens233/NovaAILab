import { memo } from 'react';

/** 固定位置的少量星光与轨道装饰。pointer-events: none，不会遮挡任何交互。 */
const STARS = [
  { x: 6, y: 12, s: 10, d: 0 },
  { x: 22, y: 78, s: 7, d: 1.2 },
  { x: 38, y: 8, s: 6, d: 2.1 },
  { x: 57, y: 92, s: 8, d: 0.6 },
  { x: 71, y: 16, s: 11, d: 1.8 },
  { x: 86, y: 64, s: 7, d: 2.6 },
  { x: 94, y: 30, s: 6, d: 0.9 },
  { x: 48, y: 46, s: 5, d: 3.1 },
  { x: 14, y: 46, s: 5, d: 2.4 },
];

function Spark({ size }: { size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 10 10" aria-hidden>
      <path d="M5 0 C5.4 3.6 6.4 4.6 10 5 C6.4 5.4 5.4 6.4 5 10 C4.6 6.4 3.6 5.4 0 5 C3.6 4.6 4.6 3.6 5 0Z" fill="var(--nova-star-color)" />
    </svg>
  );
}

export const StarfieldBackground = memo(function StarfieldBackground() {
  return (
    <div
      aria-hidden
      className="pointer-events-none fixed inset-0 z-0 overflow-hidden"
      style={{ background: 'var(--nova-bg-gradient)' }}
    >
      <svg className="absolute -top-40 -right-40 h-[520px] w-[520px] opacity-40" viewBox="0 0 200 200">
        <circle cx="100" cy="100" r="80" fill="none" stroke="var(--nova-lavender-300)" strokeWidth="0.4" />
        <circle cx="100" cy="100" r="60" fill="none" stroke="var(--nova-lavender-300)" strokeWidth="0.3" strokeDasharray="1 3" />
        <circle cx="180" cy="100" r="2.4" fill="var(--nova-sky-400)" />
      </svg>
      <svg className="absolute -bottom-52 -left-40 h-[560px] w-[560px] opacity-30" viewBox="0 0 200 200">
        <ellipse cx="100" cy="100" rx="90" ry="34" fill="none" stroke="var(--nova-violet-500)" strokeWidth="0.35" transform="rotate(-20 100 100)" />
      </svg>
      {STARS.map((st, i) => (
        <span
          key={i}
          className="nova-anim-twinkle absolute"
          style={{ left: `${st.x}%`, top: `${st.y}%`, animationDelay: `${st.d}s` }}
        >
          <Spark size={st.s} />
        </span>
      ))}
    </div>
  );
});
