/**
 * Reads the NDJSON progress streams the backend sends (see events.ts for the wire format; the server side is
 * backend/proposalpanda/progress.py).
 */

import type { ProgressEvent, StreamLine } from './events';

/**
 * Reads a progress stream, calling onEvent for each progress line, and resolves with the result.
 * Throws Error(message) on an error line, or on a non-OK response with a JSON error body.
 */
export async function readProgressStream<T>(response: Response, onEvent: (event: ProgressEvent) => void): Promise<T> {
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.details || data.error || `Request failed (${response.status}).`);
  }
  if (!response.body) throw new Error('The server sent no response.');

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (value) buffer += value;
    const lines = buffer.split('\n');
    buffer = done ? '' : lines.pop()!;
    for (const raw of lines) {
      if (!raw.trim()) continue;
      const line = JSON.parse(raw) as StreamLine<T>;
      if (line.type === 'progress') onEvent(line);
      else if (line.type === 'result') return line.data;
      else throw new Error(line.error);
    }
    if (done) throw new Error('The connection closed before the result arrived.');
  }
}
