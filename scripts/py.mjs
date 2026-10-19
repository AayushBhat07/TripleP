#!/usr/bin/env node
/**
 * Runs a Python command from backend/ with whichever interpreter this machine has: $PYTHON, then python3
 * (macOS, Linux), then python (Windows). Usage: node scripts/py.mjs -m pytest -q
 */

import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const backend = fileURLToPath(new URL('../backend', import.meta.url));
const candidates = [process.env.PYTHON, 'python3', 'python'].filter(Boolean);
const python = candidates.find(cmd => spawnSync(cmd, ['--version'], { stdio: 'ignore' }).status === 0);
if (!python) {
  console.error('Python not found. Install Python 3.11 or newer, or set PYTHON to its path.');
  process.exit(1);
}
const { status } = spawnSync(python, process.argv.slice(2), { cwd: backend, stdio: 'inherit' });
process.exit(status ?? 1);
