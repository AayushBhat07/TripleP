"""
Settings read from the environment (see .env.example). The repository's .env.local and .env are read too, the way
Next.js reads them, so the web app and the API share one settings file. Variables already set win.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_env_files() -> None:
    for name in ('.env.local', '.env'):
        path = ROOT / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding='utf-8').splitlines():
            key, sep, value = line.strip().partition('=')
            if sep and key and not key.startswith('#') and key not in os.environ and value.strip():
                os.environ[key.strip()] = value.strip().strip('"\'')


_load_env_files()


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, '')) or default
    except ValueError:
        return default


def production() -> bool:
    return os.environ.get('NODE_ENV') == 'production' or os.environ.get('APP_ENV') == 'production'


class AI_CONFIG:
    """Local model configuration (Ollama).
    - MODEL_NAME: long-form generation (foundation bids)
    - ANALYSIS_MODEL: tender summarisation during analysis
    """

    OLLAMA_BASE_URL = os.environ.get('OLLAMA_BASE_URL') or 'http://localhost:11434'
    MODEL_NAME = os.environ.get('OLLAMA_BID_MODEL') or 'llama3:latest'
    ANALYSIS_MODEL = os.environ.get('OLLAMA_ANALYSIS_MODEL') or 'qwen2.5:3b-instruct'
    CONTEXT_TOKENS = _int('OLLAMA_NUM_CTX', 8192)
    TIMEOUT_MS = _int('OLLAMA_TIMEOUT_MS', 120000)  # local CPU inference is slow
    MAX_RETRIES = 1
    DEFAULT_INFERENCE = {'temperature': 0.2, 'top_p': 0.9, 'repeat_penalty': 1.1, 'max_tokens': 2048}


def database_path() -> str:
    """DATABASE_PATH, relative to the repository root unless absolute (':memory:' for tests)."""
    path = os.environ.get('DATABASE_PATH') or 'data/proposalpanda.db'
    return path if path == ':memory:' else str(ROOT / path)
