const pad = (n: number) => String(n).padStart(2, '0');

function parse(iso: string | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** 消息时间戳：今天显示 HH:mm，其它日期显示 M月D日 HH:mm */
export function formatMessageTime(iso: string, now: Date = new Date()): string {
  const d = parse(iso);
  if (!d) return '';
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) return hm;
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}月${d.getDate()}日 ${hm}`;
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} ${hm}`;
}

/** 相对时间：刚刚 / N 分钟前 / N 小时前 / 昨天 / 日期 */
export function formatRelative(iso: string | undefined, now: Date = new Date()): string {
  const d = parse(iso);
  if (!d) return '—';
  const diff = (now.getTime() - d.getTime()) / 1000;
  if (diff < 0) return formatMessageTime(iso!, now);
  if (diff < 60) return '刚刚';
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400 && d.toDateString() === now.toDateString()) return `${Math.floor(diff / 3600)} 小时前`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return '昨天';
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}月${d.getDate()}日`;
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
}

export function formatFullDate(iso: string | undefined): string {
  const d = parse(iso);
  if (!d) return '—';
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
