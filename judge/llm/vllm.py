import logging
import time as _time

import httpx
from django.conf import settings
from openai import OpenAI

from judge.llm.base import Backend, LLMError, LLMResult

logger = logging.getLogger(__name__)


class VLLMBackend(Backend):
    """Backend for a vLLM gateway that wraps responses in a {chat_id, response} envelope.

    The gateway sits in front of vLLM and enriches every reply with extra fields
    (e.g. ``chat_id``).  The effective JSON looks like::

        {
            "chat_id": "new#14",
            "response": {
                "id": "chatcmpl-...",
                "model": "qwen3.5-27B-FP8",
                "choices": [ { "message": { "content": "..." }, ... } ],
                "usage": { "prompt_tokens": ..., ... }
            }
        }

    We use ``openai.OpenAI.with_raw_response`` to obtain the full HTTP body,
    unwrap the outer envelope by hand, and then read the standard fields from
    ``response``.
    """

    def is_configured(self) -> bool:
        result = bool(getattr(settings, 'VLLM_API_BASE', None))
        logger.debug('is_configured=%s, VLLM_API_BASE=%r', result,
                     getattr(settings, 'VLLM_API_BASE', None))
        return result

    def _build_client(self) -> OpenAI:
        api_base = settings.VLLM_API_BASE.rstrip('/')
        if not api_base.startswith('http://') and not api_base.startswith('https://'):
            api_base = 'http://' + api_base
            logger.debug('prepended http:// -> api_base=%s', api_base)

        api_key = getattr(settings, 'VLLM_API_KEY', '') or 'sk-no-key-required'
        timeout = getattr(settings, 'VLLM_TIMEOUT', 120)

        logger.debug('building OpenAI client: base_url=%s, timeout=%s', api_base, timeout)
        return OpenAI(
            base_url=api_base,
            api_key=api_key,
            http_client=httpx.Client(verify=False),
            timeout=timeout,
        )

    def chat(self, messages, *, model=None, max_tokens=None, extra_payload=None) -> LLMResult:
        logger.debug('chat() called: messages_count=%d, model=%s, max_tokens=%s, extra_payload=%s',
                     len(messages), model, max_tokens, extra_payload)

        client = self._build_client()
        model = model or settings.VLLM_MODEL
        if max_tokens is None:
            max_tokens = getattr(settings, 'VLLM_MAX_TOKENS', 4096)

        logger.debug('sending request: model=%s, max_completion_tokens=%s, extra_body=%s',
                     model, max_tokens, extra_payload or {})

        start = _time.monotonic()
        try:
            # Use with_raw_response to get the full HTTP body so we can manually
            # unwrap the gateway envelope (see class docstring).
            raw = client.with_raw_response.chat.completions.create(
                model=model,
                messages=messages,
                max_completion_tokens=max_tokens,
                extra_body=extra_payload or {},
            )
            body = raw.http_response.json()
            logger.debug('raw HTTP response received (%.2fs), JSON parsed OK',
                         _time.monotonic() - start)
        except Exception as e:
            logger.error('API request failed after %.2fs: %s', _time.monotonic() - start, e)
            raise LLMError(f'vLLM API request failed: {e}') from e

        elapsed_ms = int((_time.monotonic() - start) * 1000)

        # --- Unwrap gateway envelope -------------------------------------------
        # The gateway returns {"chat_id": "...", "response": { ... }}.
        # All standard OpenAI fields live inside the "response" key.
        # -----------------------------------------------------------------------
        # Add more envelope fields below as needed, e.g.:
        #   chat_id = body.get("chat_id")
        #   trace_id = body.get("trace_id")
        #   custom_meta = body.get("custom_meta")
        # -----------------------------------------------------------------------
        inner = body.get('response', body)

        logger.debug('envelope: chat_id=%s, inner_keys=%s, model=%s, choices=%d, usage=%s',
                     body.get('chat_id'),
                     list(inner.keys()) if isinstance(inner, dict) else type(inner).__name__,
                     inner.get('model'),
                     len(inner.get('choices', [])),
                     inner.get('usage'))

        choices = inner.get('choices', [])
        if not choices:
            logger.error('no choices in response: %s', inner)
            raise LLMError('AI service returned no choices.')

        content = choices[0].get('message', {}).get('content')
        if not content or not content.strip():
            logger.error('empty content in response: message=%s',
                         choices[0].get('message'))
            raise LLMError('AI service returned an empty response. Please try again.')

        usage = inner.get('usage')
        logger.debug('success: elapsed=%dms, text_length=%d, prompt_tokens=%s, '
                     'completion_tokens=%s, total_tokens=%s',
                     elapsed_ms, len(content),
                     usage.get('prompt_tokens') if usage else None,
                     usage.get('completion_tokens') if usage else None,
                     usage.get('total_tokens') if usage else None)

        return LLMResult(
            text=content.strip(),
            prompt_tokens=usage.get('prompt_tokens') if usage else None,
            completion_tokens=usage.get('completion_tokens') if usage else None,
            total_tokens=usage.get('total_tokens') if usage else None,
            model_name=inner.get('model', ''),
            response_time_ms=elapsed_ms,
        )
