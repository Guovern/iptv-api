import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from utils.requests.async_tools import check_ipv6_support_async, fetch_first


class AsyncRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_fetch_uses_configured_proxy_explicitly(self):
        response = AsyncMock()
        response.__aenter__.return_value = response
        response.raise_for_status = Mock()
        response.text.return_value = "Demo,http://example.com/live"
        session = Mock()
        session.get.return_value = response

        with patch(
            "utils.requests.async_tools.config",
            SimpleNamespace(
                http_proxy="http://proxy.example.com:7890",
                request_timeout=10,
            ),
        ), patch("utils.requests.async_tools.asyncio.sleep", new=AsyncMock()):
            await fetch_first(
                session,
                ["https://example.com/list.txt"],
                name="https://example.com/list.txt",
            )

        self.assertEqual(
            session.get.call_args.kwargs["proxy"],
            "http://proxy.example.com:7890",
        )

    async def test_ipv6_check_does_not_use_configured_or_environment_proxy(self):
        response = AsyncMock(status=200)
        response.__aenter__.return_value = response
        session = MagicMock()
        session.__aenter__.return_value = session
        session.get.return_value = response

        with patch(
            "utils.requests.async_tools.config",
            SimpleNamespace(http_proxy="http://proxy.example.com:7890"),
        ), patch(
            "utils.requests.async_tools.ClientSession",
            return_value=session,
        ) as client_session:
            self.assertTrue(await check_ipv6_support_async())

        client_session.assert_called_once_with(trust_env=False)
        self.assertNotIn("proxy", session.get.call_args.kwargs)

    async def test_retry_failure_preserves_root_cause_in_message(self):
        session = Mock()
        session.get.side_effect = TimeoutError("TLS handshake timed out")

        with patch("utils.requests.async_tools.asyncio.sleep", new=AsyncMock()):
            with self.assertRaises(Exception) as raised:
                await fetch_first(
                    session,
                    ["https://example.com/list.m3u"],
                    name="https://example.com/list.m3u",
                )

        self.assertIn("TimeoutError", str(raised.exception))
        self.assertIn("TLS handshake timed out", str(raised.exception))
        self.assertIsInstance(raised.exception.__cause__, TimeoutError)


if __name__ == "__main__":
    unittest.main()
