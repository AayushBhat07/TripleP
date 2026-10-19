import { test } from 'node:test';
import assert from 'node:assert/strict';
import { countdown, daysUntil, formatKeyDate } from '../lib/tenders/dates';

test('countdowns', () => {
  const today = new Date(2026, 9, 16);
  assert.equal(daysUntil('2026-10-20', today), 4);
  assert.equal(countdown('2026-10-20', today), 'in 4 days');
  assert.equal(countdown('2026-10-17', today), 'tomorrow');
  assert.equal(countdown('2026-09-01', today), '45 days ago');
  assert.match(formatKeyDate({ date: '2026-09-01', time: '15:00' }), /^01 Sep\w* 2026, 15:00$/);
});
