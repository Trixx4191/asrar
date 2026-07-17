import asyncio
import os
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'backend' / 'core'))

from core.agent import run_task
from core import orchestrator


class DummyProvider:
    def is_available(self):
        return True


class ProviderFallbackTest(unittest.TestCase):
    def setUp(self):
        orchestrator.reset_circuit("groq")
        orchestrator.reset_circuit("google")

    def test_run_task_fallbacks_on_provider_error(self):
        async def fake_llm_call_with_tools(provider, model_id, messages, system, provider_name):
            if provider_name == 'groq':
                return 'Provider HTTP 429: rate limited', []
            return 'fallback success', []

        with patch.dict(os.environ, {'GROQ_API_KEY': 'test', 'GOOGLE_API_KEY': 'test'}), \
             patch('core.agent.get_provider', return_value=DummyProvider()), \
             patch('core.agent._llm_call_with_tools', new=fake_llm_call_with_tools), \
             patch('core.orchestrator.retry_delay_seconds', return_value=0):
            response = asyncio.run(run_task('Write a short Python script', history=[], force_model=None, conversation_id=None))

        self.assertEqual(response['response'], 'fallback success')
        self.assertEqual(response['tool_calls'], [])
        self.assertIn('Fallbacked', response['routing_reason'])


if __name__ == '__main__':
    unittest.main()
