"""Reusable LLM access layer, shared by all LLM-backed features.

Callers should use the module-level :func:`chat` / :func:`is_configured`
helpers and stay provider-agnostic; the concrete provider is chosen by the
``LLM_PROVIDER`` setting.
"""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from judge.llm.base import Backend, LLMError, LLMResult

__all__ = ['Backend', 'LLMError', 'LLMResult', 'get_backend', 'chat', 'is_configured']

_backend = None


def get_backend() -> Backend:
    """Return the configured LLM backend (cached for the process)."""
    global _backend
    if _backend is None:
        provider = getattr(settings, 'LLM_PROVIDER', 'azure')
        if provider == 'azure':
            from judge.llm.azure import AzureBackend
            _backend = AzureBackend()
        elif provider == 'vllm':
            from judge.llm.vllm import VLLMBackend
            _backend = VLLMBackend()
        else:
            raise ImproperlyConfigured(f'Unknown LLM_PROVIDER: {provider!r}')
    return _backend


def chat(messages, **kwargs) -> LLMResult:
    return get_backend().chat(messages, **kwargs)


def is_configured() -> bool:
    return get_backend().is_configured()
