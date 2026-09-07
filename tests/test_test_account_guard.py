from __future__ import annotations

import unittest
from unittest.mock import patch

from astrbot_plugin_shared_life_context import main


class _Event:
    def get_sender_id(self) -> str:
        return "20002"


class TestAccountGuardTests(unittest.TestCase):
    def test_configured_sender_is_test_account(self) -> None:
        with patch.object(main, "_test_account_ids", return_value={"20002"}):
            self.assertTrue(main._is_test_account(_Event()))


class TestAccountCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_mutation_is_blocked_before_service_access(self) -> None:
        plugin = object.__new__(main.SharedLifeContextPlugin)
        with patch.object(main, "_is_test_account", return_value=True):
            result = await plugin._handle_command(_Event(), "reset")
        self.assertIn("只读沙盒", result)


if __name__ == "__main__":
    unittest.main()
