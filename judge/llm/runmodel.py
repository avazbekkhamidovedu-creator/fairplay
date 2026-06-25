import logging
import time as _time

import requests
from django.conf import settings

from judge.llm.base import Backend, LLMError, LLMResult

logger = logging.getLogger(__name__)


class RunmodelBackend(Backend):
    """Backend for runmodel's gateway which wraps vLLM with a {chat_id, response} envelope."""

    def is_configured(self) -> bool:
        result = bool(getattr(settings, 'RUNMODEL_API_BASE', None))
        if settings.DEBUG:
            print(f'llm/runmodel.py | is_configured={result}, '
                  f'RUNMODEL_API_BASE={getattr(settings, "RUNMODEL_API_BASE", None)!r}')
        return result

    def chat(self, messages, *, model=None, max_tokens=None, extra_payload=None) -> LLMResult:
        api_base = settings.RUNMODEL_API_BASE.rstrip('/')
        if not api_base.startswith('http://') and not api_base.startswith('https://'):
            api_base = 'http://' + api_base
            if settings.DEBUG:
                print(f'llm/runmodel.py | prepended http:// → {api_base}')
        model = model or settings.RUNMODEL_MODEL
        if max_tokens is None:
            max_tokens = getattr(settings, 'RUNMODEL_MAX_TOKENS', 4096)

        payload = {
            'model': model,
            'messages': messages,
            'max_completion_tokens': max_tokens,
        }
        if extra_payload:
            payload.update(extra_payload)

        headers = {'Content-Type': 'application/json'}
        api_key = getattr(settings, 'RUNMODEL_API_KEY', '')
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'

        timeout = getattr(settings, 'RUNMODEL_TIMEOUT', 120)

        start = _time.monotonic()
        try:
            resp = requests.post(
                f'{api_base}/v1/chat/completions',
                json=payload,
                headers=headers,
                timeout=timeout,
                verify=False,
            )
            resp.raise_for_status()
            body = resp.json()
        except requests.RequestException as e:
            raise LLMError(f'runmodel API request failed: {e}') from e
        except ValueError as e:
            raise LLMError(f'runmodel returned invalid JSON: {e}') from e

        elapsed_ms = int((_time.monotonic() - start) * 1000)

        wrapped = body.get('response', body)
        if getattr(settings, 'DEBUG'):
            print(f'llm/runmodel.py | chat_id={body.get("chat_id")!r}, '
                  f'model={wrapped.get("model")!r}, '
                  f'choices_count={len(wrapped.get("choices", []))}, '
                  f'usage={wrapped.get("usage")}')

        response = wrapped

        logger.debug('LLM response: id=%s, model=%s',
                     response.get('id'), response.get('model'))

        choices = response.get('choices', [])
        if not choices:
            raise LLMError('AI service returned no choices.')

        content = choices[0].get('message', {}).get('content')
        if not content or not content.strip():
            raise LLMError('AI service returned an empty response. Please try again.')

        usage = response.get('usage')
        return LLMResult(
            text=content.strip(),
            prompt_tokens=usage.get('prompt_tokens') if usage else None,
            completion_tokens=usage.get('completion_tokens') if usage else None,
            total_tokens=usage.get('total_tokens') if usage else None,
            model_name=response.get('model', ''),
            response_time_ms=elapsed_ms,
        )
