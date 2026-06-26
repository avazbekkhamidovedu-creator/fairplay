"""Tests for the reusable LLM access layer (judge.llm)."""
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from judge import llm
from judge.llm import LLMError, LLMResult
from judge.llm.azure import AzureBackend

AZURE_SETTINGS = {
    'LLM_PROVIDER': 'azure',
    'AZURE_OPENAI_ENDPOINT': 'https://example.openai.azure.com/',
    'AZURE_OPENAI_API_KEY': 'test-key',
    'AZURE_OPENAI_DEPLOYMENT': 'gpt-test',
}


def _fake_response(content='Hello.', prompt=10, completion=5, total=15, model='gpt-test'):
    response = mock.MagicMock()
    response.id = 'resp-1'
    response.model = model
    choice = mock.MagicMock()
    choice.message.content = content
    choice.finish_reason = 'stop'
    response.choices = [choice]
    response.usage.prompt_tokens = prompt
    response.usage.completion_tokens = completion
    response.usage.total_tokens = total
    return response


@override_settings(**AZURE_SETTINGS)
class AzureBackendTestCase(SimpleTestCase):
    def test_chat_returns_result(self):
        backend = AzureBackend()
        with mock.patch('judge.llm.azure.AzureOpenAI') as client_cls:
            create = client_cls.return_value.chat.completions.create
            create.return_value = _fake_response('  Use a set.  ')
            result = backend.chat([{'role': 'user', 'content': 'hi'}])

        self.assertIsInstance(result, LLMResult)
        self.assertEqual(result.text, 'Use a set.')  # stripped
        self.assertEqual(result.prompt_tokens, 10)
        self.assertEqual(result.completion_tokens, 5)
        self.assertEqual(result.total_tokens, 15)
        self.assertEqual(result.model_name, 'gpt-test')
        self.assertIsNotNone(result.response_time_ms)

        # The deployment name and the caller's messages are passed through.
        _, kwargs = create.call_args
        self.assertEqual(kwargs['model'], 'gpt-test')
        self.assertEqual(kwargs['messages'], [{'role': 'user', 'content': 'hi'}])

    def test_chat_empty_response_raises(self):
        backend = AzureBackend()
        with mock.patch('judge.llm.azure.AzureOpenAI') as client_cls:
            client_cls.return_value.chat.completions.create.return_value = _fake_response('   ')
            with self.assertRaises(LLMError):
                backend.chat([{'role': 'user', 'content': 'hi'}])

    def test_is_configured_true_with_endpoint(self):
        self.assertTrue(AzureBackend().is_configured())

    @override_settings(AZURE_OPENAI_ENDPOINT=None)
    def test_is_configured_false_without_endpoint(self):
        self.assertFalse(AzureBackend().is_configured())


class BackendDispatchTestCase(SimpleTestCase):
    def setUp(self):
        llm._backend = None
        self.addCleanup(setattr, llm, '_backend', None)

    @override_settings(LLM_PROVIDER='azure')
    def test_azure_provider_selected(self):
        self.assertIsInstance(llm.get_backend(), AzureBackend)

    @override_settings(LLM_PROVIDER='bogus')
    def test_unknown_provider_raises(self):
        with self.assertRaises(ImproperlyConfigured):
            llm.get_backend()
