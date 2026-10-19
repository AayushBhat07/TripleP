/**
 * The tender's key dates as the analysis reports them (read from the NIT by backend/proposalpanda/engine/dates.py),
 * and how the screens show them.
 */

export type KeyDateKind = 'submission' | 'opening' | 'preBid' | 'queries';

export interface KeyDate {
  kind: KeyDateKind;
  /** yyyy-mm-dd */
  date: string;
  /** HH:MM, 24-hour, when the tender gives a time */
  time?: string;
  /** The tender's own words, for checking */
  printed: string;
}


export const KEY_DATE_LABELS: Record<KeyDateKind, string> = {
  submission: 'Bid due',
  opening: 'Bid opening',
  preBid: 'Pre-bid meeting',
  queries: 'Last date for queries',
};

/** Whole days from `today` to the date, negative once it has passed. */
export function daysUntil(date: string, today = new Date()): number {
  const [y, m, d] = date.split('-').map(Number);
  const target = Date.UTC(y, m - 1, d);
  const now = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
  return Math.round((target - now) / 86_400_000);
}

export function formatKeyDate(k: Pick<KeyDate, 'date' | 'time'>): string {
  const [y, m, d] = k.date.split('-').map(Number);
  const day = new Date(Date.UTC(y, m - 1, d)).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' });
  return k.time ? `${day}, ${k.time}` : day;
}

export function countdown(date: string, today = new Date()): string {
  const days = daysUntil(date, today);
  if (days < 0) return `${-days} ${days === -1 ? 'day' : 'days'} ago`;
  if (days === 0) return 'today';
  if (days === 1) return 'tomorrow';
  return `in ${days} days`;
}
