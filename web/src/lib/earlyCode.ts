export const OPENS_BEFORE_MS = 30 * 60 * 1000;

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

export function startsMessage(startsAt: Date, now: Date, locale?: string | string[]): string {
  const time = startsAt.toLocaleTimeString(locale, { hour: "numeric", minute: "2-digit" });
  if (sameDay(startsAt, now)) return `Your exam starts at ${time}. Come back 30 minutes before.`;
  const day = startsAt.toLocaleDateString(locale, { weekday: "short", month: "short", day: "numeric" });
  return `Your exam starts ${day} at ${time}. Come back 30 minutes before.`;
}

export function opensInMs(startsAt: Date, now: Date): number {
  return Math.max(0, startsAt.getTime() - OPENS_BEFORE_MS - now.getTime());
}

export function countdownText(ms: number): string {
  const total = Math.ceil(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}
