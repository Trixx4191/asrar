"""Registration must produce callable provider connections, not just JSON rows."""

import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core import registry, router, agent, orchestrator
from providers import get_provider, configuration
from providers.base import ProviderResponse
from providers.transport import provider_error
from providers.openai_compat import openai_stream
from providers.openai_compat import openai_complete


class ModelRegistrationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.tmp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.path = Path(self.tmp)
        self.config_path = self.path / "models.json"
        self.env_path = self.path / ".env"
        self.config_path.write_text(json.dumps({"models": {}, "task_model_map": {}}))
        self.env_path.write_text("# User configuration\nEXISTING_API_KEY=existing-test-secret\n")
        self.stack.enter_context(patch.object(registry, "CONFIG_PATH", self.config_path))
        self.stack.enter_context(patch.object(router, "CONFIG_PATH", self.config_path))
        self.stack.enter_context(patch.object(configuration, "ENV_PATH", self.env_path))
        self.stack.enter_context(patch.dict(os.environ, {}, clear=True))

    def tearDown(self):
        self.stack.close()

    def add(self, **overrides):
        args = {"key": "demo", "model_id": "model-1", "provider": "customtest",
                "display_name": "Demo", "strengths": ["coding"], "priority": 5,
                "protocol": "openai_compatible", "api_base": "https://provider.example/v1"}
        args.update(overrides)
        return registry.add_model(**args)

    async def test_custom_provider_adds_connection_routing_and_empty_key_placeholder(self):
        entry = self.add()
        self.assertEqual(entry["api_key_env"], "CUSTOMTEST_API_KEY")
        self.assertEqual(entry["protocol"], "openai_compatible")
        self.assertIn("EXISTING_API_KEY=existing-test-secret", self.env_path.read_text())
        self.assertIn("CUSTOMTEST_API_KEY=\n", self.env_path.read_text())
        saved = registry.load_registry()
        self.assertEqual(saved["task_model_map"]["coding"], ["demo"])
        self.assertNotIn("existing-test-secret", json.dumps(saved))
        self.assertNotIn("demo", router.get_available_models(saved))
        self.assertEqual((await registry.test_model("demo"))["status"], "missing_credentials")

    async def test_endpoint_and_credential_are_specific_to_each_model(self):
        first = self.add(api_key_env="FIRST_API_KEY")
        second = self.add(key="second", api_base="https://another.example/v2", api_key_env="SECOND_API_KEY")
        self.env_path.write_text("FIRST_API_KEY=first-test-secret\nSECOND_API_KEY=second-test-secret\n")
        one = get_provider("customtest", model=first)
        two = get_provider("customtest", model=second)
        self.assertEqual(one.base_url, "https://provider.example/v1")
        self.assertEqual(two.base_url, "https://another.example/v2")
        self.assertEqual(one.api_key, "first-test-secret")
        self.assertEqual(two.api_key, "second-test-secret")
        self.assertEqual(router.get_available_models(registry.load_registry()), {"demo", "second"})

    async def test_updated_file_key_overrides_old_environment_and_resets_breaker(self):
        model = self.add()
        self.env_path.write_text("CUSTOMTEST_API_KEY=old-test-secret\n")
        old = get_provider("customtest", model=model)
        for _ in range(3):
            orchestrator.record_failure("customtest")
        self.assertFalse(orchestrator.is_available("customtest"))
        os.environ["CUSTOMTEST_API_KEY"] = "old-test-secret"
        self.env_path.write_text("CUSTOMTEST_API_KEY=fresh-test-secret-longer\n")
        new = get_provider("customtest", model=model)
        self.assertEqual(old.api_key, "old-test-secret")
        self.assertEqual(new.api_key, "fresh-test-secret-longer")
        self.assertTrue(orchestrator.is_available("customtest"))
        self.env_path.write_text("# Key removed locally\n")
        self.assertFalse(get_provider("customtest", model=model).is_available())

    async def test_openrouter_url_keeps_vendor_and_never_uses_vendor_native_auth(self):
        with patch.object(registry, "_fetch_openrouter_info", new=AsyncMock(return_value=None)):
            entry = await registry.lookup_and_register_model(
                "https://openrouter.ai/anthropic/claude-example", validate=False)
        self.assertEqual(entry["id"], "anthropic/claude-example")
        self.assertEqual(entry["provider"], "openrouter")
        self.assertEqual(entry["api_key_env"], "OPENROUTER_API_KEY")

    async def test_native_model_bypasses_gateway_catalog(self):
        with patch.object(registry, "_fetch_openrouter_info", new_callable=AsyncMock) as fetch:
            entry = await registry.lookup_and_register_model("claude-example", validate=False)
        fetch.assert_not_awaited()
        self.assertEqual(entry["provider"], "anthropic")
        self.assertEqual(entry["protocol"], "anthropic")
        self.assertEqual(entry["api_key_env"], "ANTHROPIC_API_KEY")

    async def test_registration_is_idempotent_for_same_model(self):
        one = await registry.lookup_and_register_model("claude-example", validate=False)
        two = await registry.lookup_and_register_model("claude-example", validate=False)
        self.assertEqual(one["key"], two["key"])
        self.assertEqual(len(registry.list_models()), 1)
        self.assertEqual(self.env_path.read_text().count("ANTHROPIC_API_KEY="), 1)

    async def test_registration_reuses_existing_model_alias(self):
        self.add(key="my-existing-alias", model_id="claude-example", provider="anthropic",
                 protocol="anthropic", api_base="https://api.anthropic.com/v1")
        entry = await registry.lookup_and_register_model("claude-example", validate=False)
        self.assertEqual(entry["key"], "my-existing-alias")
        self.assertEqual(len(registry.list_models()), 1)

    async def test_key_change_marks_previous_validation_stale(self):
        self.add()
        with registry._REGISTRY_LOCK:
            saved = registry.load_registry()
            saved["models"]["demo"]["validation"] = {
                "ready": True, "status": "ready", "tested_at": "2000-01-01T00:00:00"}
            registry.save_registry(saved)
        listed = registry.list_models()[0]
        self.assertEqual(listed["validation"]["status"], "stale")
        self.assertFalse(listed["validation"]["ready"])

    async def test_unknown_provider_requires_actual_protocol_and_endpoint(self):
        with self.assertRaises(ValueError):
            self.add(protocol=None)
        self.assertEqual(registry.list_models(), [])

    async def test_invalid_configuration_does_not_modify_registry_or_env(self):
        before = self.env_path.read_text()
        for changes in ({"api_key_env": "sk-ant-secret-not-a-variable"},
                        {"api_base": "https://user:secret@example.test/v1"},
                        {"api_base": "https://example.test/v1?key=secret"},
                        {"protocol": "invented_api"}, {"key": "../escape"},
                        {"strengths": ["invented_task"]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.add(**changes)
        self.assertEqual(registry.list_models(), [])
        self.assertEqual(self.env_path.read_text(), before)

    async def test_native_provider_switch_does_not_reuse_old_endpoint_or_key(self):
        self.add()
        model = registry.configure_model("demo", provider="anthropic", model_id="claude-example")
        self.assertEqual(model["api_base"], "https://api.anthropic.com/v1")
        self.assertEqual(model["api_key_env"], "ANTHROPIC_API_KEY")
        self.assertEqual(model["protocol"], "anthropic")

    async def test_legacy_strength_tags_do_not_block_provider_repairs(self):
        self.add()
        saved = registry.load_registry()
        saved["models"]["demo"]["strengths"] = ["coding", "debugging", "chat"]
        registry.save_registry(saved)
        entry = registry.configure_model("demo", priority=6)
        self.assertEqual(entry["strengths"], ["coding", "fast_chat"])
        self.assertIn("debugging", entry["capability_tags"])

    async def test_removal_cleans_task_map(self):
        self.add()
        registry.remove_model("demo")
        self.assertEqual(registry.load_registry()["task_model_map"]["coding"], [])

    async def test_local_compatible_endpoint_needs_no_remote_credential(self):
        model = self.add(api_base="http://127.0.0.1:11434/v1")
        self.assertTrue(get_provider(model["provider"], model=model).is_available())
        self.assertIn("demo", router.get_available_models(registry.load_registry()))

    async def test_registration_priority_integrates_with_router(self):
        self.add(api_base="http://localhost:1234/v1")
        self.add(key="stronger", model_id="model-2", api_base="http://localhost:1234/v1", priority=8)
        decision = await router.route_async("write a Python function")
        self.assertEqual(decision.selected_model_key, "stronger")

    async def test_failed_registration_probe_is_not_used_by_automatic_routing(self):
        self.add(api_base="http://localhost:1234/v1")
        self.add(key="broken", model_id="bad-model", api_base="http://localhost:1234/v1", priority=9)
        saved = registry.load_registry()
        saved["models"]["broken"]["validation"] = {"status": "failed", "ready": False}
        registry.save_registry(saved)
        self.assertEqual((await router.route_async("write a Python function")).selected_model_key, "demo")
        self.assertEqual((await router.route_async("hello", force_model="broken")).selected_model_key, "broken")

    async def test_real_adapter_probe_uses_registered_endpoint_auth_and_tool_history(self):
        self.add()
        self.env_path.write_text("CUSTOMTEST_API_KEY=connection-test-secret\n")
        requests = []

        def handle(request):
            requests.append(request)
            body = json.loads(request.content)
            if body.get("tools"):
                nonce = int(re.search(r"nonce=(\d+)", body["messages"][0]["content"])[1])
                message = {"content": "", "tool_calls": [{"id": "probe-call", "type": "function", "function": {
                    "name": "asrar_connection_probe", "arguments": json.dumps({"nonce": nonce})}}]}
            else:
                self.assertEqual(body["messages"][-1]["tool_call_id"], "probe-call")
                self.assertNotIn("thought_signature", body["messages"][-2]["tool_calls"][0])
                return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ASRAR_MODEL_OK"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
            return httpx.Response(200, json={"choices": [{"message": message}]})

        transport = httpx.MockTransport(handle)
        real_client = httpx.AsyncClient
        def client(**kwargs):
            kwargs.setdefault("transport", transport)
            return real_client(**kwargs)
        with patch("providers.openai_compat.httpx.AsyncClient", side_effect=client):
            result = await registry.test_model("demo")
        self.assertTrue(result["ready"])
        self.assertEqual(len(requests), 2)
        self.assertEqual(str(requests[0].url), "https://provider.example/v1/chat/completions")
        self.assertEqual(requests[0].headers["authorization"], "Bearer connection-test-secret")
        self.assertEqual(registry.load_registry()["models"]["demo"]["validation"]["status"], "ready")

    async def test_probe_rejects_plain_text_without_required_tool_call(self):
        self.add()
        provider = AsyncMock()
        provider.is_available = lambda: True
        provider.complete.return_value = ProviderResponse("hello", "model-1", "customtest")
        with patch.object(registry, "get_provider", return_value=provider):
            result = await registry.test_model("demo")
        self.assertEqual(result["status"], "tool_calling_unverified")
        self.assertFalse(result["ready"])

    async def test_probe_timeout_and_auth_failure_are_reported(self):
        self.add()
        async def slow(**kwargs):
            await asyncio.sleep(10)
        provider = AsyncMock()
        provider.is_available = lambda: True
        provider.complete.side_effect = slow
        with patch.object(registry, "get_provider", return_value=provider):
            result = await registry.test_model("demo", timeout=0.02)
        self.assertEqual(result["status"], "timeout")
        provider.complete.side_effect = None
        provider.complete.return_value = ProviderResponse("", "model-1", "customtest", success=False, error="Provider HTTP 401: invalid key")
        with patch.object(registry, "get_provider", return_value=provider):
            result = await registry.test_model("demo")
        self.assertIn("401", result["message"])
        self.assertFalse(result["ready"])

    async def test_error_redaction_uses_updated_file_secret(self):
        self.env_path.write_text("CUSTOMTEST_API_KEY=file-only-test-secret\n")
        request = httpx.Request("POST", "https://example.test")
        response = httpx.Response(401, request=request, json={"error": {"message": "bad file-only-test-secret"}})
        error = httpx.HTTPStatusError("bad", request=request, response=response)
        self.assertNotIn("file-only-test-secret", provider_error(error))

    async def test_debug_key_status_uses_current_settings_without_exposing_values(self):
        from api.routes.debug import check_env_keys
        self.env_path.write_text("ANTHROPIC_API_KEY=private-test-key\n")
        status = await check_env_keys()
        self.assertEqual(status["ANTHROPIC_API_KEY"], {"configured": True})
        self.assertNotIn("private-test-key", json.dumps(status))
        self.env_path.write_text("ANTHROPIC_API_KEY=\n")
        self.assertFalse((await check_env_keys())["ANTHROPIC_API_KEY"]["configured"])

    async def test_stream_error_and_early_eof_are_failures_not_empty_success(self):
        async def run(payload):
            real_client = httpx.AsyncClient
            transport = httpx.MockTransport(lambda request: httpx.Response(200, text=payload))
            with patch("providers.openai_compat.httpx.AsyncClient", side_effect=lambda **kwargs: real_client(transport=transport, **kwargs)):
                events = [event async for event in openai_stream(
                    base_url="https://provider.example/v1", headers={}, model_id="model-1",
                    messages=[], system=None, max_tokens=32, tools=None,
                    temperature=None, provider_name="customtest")]
            return events[-1]["response"]
        error = await run('data: {"error":{"message":"generation failed"}}\n\n')
        self.assertFalse(error.success)
        self.assertIn("generation failed", error.error)
        eof = await run('data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')
        self.assertFalse(eof.success)
        self.assertIn("before completion", eof.error)
        usage = await run('data: {"choices":[],"usage":{}}\n\ndata: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
        self.assertTrue(usage.success)
        self.assertEqual(usage.content, "ok")

    async def test_ambiguous_catalog_names_are_not_silently_registered(self):
        real_client = httpx.AsyncClient
        payload = {"data": [{"id": "org/model-a", "name": "Example A"}, {"id": "org/model-b", "name": "Example B"}]}
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
        with patch("core.registry.httpx.AsyncClient", side_effect=lambda **kwargs: real_client(transport=transport, **kwargs)):
            with self.assertRaisesRegex(ValueError, "Ambiguous"):
                await registry.lookup_and_register_model("Example", validate=False)
        self.assertEqual(registry.list_models(), [])

    async def test_preconfigured_together_and_cohere_have_real_compatible_adapters(self):
        for provider, endpoint in (("together", "https://api.together.xyz/v1"),
                                   ("cohere", "https://api.cohere.ai/compatibility/v1")):
            instance = get_provider(provider)
            self.assertEqual(instance.base_url, endpoint)
            self.assertEqual(instance.name, provider)

    async def test_openai_native_requests_use_current_completion_token_parameter(self):
        bodies = []
        def handle(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        real_client = httpx.AsyncClient
        transport = httpx.MockTransport(handle)
        with patch("providers.openai_compat.httpx.AsyncClient", side_effect=lambda **kwargs: real_client(transport=transport, **kwargs)):
            result = await openai_complete(base_url="https://api.openai.com/v1", headers={}, model_id="example-model",
                messages=[], system=None, max_tokens=32, tools=None, temperature=None, provider_name="openai")
        self.assertTrue(result.success)
        self.assertEqual(bodies[0]["max_completion_tokens"], 32)
        self.assertNotIn("max_tokens", bodies[0])

    async def test_agent_tool_registration_and_api_validation_use_the_same_registry(self):
        from fastapi import FastAPI
        from api.routes import models
        app = FastAPI()
        app.include_router(models.router, prefix="/models")
        output = await agent._call_tool("register_model", {"name_or_url": "claude-example"})
        entry = json.loads(output)
        self.assertEqual(entry["validation"]["status"], "missing_credentials")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            listed = await client.get("/models")
            self.assertEqual(listed.json()["models"][0]["key"], entry["key"])
            tested = await client.post(f"/models/{entry['key']}/test")
            self.assertEqual(tested.json()["validation"]["status"], "missing_credentials")
            rejected = await client.post("/models/lookup", json={"name_or_url": "claude-example", "api_key": "must-not-be-accepted"})
            self.assertEqual(rejected.status_code, 422)


if __name__ == "__main__":
    unittest.main()
