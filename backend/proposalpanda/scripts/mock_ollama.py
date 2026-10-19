"""
Stand-in for Ollama when you can't run real models (CI, cloud sandboxes, UI work).
Implements the two endpoints the app uses: GET /api/tags and POST /api/generate.

    npm run mock-ollama              # listens on :11434
    PORT=11500 npm run mock-ollama   # then set OLLAMA_BASE_URL=http://localhost:11500
    MOCK_DELAY_MS=800 npm run mock-ollama  # answer slowly, to watch the progress animation
"""

import json
import os
import re
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get('PORT') or 11434)
DELAY_SECONDS = int(os.environ.get('MOCK_DELAY_MS') or 0) / 1000
MODELS = (os.environ.get('MOCK_MODELS') or 'llama3:latest,qwen2.5:3b-instruct').split(',')


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == '/api/tags':
            self._send(200, {'models': [{'name': name, 'details': {'parameter_size': 'mock'}} for name in MODELS]})
        else:
            self._send(404, {'error': 'not found'})

    def do_POST(self):
        if self.path != '/api/generate':
            self._send(404, {'error': 'not found'})
            return
        length = int(self.headers.get('Content-Length') or 0)
        body = json.loads(self.rfile.read(length) or b'{}')
        model, prompt = body.get('model'), body.get('prompt') or ''
        if model not in MODELS:
            self._send(404, {'error': f'model "{model}" not found'})
            return
        match = re.search(r'(?:Task: |Write the ")([^\n"]+)', prompt)
        task = match.group(1) if match else 'request'
        time.sleep(DELAY_SECONDS)
        self._send(200, {
            'model': model,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'response': f'[mock {model}] Response for: {task}. ({len(prompt)} prompt chars received)',
            'done': True,
            'eval_count': 42,
        })
        print(f'[mock-ollama] {model} → {task}', flush=True)

    def log_message(self, *_):
        pass


if __name__ == '__main__':
    print(f"[mock-ollama] listening on http://localhost:{PORT} with {', '.join(MODELS)}", flush=True)
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
