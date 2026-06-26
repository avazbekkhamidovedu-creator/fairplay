import logging
import time as _time

from django.conf import settings
from openai import AzureOpenAI

from judge.llm.base import Backend, LLMError, LLMResult

logger = logging.getLogger(__name__)


class AzureBackend(Backend):
    """Azure OpenAI backend (the openai SDK's AzureOpenAI client)."""

    def __init__(self):
        self._client = None

    def is_configured(self) -> bool:
        return bool(getattr(settings, 'AZURE_OPENAI_ENDPOINT', None))

    def _get_client(self) -> AzureOpenAI:
        # The SDK's default timeout is 600s, which would pin a gunicorn worker for
        # the whole request; cap it so a slow Azure endpoint fails fast instead.
        if self._client is None:
            self._client = AzureOpenAI(
                azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
                api_key=settings.AZURE_OPENAI_API_KEY,
                api_version=getattr(settings, 'AZURE_OPENAI_API_VERSION', '2024-02-01'),
                timeout=getattr(settings, 'AZURE_OPENAI_TIMEOUT', 30),
                max_retries=1,
            )
        return self._client

    def chat(self, messages, *, model=None, max_tokens=None) -> LLMResult:
        """Call Azure OpenAI and return an LLMResult. Raises on failure."""
        client = self._get_client()
        model = model or settings.AZURE_OPENAI_DEPLOYMENT
        if max_tokens is None:
            max_tokens = getattr(settings, 'AZURE_OPENAI_MAX_TOKENS', 2048)

        start = _time.monotonic()
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_completion_tokens=max_tokens,
        )
        elapsed_ms = int((_time.monotonic() - start) * 1000)

        logger.debug('LLM response: id=%s, model=%s, finish_reason=%s',
                     response.id, response.model,
                     response.choices[0].finish_reason if response.choices else 'N/A')

        content = response.choices[0].message.content
        if not content or not content.strip():
            raise LLMError('AI service returned an empty response. Please try again.')

        usage = response.usage
        return LLMResult(
            text=content.strip(),
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
            model_name=response.model or '',
            response_time_ms=elapsed_ms,
        )
