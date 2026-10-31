"""Subscription startup avoids redundant work without weakening account checks."""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core import claude_bridge
from core.claude_bridge import ClaudeBridge, ClaudeError
from fixtures.claude_sdk import SDK


class ClaudeCatalogStartupTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = ClaudeBridge()
        self.account = {"type": "claude.ai", "email": "first@example.invalid", "plan": "pro"}
        self.patches = [
            patch.object(claude_bridge, "AUTH_HOME", Path(self.temp.name)),
            patch.object(claude_bridge, "sdk_types", return_value=SDK),
            patch.object(claude_bridge, "executable", return_value="fake-claude"),
        ]
        for patcher in self.patches:
            patcher.start()
        SDK.clients = []

    async def asyncTearDown(self):
        await self.bridge.close()
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temp.cleanup()

    async def test_verified_account_skips_duplicate_auth_process(self):
        with patch.object(self.bridge, "require_account", new_callable=AsyncMock) as auth:
            first = await self.bridge.models(account=self.account)
            second = await self.bridge.models(account=self.account)
        auth.assert_not_awaited()
        self.assertEqual(first, second)
        self.assertEqual(first[0]["id"], "claude-test")
        self.assertEqual(len(SDK.clients), 1)
        self.assertTrue(SDK.clients[0].closed)

    async def test_direct_catalog_request_checks_auth_even_with_cached_models(self):
        await self.bridge.models(account=self.account)
        with patch.object(self.bridge, "require_account", new_callable=AsyncMock,
                          side_effect=ClaudeError("Connect your Claude account")) as auth:
            with self.assertRaisesRegex(ClaudeError, "Connect your Claude account"):
                await self.bridge.models()
        auth.assert_awaited_once()
        self.assertEqual(len(SDK.clients), 1)

    async def test_verified_account_cannot_reuse_another_accounts_catalog(self):
        await self.bridge.models(account=self.account)
        other = {**self.account, "email": "second@example.invalid"}
        await self.bridge.models(account=other)
        self.assertEqual(len(SDK.clients), 2)

    async def test_concurrent_catalog_requests_share_sdk_discovery(self):
        results = await asyncio.gather(*[
            self.bridge.models(account=self.account) for _ in range(5)
        ])
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(len(SDK.clients), 1)
