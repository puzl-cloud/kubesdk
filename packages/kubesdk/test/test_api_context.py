import asyncio
import unittest
from typing import AsyncIterator, Any

from kubesdk._auth import APIContext
from kubesdk.credentials import ConnectionInfo, ServerInfo, ClientInfo


class FakeSession:
    """
    Minimal session-like object.
    Enough so that _context.session.request(...) works without pulling in aiohttp.
    """
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []

    async def request(self, method: str, url: str, **kwargs: Any) -> tuple[str, str]:
        self.requests.append((method, url))
        # just something awaitable
        return method, url


def fake_session_factory() -> FakeSession:
    return FakeSession()


async def fake_async_call(*, _context: APIContext) -> int:
    await _context.session.request("GET", "/ping")
    return 123


async def fake_watch(n: int, *, _context: APIContext) -> AsyncIterator[int]:
    for i in range(n):
        # Simulate a bit of IO every so often
        if i % 1000 == 0:
            await asyncio.sleep(0)
        yield i


class TestAPIContext(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def _make_ctx(self) -> APIContext:
        info = ConnectionInfo(server_info=ServerInfo(server="localhost"), client_info=ClientInfo())
        ctx = APIContext(
            info=info,
            pool_size=1,
            threads=1,
            session_factory=fake_session_factory,
        )
        return ctx

    def test_async_call_basic(self) -> None:
        """
        Non-generator path: async def executed on worker loop.
        """

        async def scenario():
            ctx = self._make_ctx()
            try:
                result = await ctx.call(fake_async_call)
                self.assertEqual(result, 123)

                # Grab the underlying FakeSession and ensure we hit it once.
                # session objects live inside workers: ctx._workers[0]._sessions[0]
                worker = ctx._workers[0]
                session = worker._sessions[0]
                self.assertIsInstance(session, FakeSession)
                self.assertEqual(session.requests, [("GET", "/ping")])
            finally:
                if hasattr(ctx, "close"):
                    ctx.close()

        self._run(scenario())

    def test_watch_generator_basic(self) -> None:
        """
        Generator path: we should see all yielded items in order.
        """
        async def scenario():
            ctx = self._make_ctx()
            try:
                N = 1000
                agen = await ctx.call(fake_watch, N)
                items = [x async for x in agen]
                self.assertEqual(len(items), N)
                self.assertEqual(items[0], 0)
                self.assertEqual(items[-1], N - 1)
            finally:
                if hasattr(ctx, "close"):
                    ctx.close()

        self._run(scenario())
