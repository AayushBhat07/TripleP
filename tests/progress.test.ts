import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readProgressStream } from '../lib/progress/stream';
import type { ProgressEvent } from '../lib/progress/events';

/** A Response whose body arrives in the given pieces. */
const chunked = (pieces: string[], init?: ResponseInit) =>
  new Response(
    new ReadableStream({
      start(c) {
        pieces.forEach(p => c.enqueue(new TextEncoder().encode(p)));
        c.close();
      },
    }),
    init
  );

test('progress lines then the result', async () => {
  const lines = [
    { type: 'progress', step: 'extract', status: 'started', label: 'Text extraction', at: 0 },
    { type: 'progress', step: 'extract', status: 'done', detail: '10 words read', at: 5 },
    { type: 'result', data: { ok: 1 } },
  ];
  const events: ProgressEvent[] = [];
  const data = await readProgressStream<{ ok: number }>(chunked(lines.map(l => JSON.stringify(l) + '\n')), e => events.push(e));
  assert.deepEqual(data, { ok: 1 });
  assert.deepEqual(events.map(({ type, step, status }) => [type, step, status]), [
    ['progress', 'extract', 'started'],
    ['progress', 'extract', 'done'],
  ]);
  assert.equal(events[1].detail, '10 words read');
});

test('reader handles lines split across chunks', async () => {
  const lines = [
    { type: 'progress', step: 'dates', status: 'started', at: 1 },
    { type: 'progress', step: 'dates', status: 'done', detail: 'Completion period: 18 months', at: 2 },
    { type: 'result', data: { text: 'héllo ₹' } },
  ]
    .map(l => JSON.stringify(l) + '\n')
    .join('');
  const bytes = new TextEncoder().encode(lines);
  // Split every 7 bytes so lines and multi-byte characters straddle chunks.
  const response = new Response(
    new ReadableStream({
      start(c) {
        for (let i = 0; i < bytes.length; i += 7) c.enqueue(bytes.slice(i, i + 7));
        c.close();
      },
    })
  );
  const events: ProgressEvent[] = [];
  assert.deepEqual(await readProgressStream(response, e => events.push(e)), { text: 'héllo ₹' });
  assert.deepEqual(events.map(e => e.status), ['started', 'done']);
});

test('an error line becomes a thrown Error with its message', async () => {
  await assert.rejects(
    readProgressStream(chunked(['{"type":"progress","step":"legal","status":"started","at":0}\n{"type":"error","error":"Nope","status":422}\n']), () => {}),
    { message: 'Nope' }
  );
});

test('a non-OK JSON response throws its error', async () => {
  const response = Response.json({ error: 'No file provided.' }, { status: 400 });
  await assert.rejects(readProgressStream(response, () => {}), { message: 'No file provided.' });
  await assert.rejects(readProgressStream(chunked(['{"type":"progress","step":"legal","status":"started","at":0}\n']), () => {}), /closed before/);
});
