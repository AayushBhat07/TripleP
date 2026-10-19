"""The one Ollama client: Llama 3 for bids (AI_CONFIG.MODEL_NAME), Qwen for analysis (AI_CONFIG.ANALYSIS_MODEL)."""

import logging
import time

import httpx

from .config import AI_CONFIG

log = logging.getLogger('proposalpanda')


class LlmServiceError(Exception):
    """type: OLLAMA_UNAVAILABLE, TIMEOUT, INVALID_REQUEST, GENERATION_FAILED or NETWORK_ERROR."""

    def __init__(self, type: str, message: str):
        super().__init__(message)
        self.type = type
        self.message = message


async def check_health(model_name: str | None = None) -> dict:
    """Whether Ollama is up and has the model: {available, modelName?, version?, errorMessage?}."""
    model_name = model_name or AI_CONFIG.MODEL_NAME
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f'{AI_CONFIG.OLLAMA_BASE_URL}/api/tags')
        if response.status_code >= 400:
            return {'available': False, 'errorMessage': f'Ollama responded with status {response.status_code}'}
        models = response.json().get('models') or []
        # Ollama reports "llama3" as "llama3:latest"
        wanted = model_name if ':' in model_name else f'{model_name}:latest'
        target = next((m for m in models if m.get('name') == wanted), None)
        if not target:
            names = ', '.join(m.get('name', '') for m in models)
            return {
                'available': False,
                'errorMessage': f'Model {model_name} not found. Run `ollama pull {model_name}`. Available models: {names}',
            }
        return {'available': True, 'modelName': target['name'], 'version': (target.get('details') or {}).get('parameter_size') or 'unknown'}
    except httpx.TimeoutException:
        return {'available': False, 'errorMessage': 'Ollama health check timed out'}
    except (httpx.HTTPError, ValueError) as error:
        return {'available': False, 'errorMessage': str(error) or type(error).__name__}


async def generate(
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    retry_count: int = 0,
) -> dict:
    """One completion: {content, modelName, tokenCount, responseTimeMs, finishReason}. Raises LlmServiceError."""
    if not system_prompt or not user_prompt:
        raise LlmServiceError('INVALID_REQUEST', 'Both systemPrompt and userPrompt are required')
    defaults = AI_CONFIG.DEFAULT_INFERENCE
    payload = {
        'model': model or AI_CONFIG.MODEL_NAME,
        'prompt': f'{system_prompt}\n\n{user_prompt}',
        'stream': False,
        'options': {
            'temperature': defaults['temperature'] if temperature is None else temperature,
            'top_p': defaults['top_p'],
            'repeat_penalty': defaults['repeat_penalty'],
            'num_predict': defaults['max_tokens'] if max_tokens is None else max_tokens,
            'num_ctx': AI_CONFIG.CONTEXT_TOKENS,
        },
    }
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=AI_CONFIG.TIMEOUT_MS / 1000) as client:
            response = await client.post(f'{AI_CONFIG.OLLAMA_BASE_URL}/api/generate', json=payload)
    except httpx.TimeoutException:
        raise LlmServiceError('TIMEOUT', f'Generation timed out after {AI_CONFIG.TIMEOUT_MS}ms') from None
    except httpx.TransportError:
        if retry_count < AI_CONFIG.MAX_RETRIES:
            log.warning('Network error, retrying (%d/%d)...', retry_count + 1, AI_CONFIG.MAX_RETRIES)
            return await generate(system_prompt, user_prompt, model, temperature, max_tokens, retry_count + 1)
        raise LlmServiceError(
            'OLLAMA_UNAVAILABLE', f'Ollama service is not available. Ensure it is running at {AI_CONFIG.OLLAMA_BASE_URL}'
        ) from None
    if response.status_code >= 400:
        raise LlmServiceError(
            'GENERATION_FAILED', f'Ollama responded with status {response.status_code}: {response.reason_phrase}'
        )
    try:
        data = response.json()
    except ValueError as error:
        raise LlmServiceError('GENERATION_FAILED', str(error)) from None
    if not data.get('done'):
        raise LlmServiceError('GENERATION_FAILED', 'Generation was not completed')
    return {
        'content': (data.get('response') or '').strip(),
        'modelName': data.get('model'),
        'tokenCount': data.get('eval_count'),
        'responseTimeMs': int((time.monotonic() - start) * 1000),
        'finishReason': 'complete',
    }


async def generate_safe(**request) -> dict:
    """generate() that returns {success: True, data} or {success: False, error: {type, message}} instead of raising."""
    try:
        return {'success': True, 'data': await generate(**request)}
    except LlmServiceError as error:
        return {'success': False, 'error': {'type': error.type, 'message': error.message}}
    except Exception as error:  # noqa: BLE001
        return {'success': False, 'error': {'type': 'GENERATION_FAILED', 'message': str(error)}}
